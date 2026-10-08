"""
Envio de correo con dos proveedores:

  resend  API HTTPS de Resend (se usa si hay RESEND_API_KEY y EMAIL_FROM). Funciona en Railway.
  smtp    Gmail con contrasena de aplicacion (desarrollo local). Railway bloquea el SMTP saliente
          en los planes que no son Pro, por eso en produccion se usa Resend.

Reintenta los fallos transitorios (red, 429, 5xx). enviar() devuelve True/False y nunca lanza.
"""
import json
import logging
import smtplib
import time
import urllib.error
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import config

log = logging.getLogger(__name__)

INTENTOS = 3
ESPERA_SEGUNDOS = 2


class MailError(Exception):
    def __init__(self, mensaje: str, reintentable: bool = False):
        super().__init__(mensaje)
        self.reintentable = reintentable


def proveedor() -> str | None:
    """'resend', 'smtp' o None si no hay ninguno configurado."""
    if config.RESEND_API_KEY and config.EMAIL_FROM:
        return "resend"
    if config.GMAIL_USER and config.GMAIL_APP_PASSWORD:
        return "smtp"
    return None


def _enviar_resend(asunto: str, html: str, para: list[str], cco: list[str]) -> None:
    payload = {"from": config.EMAIL_FROM, "to": para, "subject": asunto, "html": html}
    if cco:
        payload["bcc"] = cco
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {config.RESEND_API_KEY}", "Content-Type": "application/json",
                 "User-Agent": "autcursos/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")[:300]
        raise MailError(f"Resend respondio {e.code}: {cuerpo}", reintentable=e.code == 429 or e.code >= 500) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MailError(f"No se pudo contactar a Resend: {e}", reintentable=True) from e


def _enviar_smtp(asunto: str, html: str, para: list[str], cco: list[str]) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = asunto, config.GMAIL_USER, ", ".join(para)
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            server.sendmail(config.GMAIL_USER, para + cco, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"Autenticacion SMTP rechazada ({e.smtp_code})") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Error SMTP: {e}", reintentable=True) from e


def enviar(asunto: str, html: str, para: list[str], copia_oculta: list[str] | None = None) -> bool:
    cco = [c for c in (copia_oculta or []) if c and c not in para]
    prov = proveedor()
    if prov is None:
        log.error("No hay proveedor de correo: defina RESEND_API_KEY + EMAIL_FROM (o GMAIL_USER + GMAIL_APP_PASSWORD).")
        return False
    enviar_con = _enviar_resend if prov == "resend" else _enviar_smtp
    for intento in range(1, INTENTOS + 1):
        try:
            enviar_con(asunto, html, para, cco)
            return True
        except MailError as e:
            if not e.reintentable or intento == INTENTOS:
                log.error("Error al enviar correo (%s): %s", prov, e)
                return False
            log.warning("Envio fallido (intento %d/%d): %s", intento, INTENTOS, e)
            time.sleep(ESPERA_SEGUNDOS * intento)
    return False
