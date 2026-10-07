"""
Lógica de negocio de vencimientos: clasificación por urgencia y consulta de cursos.

Los días restantes se calculan en Python con la fecha local de Colombia, no con
funciones de fecha de la base de datos, para que el resultado sea el mismo en
SQLite y Postgres y no dependa de la hora UTC del servidor.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.engine import Connection

import config
from db import cursos, empleado_cursos, empleados

NIVELES = ("vencido", "critico", "alerta", "proximo", "normal")


def hoy() -> date:
    """Fecha actual en la zona horaria configurada (America/Bogota por defecto)."""
    return datetime.now(ZoneInfo(config.TIMEZONE)).date()


def clasificar(dias: int) -> tuple[str, str]:
    """Devuelve (clase_css, etiqueta) según los días que faltan (negativo = ya venció)."""
    if dias < 0:
        return "vencido", "VENCIDO"
    if dias <= 7:
        return "critico", "CRÍTICO"
    if dias <= 15:
        return "alerta", "ALERTA"
    if dias <= 30:
        return "proximo", "PRÓXIMO"
    return "normal", "NORMAL"


def _texto_dias(dias: int) -> str:
    if dias < 0:
        n = abs(dias)
        return f"Hace {n} día" + ("" if n == 1 else "s")
    if dias == 0:
        return "Hoy"
    return f"{dias} día" + ("" if dias == 1 else "s")


def consultar_vencimientos(
    conn: Connection, dias_ventana: int, fecha_ref: date | None = None
) -> list[dict]:
    """Cursos de empleados activos ya vencidos o que vencen dentro de dias_ventana días."""
    fecha_ref = fecha_ref or hoy()
    limite = fecha_ref + timedelta(days=dias_ventana)
    e, c, ec = empleados.c, cursos.c, empleado_cursos.c

    consulta = (
        select(
            e.cedula, e.nombre, e.apellido, e.area, e.cargo,
            c.nombre.label("curso"), ec.fecha_vencimiento,
        )
        .select_from(
            empleado_cursos.join(empleados, ec.empleado_id == e.id).join(
                cursos, ec.curso_id == c.id
            )
        )
        .where(e.activo.is_(True), ec.fecha_vencimiento <= limite)
        .order_by(ec.fecha_vencimiento, e.apellido, e.nombre)
    )

    registros = []
    for r in conn.execute(consulta):
        dias = (r.fecha_vencimiento - fecha_ref).days
        clase, etiqueta = clasificar(dias)
        registros.append({
            "cedula": r.cedula,
            "nombre": r.nombre,
            "apellido": r.apellido,
            "area": r.area or "—",
            "cargo": r.cargo or "—",
            "curso": r.curso,
            "fecha_vencimiento": r.fecha_vencimiento.strftime("%d/%m/%Y"),
            "dias_restantes": dias,
            "dias_texto": _texto_dias(dias),
            "clase": clase,
            "estado_label": etiqueta,
        })
    return registros


def calcular_resumen(registros: list[dict]) -> dict[str, int]:
    return {nivel: sum(1 for r in registros if r["clase"] == nivel) for nivel in NIVELES}
