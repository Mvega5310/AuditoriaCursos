from datetime import date, datetime

import openpyxl
from sqlalchemy import func, select

import db
from scripts import importar_excel


def _crear_excel(ruta, filas, encabezado=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(encabezado or ["Cédula", "Nombre", "Apellido", "Cargo", "Área", "Curso",
                              "Fecha Realización", "Fecha Vencimiento"])
    for fila in filas:
        ws.append(fila)
    wb.save(ruta)


def test_importa_formatos_mixtos_y_reporta_errores_por_fila(engine, tmp_path):
    ruta = tmp_path / "datos.xlsx"
    _crear_excel(ruta, [
        [1234567.0, "Ana", "Ruiz", "Enfermera", "UCI", "RCP", datetime(2026, 1, 10), datetime(2027, 1, 10)],
        ["7654321", "Luis", "Mora", "", "", "Bioseguridad", None, "15/03/2027"],
        ["1111111", "Eva", "Paz", "", "", "RCP", None, "no-es-fecha"],           # error
        [None, None, None, None, None, None, None, None],                         # fila en blanco: se ignora
        ["2222222", "Sol", "Gil", "", "", "RCP", "2026-02-01", None],             # vencimiento calculado
    ])
    r = importar_excel.importar(str(ruta))

    assert (r.nuevos, r.actualizados, len(r.errores)) == (3, 0, 1)
    assert "Fila 4" in r.errores[0]
    with engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.empleados)) == 3  # la fila mala no dejó residuos
        venc = {row.cedula: row.fecha_vencimiento for row in conn.execute(
            select(db.empleados.c.cedula, db.empleado_cursos.c.fecha_vencimiento)
            .join(db.empleado_cursos, db.empleado_cursos.c.empleado_id == db.empleados.c.id))}
    assert venc["1234567"] == date(2027, 1, 10)
    assert venc["7654321"] == date(2027, 3, 15)
    assert venc["2222222"] == date(2027, 2, 1)


def test_reimportar_actualiza_sin_duplicar(engine, tmp_path):
    ruta = tmp_path / "datos.xlsx"
    _crear_excel(ruta, [["1234567", "Ana", "Ruiz", "", "", "RCP", None, "2026-12-01"]])
    importar_excel.importar(str(ruta))
    r = importar_excel.importar(str(ruta))
    assert (r.nuevos, r.actualizados) == (0, 1)


def test_faltan_columnas(engine, tmp_path):
    ruta = tmp_path / "malo.xlsx"
    _crear_excel(ruta, [["a", "b"]], encabezado=["Cedula", "Nombre"])
    try:
        importar_excel.importar(str(ruta))
    except ValueError as e:
        assert "Faltan columnas" in str(e)
    else:
        raise AssertionError("debía fallar")
