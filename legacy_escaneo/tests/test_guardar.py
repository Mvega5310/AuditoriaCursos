from datetime import date

import pytest
from sqlalchemy import select

import db
from scripts.guardar_certificado import guardar_registro


def _fila(engine, cedula):
    with engine.connect() as conn:
        return conn.execute(select(db.empleados).where(db.empleados.c.cedula == cedula)).one()


def test_nuevo_y_luego_actualizado(engine):
    r1 = guardar_registro(cedula="123456", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2026-12-01")
    r2 = guardar_registro(cedula="123456", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2027-12-01")
    assert (r1, r2) == ("nuevo", "actualizado")
    with engine.connect() as conn:
        filas = conn.execute(select(db.empleado_cursos)).all()
    assert len(filas) == 1 and filas[0].fecha_vencimiento == date(2027, 12, 1)


def test_vencimiento_se_calcula_con_la_periodicidad_del_curso(engine):
    guardar_registro(cedula="123456", nombre="Ana", apellido="Ruiz", curso="Bioseguridad", fecha_realizacion="2026-01-01")
    with engine.connect() as conn:
        assert conn.scalar(select(db.empleado_cursos.c.fecha_vencimiento)) == date(2027, 1, 1)  # 365 días


def test_valor_vacio_no_borra_cargo_ni_area(engine):
    guardar_registro(cedula="123456", nombre="Ana", apellido="Ruiz", cargo="Enfermera", area="UCI",
                     curso="RCP", fecha_vencimiento="2026-12-01")
    guardar_registro(cedula="123456", curso="Bioseguridad", fecha_vencimiento="2026-12-01")
    fila = _fila(engine, "123456")
    assert (fila.nombre, fila.cargo, fila.area) == ("Ana", "Enfermera", "UCI")


def test_cedula_con_puntos_se_normaliza(engine):
    guardar_registro(cedula="1.234.567", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2026-12-01")
    assert _fila(engine, "1234567").nombre == "Ana"


def test_renovar_sin_fecha_realizacion_conserva_la_anterior(engine):
    guardar_registro(cedula="123456", nombre="Ana", apellido="Ruiz", curso="RCP",
                     fecha_realizacion="2026-01-01", fecha_vencimiento="2026-12-31")
    guardar_registro(cedula="123456", curso="RCP", fecha_vencimiento="2027-12-31")
    with engine.connect() as conn:
        assert conn.scalar(select(db.empleado_cursos.c.fecha_realizacion)) == date(2026, 1, 1)


@pytest.mark.parametrize("kwargs, texto", [
    (dict(cedula="12", nombre="A", apellido="B", curso="RCP", fecha_vencimiento="2026-12-01"), "cédula"),
    (dict(cedula="123456", nombre="A", apellido="B", curso="", fecha_vencimiento="2026-12-01"), "curso"),
    (dict(cedula="123456", nombre="A", apellido="B", curso="RCP"), "fecha"),
    (dict(cedula="123456", nombre="A", apellido="B", curso="RCP", fecha_vencimiento="31/12/2026"), "no es válida"),
    (dict(cedula="123456", nombre="A", apellido="B", curso="RCP",
          fecha_realizacion="2026-06-01", fecha_vencimiento="2026-05-01"), "anterior"),
    (dict(cedula="123456", nombre="", apellido="", curso="RCP", fecha_vencimiento="2026-12-01"), "obligatorios"),
    (dict(cedula="123456", nombre="A" * 500, apellido="B", curso="RCP", fecha_vencimiento="2026-12-01"), "supera"),
])
def test_validaciones(engine, kwargs, texto):
    with pytest.raises(ValueError, match=texto):
        guardar_registro(**kwargs)
