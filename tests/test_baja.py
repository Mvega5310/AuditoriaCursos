"""Baja de avisos desde el correo: un enlace firmado POR DESTINATARIO, usable sin iniciar sesion.
Lo que se verifica sobre todo: dar de baja una direccion NO afecta a los demas responsables
de su empresa ni a ninguna otra empresa.
"""
import io
import re
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

CLAVE = "x" * 32                 # el mismo SECRET_KEY que usa la fixture `app`
H = core.hoy()


def _excel():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Documento", "Fecha Vencimiento"])
    ws.append(["101", "Ana", "Uno", "Enfermera", "BLS", (H + timedelta(days=3)).strftime("%d/%m/%Y")])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _capturar(monkeypatch) -> list:
    """Sustituye el envio real. Devuelve la lista de (destinatario, html) que se habria enviado."""
    enviados = []
    monkeypatch.setattr(config, "FLASK_SECRET_KEY", CLAVE)
    monkeypatch.setattr(generar_alertas, "enviar_email",
                        lambda asunto, html, para, cco=None: enviados.append((para[0], html)) or True)
    return enviados


def _ruta_baja(html: str) -> str:
    m = re.search(r"/baja/([\w\-\.]+)", html)
    assert m, "el correo no trae enlace de baja"
    return "/baja/" + m.group(1)


def _escenario(app, monkeypatch):
    """Hospital Z con dos responsables + Fabrica B con uno. Devuelve los correos enviados."""
    c, _ = registrar(app, "Hospital Z", "jefe@z.com")
    subir(c, _excel())
    c.post("/responsables", data={"csrf": _csrf(c, "/responsables"), "nombre": "Otro", "email": "otro@z.com"})
    c2, _ = registrar(app, "Fabrica B", "jefe@b.com")
    subir(c2, _excel())
    enviados = _capturar(monkeypatch)
    generar_alertas.ejecutar_alerta("semanal", db_path=app.config["DB_PATH"])
    return enviados


def _activos(app) -> set:
    with core.conectar(app.config["DB_PATH"]) as con:
        return {f["email"] for f in con.execute("SELECT email FROM responsables WHERE activo = 1")}


def test_un_correo_por_destinatario_con_su_propio_enlace(app, monkeypatch):
    enviados = _escenario(app, monkeypatch)
    # un correo por persona, no uno solo con todas las direcciones en el "Para"
    assert sorted(e for e, _ in enviados) == ["jefe@b.com", "jefe@z.com", "otro@z.com"]
    tokens = {e: _ruta_baja(h) for e, h in enviados}
    assert len(set(tokens.values())) == 3                      # un token distinto por responsable


def test_el_get_no_da_de_baja(app, monkeypatch):
    """El prefetch de Gmail y los escaneres corporativos abren los enlaces solos."""
    enviados = _escenario(app, monkeypatch)
    ruta = _ruta_baja(next(h for e, h in enviados if e == "jefe@z.com"))
    r = app.test_client().get(ruta)                            # sin iniciar sesion
    assert r.status_code == 200 and "¿Dejar de recibir" in r.data.decode()
    assert _activos(app) == {"jefe@z.com", "otro@z.com", "jefe@b.com"}


def test_la_baja_solo_afecta_a_quien_la_pide(app, monkeypatch):
    enviados = _escenario(app, monkeypatch)
    ruta = _ruta_baja(next(h for e, h in enviados if e == "jefe@z.com"))

    publico = app.test_client()
    publico.get(ruta)
    with publico.session_transaction() as s:
        csrf = s["csrf"]
    r = publico.post(ruta, data={"csrf": csrf})
    assert r.status_code == 200 and "se dio de baja" in r.data.decode()

    assert _activos(app) == {"otro@z.com", "jefe@b.com"}       # su companero y la otra empresa, intactos

    enviados.clear()                                           # y deja de recibir en el siguiente envio
    generar_alertas.ejecutar_alerta("semanal", db_path=app.config["DB_PATH"])
    assert sorted(e for e, _ in enviados) == ["jefe@b.com", "otro@z.com"]


def test_token_falso_o_manipulado_no_sirve(app, monkeypatch):
    monkeypatch.setattr(config, "FLASK_SECRET_KEY", CLAVE)
    c = app.test_client()
    assert c.get("/baja/esto-no-esta-firmado").status_code == 404
    assert c.post("/baja/esto-no-esta-firmado", data={"csrf": _csrf(c, "/login")}).status_code == 404


def test_sin_secreto_no_se_firma_ningun_enlace(monkeypatch):
    monkeypatch.setattr(config, "FLASK_SECRET_KEY", None)
    assert generar_alertas.enlace_baja(1) is None
    assert generar_alertas.enlace_baja(None) is None           # respaldo RRHH_EMAIL: no hay fila que dar de baja
    assert "/baja/" not in generar_alertas.renderizar_html([], "semanal", "X", [])
