"""Cursos de vigencia indefinida (modalidad de pago unico).

Lo critico que se verifica: la marca es POR DOCUMENTO. Dos personas con el mismo curso, una en la
modalidad de pago unico y otra en la renovable, deben comportarse distinto: la segunda sigue alertando.
"""
import io
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


def _f(d):
    return (H + timedelta(days=d)).strftime("%d/%m/%Y")


def _lista(filas):
    """Hoja 'Documentos': Cedula | Nombre | Apellido | Cargo | Documento | Fecha Vencimiento | Vigencia"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documentos"
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Documento", "Fecha Vencimiento", "Vigencia"])
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _matriz(celdas):
    """Hoja 'Matriz': una columna por curso; cada celda es la fecha (o la marca)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Matriz"
    ws.append(["Cedula", "Nombre completo", "Cargo", "RCP"])
    for fila in celdas:
        ws.append(fila)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _subir_matriz(c, buf):
    """Sube la matriz declarando que las celdas son fechas de VENCIMIENTO (no de realizacion)."""
    return c.post("/importar", data={"csrf": _csrf(c, "/importar"), "archivo": (buf, "m.xlsx"),
                                     "fechas": "vencimiento"}, content_type="multipart/form-data")


def _estados(app, eid=1, **kw):
    with core.conectar(app.config["DB_PATH"]) as con:
        return {(r["titular"], r["tipo"]): r["estado_label"]
                for r in generar_alertas.consultar_vencimientos(con, eid, 60, **kw)}


# ------------------------------------------------------------------ lo esencial

def test_la_marca_es_por_documento_no_por_curso(app):
    """Mismo curso, dos personas: la de pago unico no alerta, la renovable si."""
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _lista([
        ["101", "Ana", "Uno", "Enfermera", "RCP", _f(-200), "indefinida"],   # pago unico: vencida en papel
        ["102", "Luis", "Dos", "Enfermera", "RCP", _f(-200), ""],            # renovable: de verdad vencida
    ]))
    # el correo (sin indefinidos) solo trae a Luis
    assert _estados(app) == {("Dos, Luis", "RCP"): "VENCIDO"}
    # el panel los muestra a los dos, con estados distintos
    assert _estados(app, incluir_indefinidos=True) == {
        ("Uno, Ana", "RCP"): "INDEFINIDO",
        ("Dos, Luis", "RCP"): "VENCIDO",
    }


def test_conserva_la_fecha_impresa_del_certificado(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _lista([["101", "Ana", "Uno", "Enfermera", "RCP", "INDEF: " + _f(-200), ""]]))
    with core.conectar(app.config["DB_PATH"]) as con:
        d = con.execute("SELECT fecha_vencimiento, vigencia_indefinida FROM documentos").fetchone()
    assert d["vigencia_indefinida"] == 1
    assert d["fecha_vencimiento"] == (H - timedelta(days=200)).isoformat()   # la fecha NO se pierde


def test_marca_sola_sin_fecha(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _lista([["101", "Ana", "Uno", "Enfermera", "RCP", "INDEFINIDO", ""]]))
    with core.conectar(app.config["DB_PATH"]) as con:
        d = con.execute("SELECT fecha_vencimiento, vigencia_indefinida FROM documentos").fetchone()
    assert (d["fecha_vencimiento"], d["vigencia_indefinida"]) == (None, 1)


def test_en_la_matriz_la_marca_viaja_en_la_celda(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    _subir_matriz(c, _matriz([
        ["101", "Ana Uno", "Enfermera", "INDEF " + _f(-300)],
        ["102", "Luis Dos", "Enfermera", _f(-300)],
    ]))
    assert _estados(app) == {("Luis Dos", "RCP"): "VENCIDO"}        # Ana no alerta
    assert _estados(app, incluir_indefinidos=True)[("Ana Uno", "RCP")] == "INDEFINIDO"


def test_no_se_confunde_con_na_ni_con_fecha_dudosa(app):
    """N/A sigue significando 'no le aplica'. Y un pago unico vencido hace anos no es error de digitacion."""
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    r = _subir_matriz(c, _matriz([["101", "Ana Uno", "Enfermera", "N/A"],
                                  ["102", "Luis Dos", "Enfermera", "INDEF 01/01/2015"]]))
    assert r.status_code == 200
    with core.conectar(app.config["DB_PATH"]) as con:
        filas = {f["cedula"]: (f["no_aplica"], f["vigencia_indefinida"], f["fecha_vencimiento"])
                 for f in con.execute("""SELECT e.cedula, d.no_aplica, d.vigencia_indefinida, d.fecha_vencimiento
                                         FROM documentos d JOIN empleados e ON e.id = d.empleado_id""")}
    assert filas["101"] == (1, 0, None)                 # N/A: no le aplica
    assert filas["102"] == (0, 1, "2015-01-01")         # pago unico de 2015: se guarda, no se descarta


def test_el_panel_lo_separa_de_los_vencidos(app):
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, _lista([
        ["101", "Ana", "Uno", "Enfermera", "RCP", _f(-200), "indefinida"],
        ["102", "Luis", "Dos", "Enfermera", "RCP", _f(-200), ""],
    ]))
    html = c.get("/panel").data.decode()
    assert ">1</b>Vencidos" in html                     # solo Luis cuenta como vencido
    assert ">1</b>Vigencia indefinida" in html          # Ana tiene su propio KPI
    solo = c.get("/panel?ventana=indefinidos").data.decode()
    assert "Uno, Ana" in solo and "Dos, Luis" not in solo


def test_cuenta_como_presente_para_los_requisitos(app):
    """Un curso de pago unico no debe aparecer como 'documento exigido sin registrar'."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documentos"
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Documento", "Fecha Vencimiento", "Vigencia"])
    ws.append(["101", "Ana", "Uno", "Enfermera", "RCP", "INDEFINIDO", ""])
    wr = wb.create_sheet("Requisitos")
    wr.append(["Cargo", "Documento"])
    wr.append(["Enfermera", "RCP"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    c, _ = registrar(app, "Hospital Z", "z@z.com")
    subir(c, buf)
    with core.conectar(app.config["DB_PATH"]) as con:
        assert generar_alertas.consultar_faltantes(con, 1) == []


def test_columna_entera_de_pago_unico_no_se_descarta(app):
    """Antes, una columna sin ninguna fecha legible se ignoraba como 'no es un curso'."""
    c, _ = registrar(app, "Hospital Z", "z@z.com")
    r = _subir_matriz(c, _matriz([["101", "Ana Uno", "Enfermera", "INDEFINIDO"],
                                  ["102", "Luis Dos", "Enfermera", "INDEFINIDO"]]))
    assert "ignoraron" not in r.data.decode()
    with core.conectar(app.config["DB_PATH"]) as con:
        filas = con.execute("SELECT vigencia_indefinida FROM documentos").fetchall()
    assert len(filas) == 2 and all(f["vigencia_indefinida"] == 1 for f in filas)
