"""
Envío de correo con dos proveedores intercambiables (EMAIL_PROVIDER):

  smtp    Gmail con contraseña de aplicación. No funciona en Railway Free/Trial/Hobby
          porque bloquean el tráfico SMTP saliente.
  resend  API HTTPS de Resend. Funciona en cualquier hosting.

Reintenta automáticamente los fallos transitorios (red, 429, 5xx).
"""
from __future__ import annotations

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


def _remitente() -> str:
    return config.EMAIL_FROM or config.GMAIL_USER


def _enviar_smtp(asunto: str, html: str, texto: str, destinatarios: list[str]) -> None:
    if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        raise MailError("Faltan GMAIL_USER / GMAIL_APP_PASSWORD")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"] = _remitente()
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
            server.sendmail(_remitente(), destinatarios, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise MailError(f"Autenticación SMTP rechazada: {e.smtp_code}") from e
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"Error SMTP: {e}", reintentable=True) from e


def _enviar_resend(asunto: str, html: str, texto: str, destinatarios: list[str]) -> None:
    if not config.RESEND_API_KEY or not config.EMAIL_FROM:
        raise MailError("Faltan RESEND_API_KEY / EMAIL_FROM")
    payload = {
        "from": config.EMAIL_FROM,
        "to": destinatarios,
        "subject": asunto,
        "html": html,
        "text": texto,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config.RESEND_API_KEY}",
            "Content-Type": "application/json",
            "User-Agent": "autcursos/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")[:300]
        raise MailError(
            f"Resend respondió {e.code}: {cuerpo}",
            reintentable=e.code == 429 or e.code >= 500,
        ) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise MailError(f"No se pudo contactar a Resend: {e}", reintentable=True) from e


def enviar_correo(
    asunto: str, html: str, texto: str, destinatarios: list[str] | None = None
) -> None:
    """Envía el correo o lanza MailError. Reintenta solo los fallos transitorios."""
    destinatarios = destinatarios or config.RRHH_EMAILS
    if not destinatarios:
        raise MailError("RRHH_EMAIL no está configurado")

    if config.EMAIL_PROVIDER == "smtp":
        enviar = _enviar_smtp
    elif config.EMAIL_PROVIDER == "resend":
        enviar = _enviar_resend
    else:
        raise MailError(f"EMAIL_PROVIDER no soportado: {config.EMAIL_PROVIDER}")

    for intento in range(1, INTENTOS + 1):
        try:
            enviar(asunto, html, texto, destinatarios)
            return
        except MailError as e:
            if not e.reintentable or intento == INTENTOS:
                raise
            log.warning("Envío fallido (intento %d/%d): %s", intento, INTENTOS, e)
            time.sleep(ESPERA_SEGUNDOS * intento)
