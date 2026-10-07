"""
Capa de datos de autCursos.

Una sola definición de tablas que funciona en SQLite (desarrollo) y en Postgres
(producción: Supabase, Railway...). La conexión se elige con DATABASE_URL.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    case,
    create_engine,
    event,
    func,
    inspect,
    select,
    text,
    true,
)
from sqlalchemy.engine import Connection, Engine

import config

log = logging.getLogger(__name__)

metadata = MetaData()

empleados = Table(
    "empleados",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("cedula", Text, nullable=False, unique=True),
    Column("nombre", Text, nullable=False),
    Column("apellido", Text, nullable=False),
    Column("cargo", Text, server_default=""),
    Column("area", Text, server_default=""),
    Column("activo", Boolean, nullable=False, server_default=true()),
    Column("fecha_registro", DateTime(timezone=True), server_default=func.now()),
)

cursos = Table(
    "cursos",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nombre", Text, nullable=False, unique=True),
    Column("descripcion", Text, server_default=""),
    Column("periodicidad_dias", Integer, server_default="365"),
)

empleado_cursos = Table(
    "empleado_cursos",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("empleado_id", Integer, ForeignKey("empleados.id"), nullable=False),
    Column("curso_id", Integer, ForeignKey("cursos.id"), nullable=False),
    Column("fecha_realizacion", Date),
    Column("fecha_vencimiento", Date, nullable=False),
    Column("fecha_registro", DateTime(timezone=True), server_default=func.now()),
    Column("fecha_actualizacion", DateTime(timezone=True), server_default=func.now()),
    UniqueConstraint("empleado_id", "curso_id"),
    Index("ix_empleado_cursos_vencimiento", "fecha_vencimiento"),
)

# Auditoría: cada correo (o intento) del motor de alertas queda aquí
log_alertas = Table(
    "log_alertas",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("fecha_envio", DateTime(timezone=True), nullable=False),
    Column("tipo_alerta", Text, nullable=False),
    Column("cursos_notificados", Integer, server_default="0"),
    Column("destinatario", Text, nullable=False),
    Column("estado", Text, nullable=False),  # enviado | error_envio | sin_registros
    Column("detalle", Text, server_default=""),
)

# Columnas que bases creadas con versiones anteriores del esquema pueden no tener
_COLUMNAS_NUEVAS = {
    "empleados": ["fecha_registro"],
    "empleado_cursos": ["fecha_registro", "fecha_actualizacion"],
}

_engine: Engine | None = None


def crear_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":
        # SQLite no aplica llaves foráneas salvo que se le pida
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = crear_engine(config.DATABASE_URL)
    return _engine


def init_db(engine: Engine | None = None) -> None:
    """Crea las tablas que falten y añade columnas nuevas a bases antiguas. Es idempotente."""
    engine = engine or get_engine()
    if engine.dialect.name == "sqlite" and engine.url.database:
        Path(engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    metadata.create_all(engine)
    _asegurar_columnas(engine)


def _asegurar_columnas(engine: Engine) -> None:
    tipo = "TIMESTAMPTZ" if engine.dialect.name == "postgresql" else "TIMESTAMP"
    insp = inspect(engine)
    for tabla, columnas in _COLUMNAS_NUEVAS.items():
        existentes = {c["name"] for c in insp.get_columns(tabla)}
        for columna in columnas:
            if columna not in existentes:
                log.info("Migrando esquema: %s.%s", tabla, columna)
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}"))


def _insert(conn: Connection, tabla: Table):
    """INSERT con soporte de ON CONFLICT según el motor."""
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(tabla)


# --- Operaciones de escritura -------------------------------------------------

def upsert_empleado(
    conn: Connection, *, cedula: str, nombre: str, apellido: str, cargo: str, area: str
) -> int:
    """Crea o actualiza un empleado. Un valor vacío nunca borra uno existente."""
    existe = conn.scalar(select(empleados.c.id).where(empleados.c.cedula == cedula))
    if existe is None and (not nombre or not apellido):
        raise ValueError("Nombre y apellido son obligatorios para registrar un empleado nuevo.")

    ins = _insert(conn, empleados).values(
        cedula=cedula, nombre=nombre, apellido=apellido, cargo=cargo, area=area
    )
    ex = ins.excluded

    def conservar(columna):
        return case((ex[columna.name] != "", ex[columna.name]), else_=columna)

    ins = ins.on_conflict_do_update(
        index_elements=[empleados.c.cedula],
        set_={
            "nombre": conservar(empleados.c.nombre),
            "apellido": conservar(empleados.c.apellido),
            "cargo": conservar(empleados.c.cargo),
            "area": conservar(empleados.c.area),
        },
    )
    conn.execute(ins)
    return conn.scalar(select(empleados.c.id).where(empleados.c.cedula == cedula))


def upsert_curso(conn: Connection, nombre: str) -> tuple[int, int]:
    """Devuelve (curso_id, periodicidad_dias), creando el curso si no existe."""
    conn.execute(
        _insert(conn, cursos).values(nombre=nombre).on_conflict_do_nothing(
            index_elements=[cursos.c.nombre]
        )
    )
    fila = conn.execute(
        select(cursos.c.id, cursos.c.periodicidad_dias).where(cursos.c.nombre == nombre)
    ).one()
    return fila.id, fila.periodicidad_dias or 365


def upsert_asignacion(
    conn: Connection,
    *,
    empleado_id: int,
    curso_id: int,
    fecha_realizacion: date | None,
    fecha_vencimiento: date,
) -> str:
    """Crea o renueva la asignación empleado-curso. Devuelve 'nuevo' o 'actualizado'."""
    ec = empleado_cursos
    existe = conn.scalar(
        select(ec.c.id).where(ec.c.empleado_id == empleado_id, ec.c.curso_id == curso_id)
    )
    ins = _insert(conn, ec).values(
        empleado_id=empleado_id,
        curso_id=curso_id,
        fecha_realizacion=fecha_realizacion,
        fecha_vencimiento=fecha_vencimiento,
    )
    ex = ins.excluded
    ins = ins.on_conflict_do_update(
        index_elements=[ec.c.empleado_id, ec.c.curso_id],
        set_={
            "fecha_realizacion": func.coalesce(ex.fecha_realizacion, ec.c.fecha_realizacion),
            "fecha_vencimiento": ex.fecha_vencimiento,
            "fecha_actualizacion": func.now(),
        },
    )
    conn.execute(ins)
    return "actualizado" if existe is not None else "nuevo"


def registrar_log(
    conn: Connection, *, tipo: str, total: int, destinatario: str, estado: str, detalle: str = ""
) -> None:
    conn.execute(
        log_alertas.insert().values(
            fecha_envio=datetime.now(timezone.utc),
            tipo_alerta=tipo,
            cursos_notificados=total,
            destinatario=destinatario or "(sin configurar)",
            estado=estado,
            detalle=detalle[:1000],
        )
    )
