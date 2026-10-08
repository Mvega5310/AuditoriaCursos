"""
Sincronizacion con Google Sheets (solo lectura).

El cliente comparte su hoja con el correo de la cuenta de servicio (rol LECTOR) y pega el enlace en la app.
Cada dia (y con el boton "Sincronizar ahora") se lee la hoja y se procesa con el MISMO importador del Excel:
actualiza sin duplicar, valida fila por fila y NO borra nada (borrar una fila del Sheet no borra el documento).

Las fechas se leen por su valor real (no por el texto en pantalla), asi que no dependen del idioma de la hoja.
"""
import base64
import json
import logging
import re
from datetime import date, datetime, timedelta
from functools import lru_cache

import pandas as pd

import config
from core import conectar, inicializar_db

log = logging.getLogger(__name__)

API = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_RE_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_RE_ID_SOLO = re.compile(r"^[A-Za-z0-9_-]{25,}$")
MAX_COLUMNAS = 80
ESPERA_MANUAL_SEG = 60


class SheetsError(Exception):
    """Error con un mensaje apto para mostrar al usuario."""


# ------------------------------------------------------------------ credenciales
@lru_cache(maxsize=4)
def _credenciales(crudo: str | None) -> dict | None:
    if not crudo:
        return None
    texto = crudo.strip()
    try:
        if not texto.startswith("{"):
            texto = base64.b64decode(texto).decode("utf-8")
        info = json.loads(texto)
    except Exception:
        log.error("GOOGLE_SERVICE_ACCOUNT_JSON no es un JSON valido (ni base64 de uno).")
        return None
    return info if info.get("client_email") and info.get("private_key") else None


def credenciales() -> dict | None:
    return _credenciales(config.GOOGLE_SERVICE_ACCOUNT_JSON)


def configurado() -> bool:
    return credenciales() is not None


def email_cuenta_servicio() -> str | None:
    info = credenciales()
    return info["client_email"] if info else None


# ------------------------------------------------------------------ enlaces
def extraer_id(url_o_id: str | None) -> str | None:
    """ID de la hoja a partir de un enlace de Google Sheets (o del ID solo). None si no parece uno."""
    texto = (url_o_id or "").strip()
    m = _RE_ID.search(texto)
    if m and "docs.google.com" in texto:
        return m.group(1)
    return texto if _RE_ID_SOLO.match(texto) else None


# ------------------------------------------------------------------ lectura
def _celda(c: dict) -> str | None:
    """Valor real de una celda. Las fechas llegan como numero de serie: se convierten a ISO segun el FORMATO de la
    celda, no segun lo que se ve en pantalla (que depende del idioma de la hoja)."""
    ev = c.get("effectiveValue") or {}
    if "numberValue" in ev:
        n = float(ev["numberValue"])
        tipo = ((c.get("effectiveFormat") or {}).get("numberFormat") or {}).get("type", "")
        if tipo in ("DATE", "DATE_TIME"):
            return (date(1899, 12, 30) + timedelta(days=int(n))).isoformat()
        return str(int(n)) if n.is_integer() else repr(n)
    if "stringValue" in ev:
        return ev["stringValue"]
    if "boolValue" in ev:
        return "TRUE" if ev["boolValue"] else "FALSE"
    return None


def hojas_desde_respuesta(datos: dict) -> dict[str, pd.DataFrame]:
    """Respuesta de la API (includeGridData) -> {nombre de hoja: DataFrame sin encabezado}, como lee pandas un Excel."""
    hojas = {}
    for hoja in datos.get("sheets", []):
        titulo = hoja["properties"]["title"]
        filas = []
        for bloque in hoja.get("data", []):
            for fila in bloque.get("rowData", [])[: config.SHEETS_MAX_FILAS]:
                filas.append([_celda(c) for c in fila.get("values", [])[:MAX_COLUMNAS]])
        ancho = max((len(f) for f in filas), default=0)
        hojas[titulo] = pd.DataFrame([f + [None] * (ancho - len(f)) for f in filas], dtype=object)
    return hojas


def _sesion():
    info = credenciales()
    if not info:
        raise SheetsError("La sincronización con Google Sheets aún no está activada en el sistema. "
                          "Contacte al administrador.")
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    return AuthorizedSession(service_account.Credentials.from_service_account_info(info, scopes=[SCOPE]))


def leer_hojas(sheet_id: str, sesion=None) -> dict[str, pd.DataFrame]:
    """Lee todas las hojas de un Google Sheet compartido con la cuenta de servicio."""
    import requests
    try:
        sesion = sesion or _sesion()
        r = sesion.get(f"{API}/{sheet_id}", timeout=45, params={
            "includeGridData": "true",
            "fields": "sheets(properties.title,data.rowData.values(effectiveValue,effectiveFormat.numberFormat.type))"})
    except SheetsError:
        raise
    except (requests.ConnectionError, requests.Timeout) as e:
        raise SheetsError("No se pudo conectar con Google. Se reintentará en la próxima sincronización.") from e
    if r.status_code in (403, 404):
        raise SheetsError("No tengo acceso a esa hoja. Compártala (rol Lector) con "
                          f"{email_cuenta_servicio() or 'la cuenta de servicio'} y vuelva a intentar.")
    if r.status_code == 400:
        raise SheetsError("Ese enlace no es una hoja de Google Sheets (¿es un Excel subido a Drive? Ábralo y use "
                          "Archivo > Guardar como hoja de cálculo de Google).")
    if r.status_code == 429:
        raise SheetsError("Google limitó las consultas; se reintentará en la próxima sincronización.")
    if r.status_code != 200:
        log.error("Sheets API respondio %s: %s", r.status_code, r.text[:200])
        raise SheetsError(f"Google respondió con un error ({r.status_code}).")
    return hojas_desde_respuesta(r.json())


# ------------------------------------------------------------------ sincronizacion
def sincronizar_empresa(db_path, empresa_id: int, lector=None) -> dict:
    """Lee el Sheet de la empresa, lo importa y guarda el resultado. Nunca lanza: devuelve {'estado', 'detalle', ...}.
    lector(sheet_id) -> hojas; se puede inyectar para pruebas."""
    from scripts.importar_excel import importar

    conn = conectar(db_path)
    try:
        inicializar_db(conn)
        e = conn.execute("SELECT nombre, sheet_url, sheet_fechas FROM empresas WHERE id = ? AND activa = 1",
                         (empresa_id,)).fetchone()
    finally:
        conn.close()
    if not e or not e["sheet_url"]:
        return {"estado": "sin_hoja", "detalle": ""}

    stats, detalle = None, ""
    try:
        sheet_id = extraer_id(e["sheet_url"])
        if not sheet_id:
            raise SheetsError("El enlace guardado no es de Google Sheets. Vuelva a pegarlo.")
        hojas = (lector or leer_hojas)(sheet_id)
        stats = importar(None, db_path=db_path, verbose=False, hojas_crudas=hojas, empresa_forzada=e["nombre"],
                         fechas="vencimiento" if e["sheet_fechas"] == "vencimiento" else "realizacion")
        estado = (f"ok: {stats['nuevos']} nuevos, {stats['actualizados']} actualizados"
                  + (f", {len(stats['errores'])} filas con error" if stats["errores"] else ""))
        detalle = "\n".join(stats["errores"][:30])
    except SheetsError as exc:
        estado = f"error: {exc}"
    except SystemExit as exc:          # el importador explica que columnas faltan
        estado = "error: " + re.sub(r"^Error:\s*", "", str(exc))[:400]
    except Exception:
        log.exception("Sincronizacion fallida (empresa %s)", empresa_id)
        estado = "error: no se pudo sincronizar. El administrador ya fue notificado en el registro."

    conn = conectar(db_path)
    try:
        conn.execute("UPDATE empresas SET sheet_sync_en = ?, sheet_sync_estado = ?, sheet_sync_detalle = ? WHERE id = ?",
                     (datetime.now().isoformat(timespec="seconds"), estado, detalle, empresa_id))
        conn.commit()
    finally:
        conn.close()
    log.info("[empresa %s] Sheets: %s", empresa_id, estado)
    return {"estado": estado, "detalle": detalle, "stats": stats}


def sincronizar_todas(db_path=config.DB_PATH, lector=None) -> int:
    """Sincroniza todas las empresas con Sheet. Una que falle no detiene a las demas. Devuelve cuantas se procesaron."""
    conn = conectar(db_path)
    try:
        inicializar_db(conn)
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM empresas WHERE activa = 1 AND sheet_url IS NOT NULL AND sheet_url <> '' ORDER BY id")]
    finally:
        conn.close()
    for i in ids:
        sincronizar_empresa(db_path, i, lector)
    log.info("Sincronizacion con Google Sheets: %s empresa(s).", len(ids))
    return len(ids)
