"""
Nucleo compartido: conexion a la base de datos, fechas, estados y utilidades de texto.
"""
import re
import sqlite3
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import pytz

from catalogo import CATALOGO
import config
from config import DB_PATH, SCHEMA_PATH, ZONA_HORARIA, UMBRALES, CATEGORIAS

FORMATOS_FECHA = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%y", "%d-%m-%y")


def hoy() -> date:
    """Fecha actual en la zona horaria de la empresa (no la del servidor)."""
    return datetime.now(pytz.timezone(ZONA_HORARIA)).date()


# ---------------------------------------------------------------- base de datos (SQLite local | Postgres)
# El resto del codigo escribe SQL estilo SQLite (marcador '?'); para Postgres se traduce aqui.
DATABASE_URL = config.DATABASE_URL


class Fila(tuple):
    """Fila que se lee por posicion (r[0]) o por nombre (r['x']), como sqlite3.Row."""
    def __new__(cls, valores, claves):
        obj = super().__new__(cls, valores)
        obj._claves = {k: i for i, k in enumerate(claves)}
        return obj

    def __getitem__(self, i):
        return super().__getitem__(self._claves[i] if isinstance(i, str) else i)

    def keys(self):
        return list(self._claves)


def _fabrica_filas(cursor):
    claves = [d.name for d in cursor.description or []]
    return lambda valores: Fila(valores, claves)


def _traducir(sql: str, devolver_id: bool = True) -> str:
    """SQLite -> Postgres: ? -> %s, INSERT OR IGNORE -> ON CONFLICT DO NOTHING y RETURNING id (para lastrowid)."""
    sql = sql.replace("%", "%%").replace("?", "%s").strip().rstrip(";")
    if re.match(r"(?i)INSERT\s+OR\s+IGNORE\s+INTO", sql):
        sql = re.sub(r"(?i)INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1) + " ON CONFLICT DO NOTHING"
    if devolver_id and re.match(r"(?i)INSERT\s", sql) and "RETURNING" not in sql.upper():
        sql += " RETURNING id"
    return sql


class _Cursor:
    def __init__(self, raw):
        self._c = raw.cursor(row_factory=_fabrica_filas)
        self.lastrowid = None
        self.rowcount = -1

    def execute(self, sql, params=()):
        pg = _traducir(sql)
        self._c.execute(pg, tuple(params))
        self.rowcount = self._c.rowcount
        self.lastrowid = None
        if pg.endswith("RETURNING id") and self._c.description:
            fila = self._c.fetchone()
            self.lastrowid = fila[0] if fila else None
        return self

    def executemany(self, sql, secuencia):
        self._c.executemany(_traducir(sql, devolver_id=False), [tuple(p) for p in secuencia])
        return self

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c.fetchall())


class ConexionPG:
    """Envoltorio de psycopg con la misma interfaz que usa el codigo con sqlite3."""
    es_pg = True

    def __init__(self, url: str):
        import psycopg
        self._raw = psycopg.connect(url)

    def cursor(self):
        return _Cursor(self._raw)

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def executemany(self, sql, secuencia):
        return self.cursor().executemany(sql, secuencia)

    def executescript(self, script: str):
        self._raw.execute(script)          # sin parametros: admite varias sentencias

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, tipo, _valor, _tb):
        (self.rollback if tipo else self.commit)()
        return False


def es_postgres(conn) -> bool:
    return getattr(conn, "es_pg", False)


def conectar(db_path: Path | str = DB_PATH):
    """Postgres si hay DATABASE_URL; si no, el archivo SQLite db_path."""
    if DATABASE_URL:
        return ConexionPG(DATABASE_URL)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_TRIGGER_PG = """
CREATE OR REPLACE FUNCTION fn_documentos_historial() RETURNS trigger AS $$
BEGIN
    INSERT INTO documentos_historial (documento_id, fecha_emision, fecha_vencimiento, archivo, registrado_en)
    VALUES (OLD.id, OLD.fecha_emision, OLD.fecha_vencimiento, OLD.archivo, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'));
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_documentos_historial ON documentos;
CREATE TRIGGER trg_documentos_historial BEFORE UPDATE OF fecha_vencimiento ON documentos
    FOR EACH ROW WHEN (OLD.fecha_vencimiento IS DISTINCT FROM NEW.fecha_vencimiento)
    EXECUTE FUNCTION fn_documentos_historial();
"""


def _esquema_pg(sqlite_sql: str) -> str:
    """Deriva el esquema de Postgres del de SQLite (una sola fuente de verdad)."""
    sql = re.sub(r"CREATE TRIGGER.*?\nEND;", "", sqlite_sql, flags=re.S)
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    return sql + _TRIGGER_PG


def inicializar_db(conn) -> None:
    """Crea las tablas si no existen y carga el catalogo inicial (sin pisar lo existente)."""
    esquema = SCHEMA_PATH.read_text(encoding="utf-8")
    if es_postgres(conn):
        esquema = _esquema_pg(esquema)
    conn.executescript(esquema)
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


def _asegurar_columnas(conn) -> None:
    """Agrega columnas nuevas a bases ya existentes (sin tocar sus datos)."""
    def columnas(tabla):
        if es_postgres(conn):
            return {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?", (tabla,))}
        return {r[1] for r in conn.execute(f"PRAGMA table_info({tabla})")}
    if "avisar_dias" not in columnas("documentos"):
        conn.execute("ALTER TABLE documentos ADD COLUMN avisar_dias INTEGER")
    if "consentimiento_en" not in columnas("empresas"):
        conn.execute("ALTER TABLE empresas ADD COLUMN consentimiento_en TEXT")
    cols_empresa = columnas("empresas")
    for col, tipo in (("sheet_url", "TEXT"), ("sheet_fechas", "TEXT DEFAULT 'realizacion'"), ("sheet_sync_en", "TEXT"),
                      ("sheet_sync_estado", "TEXT"), ("sheet_sync_detalle", "TEXT")):
        if col not in cols_empresa:
            conn.execute(f"ALTER TABLE empresas ADD COLUMN {col} {tipo}")
    if "sector" not in columnas("tipos_documento"):
        conn.execute("ALTER TABLE tipos_documento ADD COLUMN sector TEXT NOT NULL DEFAULT 'general'")


def es_v1(conn) -> bool:
    """True si la base es del esquema anterior (empleado_cursos, sin multi-empresa). Solo existe en SQLite."""
    if es_postgres(conn):
        return False
    tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return "empleado_cursos" in tablas and "documentos" not in tablas


# ---------------------------------------------------------------- fechas y estados

_MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
          "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
_RE_FECHA_TEXTO = re.compile(r"^(\d{1,2})[\s\-/.]*(?:de\s+)?([a-z]{3,10})\.?[\s\-/.]*(?:de\s+)?(\d{2,4})$")


def parsear_fecha(valor, serial: bool = True) -> str | None:
    """Convierte fechas de Excel/texto a ISO. Formatos con '/' o '-' se leen dia primero (dd/mm/aaaa).
    Tambien entiende '15-mar-26', '15 de marzo de 2026' y, si serial=True, el numero de serie de Excel
    (celda con fecha pero formato General, p. ej. 46100). serial=False se usa para DETECTAR columnas de fechas,
    donde un numero cualquiera (valor, consecutivo) no debe confundirse con una fecha."""
    if valor is None:
        return None
    if isinstance(valor, (datetime, date)):
        try:
            return valor.strftime("%Y-%m-%d")
        except ValueError:      # pandas.NaT
            return None
    completo = str(valor).strip()
    texto = completo.split(" ")[0].split("T")[0]
    if not texto or texto.lower() in ("nan", "nat", "none"):
        return None
    for fmt in FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if serial and re.fullmatch(r"\d{5}(\.\d+)?", completo) and 25569 <= float(completo) < 80000:
        return (date(1899, 12, 30) + timedelta(days=int(float(completo)))).isoformat()
    m = _RE_FECHA_TEXTO.match(normalizar(completo))
    if m and m.group(2)[:3] in _MESES:
        anio = int(m.group(3))
        anio = anio + (2000 if anio < 70 else 1900) if anio < 100 else anio
        try:
            return date(anio, _MESES[m.group(2)[:3]], int(m.group(1))).isoformat()
        except ValueError:
            return None
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
