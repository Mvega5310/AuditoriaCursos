"""
Motor de alertas: consulta la base de datos y envia correos a RRHH con los
cursos proximos a vencer segun el tipo de alerta solicitado.

Uso directo (prueba manual o Task Scheduler):
  python scripts/generar_alertas.py diaria
  python scripts/generar_alertas.py semanal
  python scripts/generar_alertas.py quincenal
  python scripts/generar_alertas.py mensual
"""
import sqlite3
import smtplib
import sys
import logging
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import GMAIL_USER, GMAIL_APP_PASSWORD, RRHH_EMAIL, DB_PATH, LOG_PATH, TEMPLATE_PATH, ALERTAS

# Logging a archivo y consola
LOG_PATH.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


def _clasificar(dias: int) -> tuple[str, str]:
    """Devuelve (clase_css, etiqueta_legible) segun los dias restantes."""
    if dias <= 7:
        return "critico", "CRITICO"
    if dias <= 15:
        return "alerta", "ALERTA"
    if dias <= 30:
        return "proximo", "PROXIMO"
    return "normal", "NORMAL"


def consultar_vencimientos(dias_limite: int) -> list[dict]:
    """Retorna lista de cursos que vencen dentro de los proximos dias_limite dias."""
    if not DB_PATH.exists():
        log.error(f"Base de datos no encontrada en {DB_PATH}. Ejecute primero importar_excel.py")
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            e.cedula,
            e.nombre,
            e.apellido,
            e.area,
            e.cargo,
            c.nombre                                                            AS curso,
            ec.fecha_vencimiento,
            CAST(julianday(ec.fecha_vencimiento) - julianday('now') AS INTEGER) AS dias_restantes
        FROM empleado_cursos ec
        JOIN empleados e ON ec.empleado_id = e.id
        JOIN cursos    c ON ec.curso_id    = c.id
        WHERE e.activo = 1
          AND ec.fecha_vencimiento >= date('now')
          AND ec.fecha_vencimiento <= date('now', ?)
        ORDER BY ec.fecha_vencimiento ASC, e.apellido ASC, e.nombre ASC
    """, (f"+{dias_limite} days",))

    registros = []
    for r in cur.fetchall():
        dias = max(int(r["dias_restantes"]), 0)
        clase, label = _clasificar(dias)
        registros.append({
            "cedula":            r["cedula"],
            "nombre":            r["nombre"],
            "apellido":          r["apellido"],
            "area":              r["area"] or "—",
            "cargo":             r["cargo"] or "—",
            "curso":             r["curso"],
            "fecha_vencimiento": datetime.strptime(r["fecha_vencimiento"], "%Y-%m-%d").strftime("%d/%m/%Y"),
            "dias_restantes":    dias,
            "clase":             clase,
            "estado_label":      label,
        })

    conn.close()
    return registros


def _calcular_resumen(registros: list[dict]) -> dict:
    return {
        "critico": sum(1 for r in registros if r["clase"] == "critico"),
        "alerta":  sum(1 for r in registros if r["clase"] == "alerta"),
        "proximo": sum(1 for r in registros if r["clase"] == "proximo"),
        "normal":  sum(1 for r in registros if r["clase"] == "normal"),
    }


def renderizar_html(registros: list[dict], tipo: str) -> str:
    config = ALERTAS[tipo]
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_PATH.parent)))
    template = env.get_template(TEMPLATE_PATH.name)
    return template.render(
        tipo_alerta=config["label"],
        fecha_reporte=date.today().strftime("%d/%m/%Y"),
        registros=registros,
        total=len(registros),
        resumen=_calcular_resumen(registros),
    )


def enviar_email(asunto: str, html_body: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = GMAIL_USER
        msg["To"]      = RRHH_EMAIL
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, [RRHH_EMAIL], msg.as_string())
        return True
    except Exception as e:
        log.error(f"Error al enviar correo: {e}")
        return False


def _registrar_log(tipo: str, total: int, estado: str, detalle: str = "") -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO log_alertas
                (fecha_envio, tipo_alerta, cursos_notificados, destinatario, estado, detalle)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(timespec="seconds"), tipo, total, RRHH_EMAIL, estado, detalle))
        conn.commit()
        conn.close()
    except Exception as e:
        log.error(f"No se pudo registrar en log_alertas: {e}")


def ejecutar_alerta(tipo: str) -> None:
    if tipo not in ALERTAS:
        log.error(f"Tipo invalido: '{tipo}'. Opciones: {list(ALERTAS.keys())}")
        return

    config = ALERTAS[tipo]
    log.info(f"=== Alerta '{tipo}' — ventana {config['dias']} dias ===")

    registros = consultar_vencimientos(config["dias"])

    if not registros:
        log.info(f"Sin vencimientos en los proximos {config['dias']} dias. Correo no enviado.")
        _registrar_log(tipo, 0, "sin_registros")
        return

    log.info(f"Encontrados {len(registros)} registros. Generando y enviando correo a {RRHH_EMAIL}...")

    html = renderizar_html(registros, tipo)
    ok = enviar_email(config["asunto"], html)

    if ok:
        log.info(f"Correo enviado exitosamente con {len(registros)} registros.")
        _registrar_log(tipo, len(registros), "enviado")
    else:
        log.error("Fallo el envio del correo.")
        _registrar_log(tipo, len(registros), "error_envio")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/generar_alertas.py [diaria|semanal|quincenal|mensual]")
        sys.exit(1)
    ejecutar_alerta(sys.argv[1])
