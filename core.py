"""
Nucleo compartido: conexion a la base de datos, fechas, estados y utilidades de texto.
"""
import re
import sqlite3
import unicodedata
from datetime import date, datetime
from pathlib import Path

import pytz

from catalogo import CATALOGO
from config import DB_PATH, SCHEMA_PATH, ZONA_HORARIA, UMBRALES, CATEGORIAS

FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y")


def hoy() -> date:
    """Fecha actual en la zona horaria de la empresa (no la del servidor)."""
    return datetime.now(pytz.timezone(ZONA_HORARIA)).date()


def conectar(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def inicializar_db(conn: sqlite3.Connection) -> None:
    """Crea las tablas si no existen y carga el catalogo inicial (sin pisar lo existente)."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _asegurar_columnas(conn)
    conn.executemany(
        """INSERT OR IGNORE INTO tipos_documento
               (sector, categoria, nombre, aplica_a, periodicidad_dias, norma_referencia, nota)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        CATALOGO,
    )
    # Bases creadas antes de existir la columna sector: asigna el sector a los tipos que siguen en 'general'
    conn.executemany(
        "UPDATE tipos_documento SET sector = ? WHERE categoria = ? AND nombre = ? AND sector = 'general'",
        [(c[0], c[1], c[2]) for c in CATALOGO if c[0] != "general"],
    )
    conn.commit()


def _asegurar_columnas(conn: sqlite3.Connection) -> None:
    """Agrega columnas nuevas a bases v2 ya existentes (sin tocar sus datos)."""
    def columnas(tabla):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
    if "avisar_dias" not in columnas("documentos"):
        conn.execute("ALTER TABLE documentos ADD COLUMN avisar_dias INTEGER")
    if "sector" not in columnas("tipos_documento"):
        conn.execute("ALTER TABLE tipos_documento ADD COLUMN sector TEXT NOT NULL DEFAULT 'general'")


def es_v1(conn: sqlite3.Connection) -> bool:
    """True si la base es del esquema anterior (empleado_cursos, sin multi-empresa)."""
    tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return "empleado_cursos" in tablas and "documentos" not in tablas


# ---------------------------------------------------------------- fechas y estados

def parsear_fecha(valor) -> str | None:
    """Convierte fechas de Excel/texto a ISO. Formatos con '/' o '-' se leen dia primero (dd/mm/aaaa)."""
    if valor is None:
        return None
    if isinstance(valor, (datetime, date)):
        try:
            return valor.strftime("%Y-%m-%d")
        except ValueError:      # pandas.NaT
            return None
    texto = str(valor).strip().split(" ")[0].split("T")[0]
    if not texto or texto.lower() in ("nan", "nat", "none"):
        return None
    for fmt in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def dias_restantes(fecha_vencimiento: str, referencia: date | None = None) -> int:
    referencia = referencia or hoy()
    return (date.fromisoformat(fecha_vencimiento) - referencia).days


def clasificar(dias: int) -> tuple[str, str]:
    """Devuelve (clase_css, etiqueta) segun los dias restantes. Negativo = vencido."""
    if dias < 0:
        return "vencido", "VENCIDO"
    if dias <= UMBRALES["critico"]:
        return "critico", "CRITICO"
    if dias <= UMBRALES["alerta"]:
        return "alerta", "ALERTA"
    if dias <= UMBRALES["proximo"]:
        return "proximo", "PROXIMO"
    return "normal", "NORMAL"


# ---------------------------------------------------------------- texto

def normalizar(texto: str) -> str:
    """minusculas, sin tildes, sin guiones bajos ni espacios repetidos."""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.replace("_", " ")).strip().lower()


_ALIAS_CATEGORIA = {
    "examenes medicos": "examen_medico", "examen": "examen_medico",
    "afiliaciones": "afiliacion",
    "documento interno": "documento_empresa", "documentos internos": "documento_empresa",
    "documento": "documento_empresa", "documentos": "documento_empresa",
    "cursos": "curso", "certificaciones": "certificacion", "licencias": "licencia",
}


def resolver_categoria(valor: str | None) -> str | None:
    """Acepta la clave ('examen_medico') o la etiqueta ('Examen médico'). None si no se reconoce."""
    if not valor:
        return None
    n = normalizar(valor)
    for clave, etiqueta in CATEGORIAS.items():
        if n in (normalizar(clave), normalizar(etiqueta)):
            return clave
    return _ALIAS_CATEGORIA.get(n)
