"""Ventanas de alerta (periodo + anticipacion), avisos por empresa, panel con filtros, Excel y resumen al importar."""
import io
import sys
from datetime import timedelta
from pathlib import Path

import openpyxl

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import config                    # noqa: E402
import core                      # noqa: E402
import generar_alertas           # noqa: E402
from test_web import app, registrar, subir, _csrf   # noqa: E402,F401

H = core.hoy()


def _f(d):
    return (H + timedelta(days=d)).strftime("%d/%m/%Y")


def _excel(filas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Area", "Documento", "Fecha Vencimiento"])
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


FILAS = [
    ["101", "Ana", "Uno", "Enfermera", "UCI", "BLS", _f(-5)],          # vencido
    ["101", "Ana", "Uno", "Enfermera", "UCI", "ACLS", _f(5)],          # 7
    ["102", "Luis", "Dos", "Auxiliar", "Urgencias", "BLS", _f(12)],    # 15
    ["102", "Luis", "Dos", "Auxiliar", "Urgencias", "ACLS", _f(25)],   # 30
    ["103", "Eva", "Tres", "Auxiliar", "Urgencias", "BLS", _f(40)],    # solo semanal 37? no: 40 > 37
    ["104", "Raul", "Cuatro", "Auxiliar", "UCI", "BLS", _f(55)],       # mensual 60
]


def test_ventana_es_periodo_mas_anticipacion():
    assert config.ventana_alerta("diaria", 90) == 7                  # fija
    assert config.ventana_alerta("semanal") == 37
    assert config.ventana_alerta("quincenal") == 45
    assert config.ventana_alerta("mensual") == 60
    assert config.ventana_alerta("quincenal", 60) == 75


def test_resumen_al_importar_y_validar(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    r = c.post("/importar", data={"csrf": _csrf(c, "/importar"), "archivo": (_excel(FILAS), "d.xlsx"),
                                  "accion": "validar"}, content_type="multipart/form-data")
    html = r.data.decode()
    assert "Así quedaría Hospital Z" in html
    assert ">1</b>Vencidos" in html and ">1</b>Vencen en 7" in html and ">2</b>Vencen en 15" in html
    assert ">3</b>Vencen en 30" in html


def test_panel_filtra_por_ventana_curso_y_ubicacion(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _excel(FILAS))
    assert "6 registros" in c.get("/panel").data.decode()                       # por defecto: ventana maxima (60)
    assert "1 registro" in c.get("/panel?ventana=vencidos").data.decode()
    assert "3 registros" in c.get("/panel?ventana=15").data.decode()
    html = c.get("/panel?ventana=60&curso=BLS&ubicacion=UCI").data.decode()
    assert "2 registros" in html and "Raul" in html and "Luis" not in html.split("Documentos exigidos")[0]
    assert c.get("/panel?ventana=abc&curso=<script>").status_code == 200        # valores raros: se ignoran
    x = c.get("/panel.xlsx?ventana=30&ubicacion=Urgencias")
    ws = openpyxl.load_workbook(io.BytesIO(x.data))["Vencimientos"]
    assert [r[0].value for r in ws.iter_rows(min_row=2)] == ["Dos, Luis", "Dos, Luis"]


def test_avisos_por_empresa(app, tmp_path, monkeypatch):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _excel(FILAS))
    r = c.post("/avisos", data={"csrf": _csrf(c, "/avisos"), "anticipacion": "10", "alertas": ["quincenal"]})
    assert r.status_code == 302
    assert "15 + 10 = <b>25 días" in c.get("/avisos").data.decode()
    assert c.post("/avisos", data={"csrf": _csrf(c, "/avisos"), "anticipacion": "999"}).status_code == 302
    conn = core.conectar(app.config["DB_PATH"])
    eid = conn.execute("SELECT id FROM empresas WHERE nombre = 'Hospital Z'").fetchone()["id"]
    conf = generar_alertas.configuracion_empresa(conn, eid)
    assert conf["anticipacion"] == 10 and conf["activas"] == {"quincenal"}          # el 999 no se guardo

    salidas = tmp_path / "salidas"
    monkeypatch.setattr(generar_alertas, "SALIDAS_DIR", salidas)
    generar_alertas.ejecutar_alerta("semanal", dry_run=True, db_path=app.config["DB_PATH"])
    assert not salidas.exists() or not list(salidas.glob("*hospital_z*"))            # no recibe semanal
    generar_alertas.ejecutar_alerta("quincenal", dry_run=True, db_path=app.config["DB_PATH"])
    html = (salidas / "alerta_quincenal_hospital_z.html").read_text(encoding="utf-8")
    assert "próximos 25 días" in html and "Cuatro" not in html and "Dos" in html     # 55 dias queda fuera

    c.post("/avisos", data={"csrf": _csrf(c, "/avisos"), "anticipacion": "30"})   # ninguna marcada
    assert generar_alertas.configuracion_empresa(conn, eid)["activas"] == set()


def test_excel_del_panel_no_ejecuta_formulas(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Area", "Documento", "Fecha Vencimiento"])
    ws.append(["105", "=HYPERLINK(\"http://x\",\"clic\")", "", "Auxiliar", "UCI", "BLS", _f(3)])
    ws["B2"].data_type = "s"                       # texto que empieza por "=", como lo guardaria un usuario
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    subir(c, buf)
    x = c.get("/panel.xlsx")
    wb = openpyxl.load_workbook(io.BytesIO(x.data))
    celda = wb["Vencimientos"]["A2"]
    assert celda.data_type == "s" and celda.value.startswith("=HYPERLINK")
