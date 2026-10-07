from datetime import date

import pytest
from sqlalchemy import update

import db
import vencimientos
from scripts.guardar_certificado import guardar_registro

HOY = date(2026, 10, 5)


@pytest.mark.parametrize("dias, clase", [
    (-30, "vencido"), (-1, "vencido"),
    (0, "critico"), (7, "critico"),
    (8, "alerta"), (15, "alerta"),
    (16, "proximo"), (30, "proximo"),
    (31, "normal"), (365, "normal"),
])
def test_clasificar_en_los_limites(dias, clase):
    assert vencimientos.clasificar(dias)[0] == clase


def test_consulta_incluye_vencidos_y_excluye_lo_lejano_y_los_inactivos(engine):
    guardar_registro(cedula="1001", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2026-09-25")      # vencido hace 10
    guardar_registro(cedula="1002", nombre="Luis", apellido="Mora", curso="RCP", fecha_vencimiento="2026-10-05")     # hoy
    guardar_registro(cedula="1003", nombre="Eva", apellido="Paz", curso="RCP", fecha_vencimiento="2026-10-12")       # 7 días
    guardar_registro(cedula="1004", nombre="Sol", apellido="Gil", curso="RCP", fecha_vencimiento="2026-10-13")       # 8 días: fuera de ventana 7
    guardar_registro(cedula="1005", nombre="Rey", apellido="Luna", curso="RCP", fecha_vencimiento="2026-10-01")      # inactivo
    with engine.begin() as conn:
        conn.execute(update(db.empleados).where(db.empleados.c.cedula == "1005").values(activo=False))

    with engine.connect() as conn:
        registros = vencimientos.consultar_vencimientos(conn, 7, HOY)

    por_cedula = {r["cedula"]: r for r in registros}
    assert set(por_cedula) == {"1001", "1002", "1003"}
    assert por_cedula["1001"]["dias_restantes"] == -10
    assert por_cedula["1001"]["clase"] == "vencido"
    assert por_cedula["1001"]["dias_texto"] == "Hace 10 días"
    assert por_cedula["1002"]["dias_restantes"] == 0 and por_cedula["1002"]["clase"] == "critico"
    assert por_cedula["1003"]["dias_restantes"] == 7
    # el más vencido va primero
    assert registros[0]["cedula"] == "1001"


def test_resumen(engine):
    guardar_registro(cedula="1001", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2026-09-01")
    guardar_registro(cedula="1002", nombre="Luis", apellido="Mora", curso="RCP", fecha_vencimiento="2026-10-08")
    with engine.connect() as conn:
        resumen = vencimientos.calcular_resumen(vencimientos.consultar_vencimientos(conn, 30, HOY))
    assert resumen == {"vencido": 1, "critico": 1, "alerta": 0, "proximo": 0, "normal": 0}
