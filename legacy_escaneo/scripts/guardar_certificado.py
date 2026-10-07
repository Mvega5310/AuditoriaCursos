"""
Guarda en la base de datos un registro de curso ya confirmado por RRHH
(proveniente del escaneo de un certificado).

Uso:
  from scripts.guardar_certificado import guardar_registro
  guardar_registro(cedula, nombre, apellido, cargo, area, curso,
                    fecha_realizacion, fecha_vencimiento)
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import DB_PATH


def _calcular_vencimiento(fecha_realizacion: str, periodicidad_dias: int) -> str:
    fecha = date.fromisoformat(fecha_realizacion)
    return (fecha + timedelta(days=periodicidad_dias)).isoformat()


def guardar_registro(
    cedula: str,
    nombre: str,
    apellido: str,
    cargo: str,
    area: str,
    curso: str,
    fecha_realizacion: Optional[str],
    fecha_vencimiento: Optional[str],
) -> None:
    """Inserta o actualiza empleado, curso y la asignacion empleado-curso.

    Si no llega fecha_vencimiento pero si fecha_realizacion, la calcula con la
    periodicidad_dias del curso (365 por defecto, igual que importar_excel.py).
    """
    if not cedula or not curso:
        raise ValueError("cedula y curso son obligatorios")
    if not fecha_vencimiento and not fecha_realizacion:
        raise ValueError("se necesita fecha_realizacion o fecha_vencimiento")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Upsert empleado: cargo/area solo se sobreescriben si vienen con valor,
    # para no borrar datos existentes cuando el certificado no los trae.
    cur.execute("""
        INSERT INTO empleados (cedula, nombre, apellido, cargo, area)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(cedula) DO UPDATE SET
            nombre   = excluded.nombre,
            apellido = excluded.apellido,
            cargo    = CASE WHEN excluded.cargo != '' THEN excluded.cargo ELSE empleados.cargo END,
            area     = CASE WHEN excluded.area  != '' THEN excluded.area  ELSE empleados.area  END
    """, (cedula, nombre or "", apellido or "", cargo or "", area or ""))

    empleado_id = cur.execute(
        "SELECT id FROM empleados WHERE cedula = ?", (cedula,)
    ).fetchone()[0]

    cur.execute("INSERT OR IGNORE INTO cursos (nombre) VALUES (?)", (curso,))
    curso_row = cur.execute(
        "SELECT id, periodicidad_dias FROM cursos WHERE nombre = ?", (curso,)
    ).fetchone()
    curso_id, periodicidad_dias = curso_row

    if not fecha_vencimiento:
        fecha_vencimiento = _calcular_vencimiento(fecha_realizacion, periodicidad_dias)

    existente = cur.execute(
        "SELECT id FROM empleado_cursos WHERE empleado_id = ? AND curso_id = ?",
        (empleado_id, curso_id),
    ).fetchone()

    if existente:
        cur.execute("""
            UPDATE empleado_cursos
            SET fecha_realizacion = ?, fecha_vencimiento = ?
            WHERE id = ?
        """, (fecha_realizacion, fecha_vencimiento, existente[0]))
    else:
        cur.execute("""
            INSERT INTO empleado_cursos (empleado_id, curso_id, fecha_realizacion, fecha_vencimiento)
            VALUES (?, ?, ?, ?)
        """, (empleado_id, curso_id, fecha_realizacion, fecha_vencimiento))

    conn.commit()
    conn.close()
