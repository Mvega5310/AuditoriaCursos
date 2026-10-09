"""
Motor de alertas: por cada empresa activa consulta los documentos VENCIDOS y los
proximos a vencer (segun el tipo de alerta) y envia un correo a sus responsables.

Uso directo (prueba manual o Programador de tareas):
  python scripts/generar_alertas.py diaria
  python scripts/generar_alertas.py semanal
  python scripts/generar_alertas.py quincenal
  python scripts/generar_alertas.py mensual

  Agregue --dry-run para NO enviar correos: genera el HTML en la carpeta salidas/
  y permite revisar el resultado antes de activar el envio real.
"""
import smtplib
import sys
import logging
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import (GMAIL_USER, GMAIL_APP_PASSWORD, RRHH_EMAIL, ADMIN_EMAIL, DB_PATH, LOG_PATH,
                    SALIDAS_DIR, TEMPLATE_PATH, ALERTAS, CATEGORIAS, ANTICIPACION_DEFECTO, ventana_alerta)
import core
import correo
from core import conectar, es_v1, hoy, dias_restantes, clasificar, normalizar

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

ESTADOS = ("vencido", "critico", "alerta", "proximo", "normal")


def _nombre(r) -> str:
    return f"{r['apellido']}, {r['nombre']}" if r["apellido"] else r["nombre"]


def consultar_faltantes(conn, empresa_id: int) -> list[dict]:
    """Empleados activos a los que les falta algun documento exigido por su cargo (nunca registrado).
    Un documento registrado sin fecha de vencimiento cuenta como presente."""
    reqs = conn.execute("""
        SELECT r.cargo_clave, t.id AS tipo_id, t.nombre, t.categoria
        FROM requisitos r JOIN tipos_documento t ON t.id = r.tipo_id
        WHERE r.empresa_id = ? ORDER BY t.nombre
    """, (empresa_id,)).fetchall()
    if not reqs:
        return []
    por_cargo: dict[str, list] = {}
    for q in reqs:
        por_cargo.setdefault(q["cargo_clave"], []).append(q)
    tiene: dict[int, set] = {}
    for d in conn.execute("SELECT empleado_id, tipo_id FROM documentos WHERE empresa_id = ? AND empleado_id IS NOT NULL",
                          (empresa_id,)):
        tiene.setdefault(d["empleado_id"], set()).add(d["tipo_id"])

    resultado = []
    for e in conn.execute("""SELECT id, cedula, nombre, apellido, cargo, area FROM empleados
                             WHERE empresa_id = ? AND activo = 1 ORDER BY apellido, nombre""", (empresa_id,)):
        exigidos = {q["tipo_id"]: q for q in por_cargo.get("*", []) + por_cargo.get(normalizar(e["cargo"] or ""), [])}
        faltan = [q["nombre"] for tid, q in exigidos.items() if tid not in tiene.get(e["id"], set())]
        if faltan:
            resultado.append({
                "titular": _nombre(e), "identificacion": e["cedula"], "area": e["area"] or "", "lista": sorted(faltan),
                "area_cargo": " / ".join(x for x in (e["area"], e["cargo"]) if x) or "—",
                "faltan": ", ".join(sorted(faltan)), "cantidad": len(faltan),
            })
    return resultado


def consultar_vencimientos(conn, empresa_id: int, dias_limite: int) -> list[dict]:
    """Documentos de la empresa ya vencidos o que vencen dentro de dias_limite dias.
    Excluye empleados inactivos y documentos sin fecha de vencimiento."""
    hasta = (hoy() + timedelta(days=dias_limite)).isoformat()
    # Un documento con avisar_dias aparece en todas las alertas desde N dias antes de vencer,
    # aunque quede fuera de la ventana del reporte.
    filas = conn.execute("""
        SELECT e.cedula, e.nombre, e.apellido, e.area, e.cargo,
               t.categoria, t.nombre AS documento, d.referencia, d.fecha_vencimiento, d.avisar_dias
        FROM documentos d
        JOIN tipos_documento t ON t.id = d.tipo_id
        LEFT JOIN empleados e  ON e.id = d.empleado_id
        WHERE d.empresa_id = ?
          AND d.fecha_vencimiento IS NOT NULL
          AND (d.fecha_vencimiento <= ? OR d.avisar_dias IS NOT NULL)
          AND (d.empleado_id IS NULL OR e.activo = 1)
        ORDER BY d.fecha_vencimiento ASC, e.apellido ASC, e.nombre ASC
    """, (empresa_id, hasta)).fetchall()

    registros = []
    for r in filas:
        dias = dias_restantes(r["fecha_vencimiento"])
        if r["fecha_vencimiento"] > hasta and not (r["avisar_dias"] is not None and dias <= r["avisar_dias"]):
            continue        # fuera de la ventana y todavia sin entrar en su aviso propio
        clase, label = clasificar(dias)
        es_empresa = r["cedula"] is None
        documento = r["documento"] + (f" ({r['referencia']})" if r["referencia"] else "")
        registros.append({
            "tipo":              r["documento"],
            "area":              "" if es_empresa else (r["area"] or ""),
            "titular":           "EMPRESA" if es_empresa else _nombre(r),
            "identificacion":    "—" if es_empresa else r["cedula"],
            "area_cargo":        "—" if es_empresa else (" / ".join(x for x in (r["area"], r["cargo"]) if x) or "—"),
            "categoria":         CATEGORIAS.get(r["categoria"], r["categoria"]),
            "documento":         documento,
            "fecha_vencimiento": datetime.strptime(r["fecha_vencimiento"], "%Y-%m-%d").strftime("%d/%m/%Y"),
            "dias":              dias,
            "dias_texto":        f"hace {-dias}" if dias < 0 else str(dias),
            "clase":             clase,
            "estado_label":      label,
        })
    return registros


def _calcular_resumen(registros: list[dict]) -> dict:
    return {estado: sum(1 for r in registros if r["clase"] == estado) for estado in ESTADOS}


def configuracion_empresa(conn, empresa_id: int) -> dict:
    """Anticipacion (dias) y alertas que recibe la empresa."""
    r = conn.execute("SELECT anticipacion_dias, alertas_activas FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    anticipacion = r["anticipacion_dias"] if r and r["anticipacion_dias"] is not None else ANTICIPACION_DEFECTO
    texto = r["alertas_activas"] if r and r["alertas_activas"] is not None else ",".join(ALERTAS)   # "" = ninguna
    activas = {x.strip() for x in texto.split(",") if x.strip()}
    return {"anticipacion": anticipacion, "activas": activas,
            "ventanas": {t: ventana_alerta(t, anticipacion) for t in ALERTAS}}


def renderizar_html(registros: list[dict], tipo: str, empresa: str, faltantes: list[dict] | None = None,
                    dias: int | None = None) -> str:
    config = ALERTAS[tipo]
    dias = dias if dias is not None else ventana_alerta(tipo)
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_PATH.parent)), autoescape=True)
    template = env.get_template(TEMPLATE_PATH.name)
    return template.render(
        empresa=empresa,
        tipo_alerta=config["label"].format(dias=dias),
        fecha_reporte=hoy().strftime("%d/%m/%Y"),
        registros=registros,
        total=len(registros),
        faltantes=faltantes or [],
        total_faltantes=sum(f["cantidad"] for f in (faltantes or [])),
        resumen=_calcular_resumen(registros),
    )


def obtener_destinatarios(conn, empresa_id: int) -> list[str]:
    """Responsables activos de la empresa; si no tiene, el correo de respaldo (RRHH_EMAIL)."""
    emails = [r["email"] for r in conn.execute(
        "SELECT email FROM responsables WHERE empresa_id = ? AND activo = 1 ORDER BY id", (empresa_id,))]
    if not emails and RRHH_EMAIL:
        emails = [RRHH_EMAIL]
    return emails


def enviar_email(asunto: str, html_body: str, para: list[str], copia_oculta: list[str] | None = None) -> bool:
    return correo.enviar(asunto, html_body, para, copia_oculta)


def _registrar_log(conn, tipo: str, empresa_id: int, total: int, destinatario: str,
                   estado: str, detalle: str = "") -> None:
    try:
        conn.execute("""
            INSERT INTO log_alertas
                (fecha_envio, tipo_alerta, empresa_id, documentos_notificados, destinatario, estado, detalle)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(timespec="seconds"), tipo, empresa_id, total, destinatario, estado, detalle))
        conn.commit()
    except Exception as e:
        log.error(f"No se pudo registrar en log_alertas: {e}")


def _slug(texto: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in texto).strip("_").lower() or "empresa"


def ejecutar_alerta(tipo: str, dry_run: bool = False, db_path: Path | str = DB_PATH) -> None:
    if tipo not in ALERTAS:
        log.error(f"Tipo invalido: '{tipo}'. Opciones: {list(ALERTAS.keys())}")
        return

    config = ALERTAS[tipo]
    if not core.DATABASE_URL and not Path(db_path).exists():
        log.error(f"Base de datos no encontrada en {db_path}. Ejecute primero importar_excel.py")
        return

    conn = conectar(db_path)
    try:
        if es_v1(conn):
            log.error("La base de datos es del esquema anterior. Ejecute primero: python scripts/migrar_v1.py")
            return

        log.info(f"=== Alerta '{tipo}' — vencidos + ventana = {config['periodo']} dias de periodo + anticipacion de "
                 f"cada empresa{' [DRY-RUN]' if dry_run else ''} ===")
        empresas = conn.execute("SELECT id, nombre FROM empresas WHERE activa = 1 ORDER BY nombre").fetchall()
        if not empresas:
            log.warning("No hay empresas registradas.")
            return

        for emp in empresas:
            conf = configuracion_empresa(conn, emp["id"])
            if tipo not in conf["activas"]:
                log.info(f"[{emp['nombre']}] La empresa no recibe la alerta '{tipo}'. Omitida.")
                continue
            dias = conf["ventanas"][tipo]
            registros = consultar_vencimientos(conn, emp["id"], dias)
            faltantes = consultar_faltantes(conn, emp["id"]) if config.get("faltantes") else []
            n_total = len(registros) + sum(f["cantidad"] for f in faltantes)
            if not registros and not faltantes:
                log.info(f"[{emp['nombre']}] Sin documentos vencidos, por vencer ni faltantes. Correo no enviado.")
                if not dry_run:
                    _registrar_log(conn, tipo, emp["id"], 0, "", "sin_registros")
                continue

            html = renderizar_html(registros, tipo, emp["nombre"], faltantes, dias)
            asunto = f"{config['asunto'].format(dias=dias)} — {emp['nombre']}"

            if dry_run:
                SALIDAS_DIR.mkdir(exist_ok=True)
                destino = SALIDAS_DIR / f"alerta_{tipo}_{_slug(emp['nombre'])}.html"
                destino.write_text(html, encoding="utf-8")
                log.info(f"[{emp['nombre']}] {len(registros)} registros, {len(faltantes)} personas con faltantes -> {destino.name} (no se envio correo)")
                continue

            para = obtener_destinatarios(conn, emp["id"])
            if not para:
                log.error(f"[{emp['nombre']}] Sin destinatarios (agregue responsables o RRHH_EMAIL en .env).")
                _registrar_log(conn, tipo, emp["id"], n_total, "", "sin_destinatario")
                continue

            log.info(f"[{emp['nombre']}] {len(registros)} registros, {len(faltantes)} personas con faltantes. Enviando a {', '.join(para)}...")
            ok = enviar_email(asunto, html, para, [ADMIN_EMAIL] if ADMIN_EMAIL else None)
            _registrar_log(conn, tipo, emp["id"], n_total, ", ".join(para),
                           "enviado" if ok else "error_envio")
            if ok:
                log.info(f"[{emp['nombre']}] Correo enviado.")
            else:
                log.error(f"[{emp['nombre']}] Fallo el envio del correo.")
    finally:
        conn.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    if not args:
        print("Uso: python scripts/generar_alertas.py [diaria|semanal|quincenal|mensual] [--dry-run]")
        sys.exit(1)
    ejecutar_alerta(args[0], dry_run="--dry-run" in sys.argv)
