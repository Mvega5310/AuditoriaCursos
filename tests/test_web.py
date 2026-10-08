"""
Pruebas de la app web: registro, verificacion, login y, sobre todo, AISLAMIENTO entre empresas.
Ejecutar:  python -m pytest -q
"""
import io
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import web  # noqa: E402

CLAVE = "clave-segura-123"


@pytest.fixture
def app(tmp_path):
    return web.create_app({"TESTING": True, "DB_PATH": tmp_path / "t.db", "SECRET_KEY": "x" * 32})


def _csrf(client, ruta="/login"):
    client.get(ruta)
    with client.session_transaction() as s:
        return s["csrf"]


def registrar(app, empresa, email, activar=True, **extra):
    c = app.test_client()
    datos = {"csrf": _csrf(c, "/registro"), "empresa": empresa, "nit": "900", "sector": "salud",
             "email": email, "password": CLAVE, "password2": CLAVE, "consentimiento": "1", **extra}
    r = c.post("/registro", data=datos)
    if activar:
        c.get(app.config["ULTIMO_ENLACE"].replace(app.config["APP_URL"], ""))
        c.post("/login", data={"csrf": _csrf(c), "email": email, "password": CLAVE})
    return c, r


def excel(filas, hoja="Documentos"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = hoja
    ws.append(["Empresa", "Cedula", "Nombre", "Apellido", "Cargo", "Documento", "Fecha Vencimiento"])
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def subir(c, buf):
    return c.post("/importar", data={"csrf": _csrf(c, "/importar"), "archivo": (buf, "datos.xlsx")},
                  content_type="multipart/form-data")


def test_registro_requiere_verificar_correo(app):
    c, r = registrar(app, "Clinica A", "a@a.com", activar=False)
    assert r.status_code == 302
    c.post("/login", data={"csrf": _csrf(c), "email": "a@a.com", "password": CLAVE})
    assert c.get("/panel").status_code == 302          # sin verificar no entra
    c.get(app.config["ULTIMO_ENLACE"].replace(app.config["APP_URL"], ""))
    c.post("/login", data={"csrf": _csrf(c), "email": "a@a.com", "password": CLAVE})
    assert c.get("/panel").status_code == 200


def test_registro_valida_datos(app):
    c = app.test_client()
    r = c.post("/registro", data={"csrf": _csrf(c, "/registro"), "empresa": "X", "email": "mal",
                                  "password": "corta", "password2": "otra"})
    assert r.status_code == 400
    assert b"tratamiento de datos" in r.data


def test_empresa_duplicada_rechazada(app):
    registrar(app, "Clinica A", "a@a.com")
    _, r = registrar(app, "Clinica A", "otro@x.com", activar=False)
    assert r.status_code == 400


def test_post_sin_csrf_rechazado(app):
    assert app.test_client().post("/login", data={"email": "a@a.com", "password": "x"}).status_code == 400


def test_login_bloquea_tras_intentos(app):
    registrar(app, "Clinica A", "a@a.com")
    c = app.test_client()
    codigos = [c.post("/login", data={"csrf": _csrf(c), "email": "a@a.com", "password": "mala"}).status_code
               for _ in range(web.INTENTOS_MAX + 1)]
    assert codigos[:web.INTENTOS_MAX] == [401] * web.INTENTOS_MAX and codigos[-1] == 429


def test_aislamiento_entre_empresas(app):
    vence = (date.today() + timedelta(days=3)).strftime("%d/%m/%Y")
    ca, _ = registrar(app, "Clinica A", "a@a.com")
    cb, _ = registrar(app, "Fabrica B", "b@b.com")
    # A sube un Excel que intenta escribir en "Fabrica B": debe quedar todo en A
    r = subir(ca, excel([["Fabrica B", "111", "Ana", "Perez", "Enfermera", "RCP", vence]]))
    assert r.status_code == 200
    assert b"Perez" in ca.get("/panel").data
    assert b"Perez" not in cb.get("/panel").data
    con = web.conectar(app.config["DB_PATH"])
    por_empresa = dict(con.execute("""SELECT e.nombre, COUNT(*) FROM documentos d
                                      JOIN empresas e ON e.id = d.empresa_id GROUP BY e.nombre"""))
    assert por_empresa == {"Clinica A": 1}


def test_responsables_no_se_pueden_quitar_entre_empresas(app):
    ca, _ = registrar(app, "Clinica A", "a@a.com")
    cb, _ = registrar(app, "Fabrica B", "b@b.com")
    con = web.conectar(app.config["DB_PATH"])
    rid_a = con.execute("""SELECT r.id FROM responsables r JOIN empresas e ON e.id = r.empresa_id
                           WHERE e.nombre = 'Clinica A'""").fetchone()[0]
    cb.post(f"/responsables/{rid_a}/quitar", data={"csrf": _csrf(cb, "/responsables")})
    assert con.execute("SELECT activo FROM responsables WHERE id = ?", (rid_a,)).fetchone()[0] == 1
    assert b"a@a.com" in ca.get("/responsables").data
    assert b"a@a.com" not in cb.get("/responsables").data


def test_importar_rechaza_no_xlsx(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    r = c.post("/importar", data={"csrf": _csrf(c, "/importar"), "archivo": (io.BytesIO(b"x"), "a.exe")},
               content_type="multipart/form-data")
    assert r.status_code == 400


def test_alertas_van_a_los_responsables_de_cada_empresa(app):
    import generar_alertas
    registrar(app, "Clinica A", "a@a.com")
    registrar(app, "Fabrica B", "b@b.com")
    con = web.conectar(app.config["DB_PATH"])
    ids = {r["nombre"]: r["id"] for r in con.execute("SELECT id, nombre FROM empresas")}
    assert generar_alertas.obtener_destinatarios(con, ids["Clinica A"]) == ["a@a.com"]
    assert generar_alertas.obtener_destinatarios(con, ids["Fabrica B"]) == ["b@b.com"]


# ------------------------------------------------------------ importacion tolerante y explicada

def _xlsx(filas_por_hoja):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for nombre, filas in filas_por_hoja.items():
        ws = wb.create_sheet(nombre)
        for f in filas:
            ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _subir(c, buf, accion="guardar"):
    return c.post("/importar", data={"csrf": _csrf(c, "/importar"), "archivo": (buf, "d.xlsx"), "accion": accion},
                  content_type="multipart/form-data")


def test_plantilla_se_descarga_y_se_importa_sin_errores(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    r = c.get("/plantilla.xlsx")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    r = _subir(c, io.BytesIO(r.data))
    assert r.status_code == 200 and b"Filas que no se pudieron usar" not in r.data
    panel = c.get("/panel").data
    assert b"RCP" in panel and "Rojas".encode() in panel


def test_encabezado_no_esta_en_la_primera_fila_y_hay_columnas_extra(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    vence = (date.today() + timedelta(days=5)).strftime("%d/%m/%Y")
    buf = _xlsx({"Listado": [
        ["HOSPITAL X - LISTADO DE CURSOS 2026"], [], ["", "", ""],
        ["No. Identificación", "Nombres y Apellidos", "Teléfono", "Curso", "Fecha de Vencimiento"],
        ["555", "Carlos Ruiz", "300 000 0000", "RCP", vence]]})
    r = _subir(c, buf)
    assert r.status_code == 200
    assert b"Ruiz" in c.get("/panel").data


def test_archivo_no_reconocible_explica_que_faltaba(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    r = _subir(c, _xlsx({"Hoja1": [["Item", "Descripcion", "Valor"], ["1", "x", "2"]]}))
    assert r.status_code == 400
    texto = r.data.decode()
    assert "Documento" in texto and "Cedula" in texto and "Descripcion" in texto   # dice que falta y que encontro


def test_validar_sin_guardar_no_escribe(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    vence = (date.today() + timedelta(days=5)).strftime("%d/%m/%Y")
    buf = _xlsx({"D": [["Cedula", "Nombre", "Documento", "Fecha Vencimiento"], ["9", "Zoe Mora", "RCP", vence]]})
    r = _subir(c, buf, accion="validar")
    assert b"no se guard" in r.data and b"nuevos" in r.data.lower()
    assert b"Mora" not in c.get("/panel").data


def test_importar_fechas_invalidas_no_muestran_texto_de_consola(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    vence = (date.today() + timedelta(days=5)).strftime("%d/%m/%Y")
    buf = _xlsx({"D": [["Cedula", "Nombre", "Documento", "Fecha Vencimiento"], ["9", "Zoe Mora", "RCP", vence]]})
    r = c.post("/importar", data={"csrf": _csrf(c, "/importar"), "archivo": (buf, "d.xlsx"),
                                  "accion": "guardar", "fechas": "cualquier-cosa"},
               content_type="multipart/form-data")
    assert r.status_code == 200 and b"--fechas" not in r.data and b"Mora" in c.get("/panel").data


# ------------------------------------------------------------ alertas y guia

def _con_datos(c):
    vence = (date.today() + timedelta(days=3)).strftime("%d/%m/%Y")
    _subir(c, _xlsx({"D": [["Cedula", "Nombre", "Documento", "Fecha Vencimiento"], ["9", "Zoe Mora", "RCP", vence]]}))


def test_alertas_muestra_vista_previa_y_destinatarios(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    _con_datos(c)
    r = c.get("/alertas?tipo=semanal")
    assert r.status_code == 200
    assert b"a@a.com" in r.data and b"Zoe Mora" in r.data and b"<iframe" in r.data
    assert c.get("/alertas?tipo=cualquiera").status_code == 200      # tipo invalido cae al semanal


def test_alerta_prueba_solo_va_al_usuario_y_tiene_limite(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    _con_datos(c)
    c.post("/alertas/prueba", data={"csrf": _csrf(c, "/alertas"), "tipo": "diaria"})
    destino, html = app.config["ULTIMO_CORREO_PRUEBA"]
    assert destino == "a@a.com" and "Zoe Mora" in html
    app.config.pop("ULTIMO_CORREO_PRUEBA")
    r = c.post("/alertas/prueba", data={"csrf": _csrf(c, "/alertas"), "tipo": "diaria"}, follow_redirects=True)
    assert "ULTIMO_CORREO_PRUEBA" not in app.config and b"Espere un minuto" in r.data


def test_alertas_aislamiento_y_requiere_login(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    _con_datos(c)
    otro, _ = registrar(app, "Clinica B", "b@b.com")
    assert b"Zoe Mora" not in otro.get("/alertas").data
    assert app.test_client().get("/alertas").status_code == 302


def test_importar_trae_la_guia_en_modal(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    r = c.get("/importar")
    assert b"<dialog" in r.data and "Cómo preparar su archivo".encode() in r.data


def test_estaticos_y_menu_lateral(app):
    c, _ = registrar(app, "Clinica A", "a@a.com")
    css = c.get("/static/app.css")
    assert css.status_code == 200 and b"--grad" in css.data
    html = c.get("/panel").data
    for destino in (b'/importar', b'/alertas', b'/responsables'):
        assert destino in html
    assert b'class="act"' in html or b'act"' in html            # el item activo se marca
    assert b"Tu aprendizaje, sin l" in app.test_client().get("/login").data
