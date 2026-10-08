"""Formatos reales de Excel: encabezados habituales en Colombia, fechas en texto/serial y mensajes de la web."""
import io
import sys
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import core                      # noqa: E402
import importar_excel            # noqa: E402


@pytest.mark.parametrize("encabezado,esperado", [
    ("N° Identificación", "cedula"), ("Nº Identificación", "cedula"), ("Nro. de cédula", "cedula"),
    ("N° de Cédula", "cedula"), ("Cedula/NIT", "cedula"), ("No. Identificación", "cedula"), ("Cédula", "cedula"),
    ("Nombre del trabajador", "nombre"), ("Trabajador", "nombre"), ("Colaborador", "nombre"),
    ("Fecha de Expedición", "fecha_emision"), ("Fecha de Venc.", "fecha_vencimiento"), ("Vence", "fecha_vencimiento"),
    ("Documento", "documento"), ("Número de documento", "referencia"),
])
def test_encabezados_reconocidos(encabezado, esperado):
    assert importar_excel._canon(encabezado, importar_excel.ALIAS_DOCUMENTOS) == esperado


def test_id_suelto_ya_no_se_toma_como_cedula():
    assert importar_excel._canon("ID", importar_excel.ALIAS_DOCUMENTOS) is None


@pytest.mark.parametrize("valor,iso", [
    ("15/03/2026", "2026-03-15"), ("2026-03-15 00:00:00", "2026-03-15"), ("15/03/2026 10:30", "2026-03-15"),
    ("15-mar-26", "2026-03-15"), ("15 de marzo de 2026", "2026-03-15"), ("3 Ene 2027", "2027-01-03"),
    ("46100", "2026-03-19"), ("46100.0", "2026-03-19"), (datetime(2026, 3, 15), "2026-03-15"),
])
def test_fechas_aceptadas(valor, iso):
    assert core.parsear_fecha(valor) == iso


@pytest.mark.parametrize("valor", ["", "nan", "abc", "31/02/2026", "32-mar-26", "123", "1234567", "15-xyz-26"])
def test_fechas_rechazadas(valor):
    assert core.parsear_fecha(valor) is None


def test_numero_de_5_cifras_no_vuelve_curso_una_columna(tmp_path):
    """Un valor/consecutivo de 5 cifras no es una fecha: la columna se ignora en la matriz."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["N° Identificación", "Nombre del trabajador", "RCP | 365", "Salario"])
    ws.append(["1", "Ana Perez", date(2026, 1, 10), "45000"])
    ws.append(["2", "Luis Ruiz", "10-ene-26", "52000"])
    ruta = tmp_path / "m.xlsx"
    wb.save(ruta)
    s = importar_excel.importar(ruta, db_path=tmp_path / "t.db", verbose=False)
    assert s["nuevos"] == 2 and s["errores"] == [] and s["columnas_ignoradas"] == ["Salario"]
