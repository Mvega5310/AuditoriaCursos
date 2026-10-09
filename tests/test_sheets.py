"""Sincronizacion con Google Sheets: lectura de celdas, importacion diaria, errores y pantalla web."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import config                    # noqa: E402
import core                      # noqa: E402
import sheets                    # noqa: E402
import web                       # noqa: E402
from test_web import CLAVE, _csrf, registrar  # noqa: E402

SHEET_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid=0"
CUENTA = json.dumps({"client_email": "autcursos@proyecto.iam.gserviceaccount.com", "private_key": "x"})


def _f(dias):
    return (date.today() + timedelta(days=dias)).strftime("%d/%m/%Y")


def hoja(filas, cols=("Cedula", "Nombre", "Documento", "Fecha Vencimiento")):
    return {"Documentos": pd.DataFrame([list(cols)] + filas, dtype=object)}


@pytest.fixture
def cuenta(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", CUENTA)


def test_extraer_id():
    assert sheets.extraer_id(URL) == SHEET_ID
    assert sheets.extraer_id(f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit?usp=sharing") == SHEET_ID
    assert sheets.extraer_id(SHEET_ID) == SHEET_ID
    for malo in ("", None, "https://evil.test/spreadsheets/d/" + SHEET_ID, "https://docs.google.com/document/d/abc", "hola"):
        assert sheets.extraer_id(malo) is None


def test_celdas_de_la_api_fechas_por_formato_no_por_pantalla():
    datos = {"sheets": [{"properties": {"title": "Matriz"}, "data": [{"rowData": [
        {"values": [{"effectiveValue": {"stringValue": "Cedula"}}, {"effectiveValue": {"stringValue": "RCP | 365"}}]},
        {"values": [{"effectiveValue": {"numberValue": 1001.0}},                      # cedula numerica
                    {"effectiveValue": {"numberValue": 46100.0},
                     "effectiveFormat": {"numberFormat": {"type": "DATE"}}}]},         # fecha
        {"values": [{"effectiveValue": {"stringValue": "0042"}}, {}]},                 # texto con ceros; celda vacia
        {},                                                                            # fila vacia
    ]}]}]}
    df = sheets.hojas_desde_respuesta(datos)["Matriz"]
    assert df.iloc[1, 0] == "1001" and df.iloc[1, 1] == "2026-03-19"
    assert df.iloc[2, 0] == "0042" and df.iloc[2, 1] is None


def test_leer_hojas_traduce_errores_de_google(cuenta):
    class R:
        def __init__(self, code):
            self.status_code, self.text = code, ""

        def json(self):
            return {"sheets": []}

    class S:
        def __init__(self, code):
            self.code = code

        def get(self, *a, **k):
            return R(self.code)

    with pytest.raises(sheets.SheetsError, match="autcursos@proyecto"):
        sheets.leer_hojas(SHEET_ID, sesion=S(403))
    with pytest.raises(sheets.SheetsError, match="no es una hoja de Google"):
        sheets.leer_hojas(SHEET_ID, sesion=S(400))
    assert sheets.leer_hojas(SHEET_ID, sesion=S(200)) == {}


def _empresa(db, url=URL, fechas="realizacion"):
    conn = core.conectar(db)
    core.inicializar_db(conn)
    conn.execute("INSERT INTO empresas (nombre, sheet_url, sheet_fechas) VALUES ('Hospital A', ?, ?)", (url, fechas))
    conn.commit()
    eid = conn.execute("SELECT id FROM empresas").fetchone()[0]
    conn.close()
    return eid


def test_sincronizacion_diaria_actualiza_y_no_borra(tmp_path):
    db = tmp_path / "t.db"
    eid = _empresa(db)
    dia1 = hoja([["1", "Ana Perez", "RCP", _f(10)], ["2", "Luis Ruiz", "RCP", _f(20)]])
    r = sheets.sincronizar_empresa(db, eid, lambda _id: dia1)
    assert r["estado"].startswith("ok: 2 nuevos")

    # Dia 2: Ana renovo, Luis desaparecio de la hoja, entra Eva con un dato malo
    dia2 = hoja([["1", "Ana Perez", "RCP", _f(375)], ["3", "Eva Gil", "RCP", "no-es-fecha"]])
    r = sheets.sincronizar_empresa(db, eid, lambda _id: dia2)
    assert r["estado"].startswith("ok: 0 nuevos, 1 actualizados") and "1 filas con error" in r["estado"]
    assert "Eva" in r["detalle"] or "RCP" in r["detalle"]

    conn = core.conectar(db)
    assert conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 2           # Luis NO se borro
    assert conn.execute("SELECT COUNT(*) FROM documentos_historial").fetchone()[0] == 1  # renovacion de Ana
    e = conn.execute("SELECT sheet_sync_en, sheet_sync_estado FROM empresas").fetchone()
    assert e[0] and e[1].startswith("ok")
    conn.close()


def test_error_de_google_no_toca_los_datos_y_queda_registrado(tmp_path):
    db = tmp_path / "t.db"
    eid = _empresa(db)
    sheets.sincronizar_empresa(db, eid, lambda _id: hoja([["1", "Ana Perez", "RCP", _f(10)]]))

    def falla(_id):
        raise sheets.SheetsError("No tengo acceso a esa hoja.")

    r = sheets.sincronizar_empresa(db, eid, falla)
    assert r["estado"] == "error: No tengo acceso a esa hoja."
    conn = core.conectar(db)
    assert conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1
    assert conn.execute("SELECT sheet_sync_estado FROM empresas").fetchone()[0].startswith("error")
    conn.close()


def test_hoja_sin_columnas_reconocibles_explica_el_problema(tmp_path):
    db = tmp_path / "t.db"
    eid = _empresa(db)
    r = sheets.sincronizar_empresa(db, eid, lambda _id: {"Hoja 1": pd.DataFrame([["a", "b"], ["1", "2"]], dtype=object)})
    assert r["estado"].startswith("error: No encontre una lista")


def test_sincronizar_todas_una_falla_no_detiene_a_las_otras(tmp_path):
    db = tmp_path / "t.db"
    conn = core.conectar(db)
    core.inicializar_db(conn)
    conn.execute("INSERT INTO empresas (nombre, sheet_url) VALUES ('Mala', ?)", (URL,))
    conn.execute("INSERT INTO empresas (nombre, sheet_url) VALUES ('Buena', ?)", (URL.replace("0123", "9999"),))
    conn.execute("INSERT INTO empresas (nombre) VALUES ('SinHoja')")
    conn.commit()
    conn.close()

    def lector(sid):
        if "9999" not in sid:
            raise RuntimeError("boom")
        return hoja([["1", "Ana Perez", "RCP", _f(10)]])

    assert sheets.sincronizar_todas(db, lector) == 2
    conn = core.conectar(db)
    estados = {r[0]: r[1] for r in conn.execute("SELECT nombre, sheet_sync_estado FROM empresas")}
    assert estados["Mala"].startswith("error") and estados["Buena"].startswith("ok") and estados["SinHoja"] is None
    conn.close()


# ------------------------------------------------------------------ web
@pytest.fixture
def app(tmp_path):
    return web.create_app({"TESTING": True, "DB_PATH": tmp_path / "t.db", "SECRET_KEY": "x" * 32})


def _post(c, **datos):
    return c.post("/sheets", data={"csrf": _csrf(c, "/sheets"), **datos}, follow_redirects=True)


def test_web_sin_cuenta_de_servicio_avisa(app, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_SERVICE_ACCOUNT_JSON", None)
    c, _ = registrar(app, "Clinica A", "a@a.com")
    assert "aún no está activada".encode() in c.get("/sheets").data
    assert "aún no está activada".encode() in _post(c, accion="guardar", url=URL).data


def test_web_guardar_sincroniza_y_aisla_empresas(app, cuenta, monkeypatch):
    llamadas = []

    def lector(sid):
        llamadas.append(sid)
        return hoja([["77", "Zoe Mora", "RCP", _f(5)]])

    monkeypatch.setattr(sheets, "leer_hojas", lector)
    a, _ = registrar(app, "Clinica A", "a@a.com")
    b, _ = registrar(app, "Fabrica B", "b@b.com")
    pagina = a.get("/sheets").data
    assert b"autcursos@proyecto.iam.gserviceaccount.com" in pagina

    assert b"no parece un enlace" in _post(a, accion="guardar", url="https://evil.test/x").data
    assert llamadas == []

    r = _post(a, accion="guardar", url=URL, fechas="realizacion")
    assert b"ok: 1 nuevos" in r.data and llamadas == [SHEET_ID]
    assert b"Mora" in a.get("/panel").data
    assert b"Mora" not in b.get("/panel").data                       # aislamiento
    assert SHEET_ID.encode() not in b.get("/sheets").data            # B no ve el enlace de A

    # "Sincronizar ahora" repetido de inmediato se frena
    assert b"Espere un minuto" in _post(a, accion="sincronizar").data
    assert len(llamadas) == 1

    _post(a, accion="quitar")
    assert SHEET_ID.encode() not in a.get("/sheets").data
    assert b"Mora" in a.get("/panel").data                           # los datos se conservan


def test_web_sheets_exige_login(app):
    assert app.test_client().get("/sheets").status_code == 302
