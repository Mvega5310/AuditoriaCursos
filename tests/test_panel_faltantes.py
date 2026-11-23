"""El panel nunca marca ceros mientras el aviso lleva documentos exigidos sin registrar."""
import io
import re
import sys
from datetime import timedelta
from pathlib import Path

import openpyxl

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import core                      # noqa: E402
import generar_alertas           # noqa: E402
from test_web import app, registrar, subir, _csrf   # noqa: E402,F401

H = core.hoy()


def _libro():
    """Un curso vigente MUY lejano (fuera de toda ventana) y un curso exigido que nunca se registro."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documentos"
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Documento", "Fecha Vencimiento"])
    ws.append(["101", "Ana", "Uno", "Enfermera", "BLS", (H + timedelta(days=500)).strftime("%d/%m/%Y")])
    wr = wb.create_sheet("Requisitos")
    wr.append(["Cargo", "Documento"])
    wr.append(["Enfermera", "ACLS"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def test_panel_tiene_kpi_sin_registrar_y_abre_la_seccion(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _libro())
    html = c.get("/panel").data.decode()
    assert ">1</b>Vencidos" not in html and ">0</b>Vencidos" in html       # ningun vencimiento...
    assert ">1</b>Sin registrar" in html                                  # ...pero el faltante se ve
    assert re.search(r'id="faltantes"[^>]*\bopen\b', html)                # y la seccion viene abierta


def test_sin_faltantes_no_hay_kpi(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    assert "Sin registrar</a>" not in c.get("/panel").data.decode()


def test_el_historial_separa_vencimientos_de_faltantes(app, monkeypatch):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _libro())
    monkeypatch.setattr(generar_alertas, "enviar_email", lambda *a, **k: True)
    generar_alertas.ejecutar_alerta("semanal", db_path=app.config["DB_PATH"])
    html = c.get("/alertas").data.decode()
    assert "0 vencimientos, 1 sin registrar" in html
