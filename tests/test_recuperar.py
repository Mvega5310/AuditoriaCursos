"""Recuperar contrasena: enlace de un solo uso, que vence, sin revelar si la cuenta existe."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import web                                           # noqa: E402
from test_web import app, registrar, _csrf, CLAVE   # noqa: E402,F401

NUEVA = "otra-clave-larga-456"


def _pedir(app, email):
    c = app.test_client()
    r = c.post("/olvide", data={"csrf": _csrf(c, "/olvide"), "email": email}, follow_redirects=True)
    return c, r


def _ruta(app):
    return app.config["ULTIMO_ENLACE_RESTABLECER"].replace(app.config["APP_URL"], "")


def _cambiar(app, ruta, clave=NUEVA, clave2=None):
    c = app.test_client()
    return c.post(ruta, data={"csrf": _csrf(c), "password": clave, "password2": clave2 or clave})


def _entra(app, email, clave):
    c = app.test_client()
    return c.post("/login", data={"csrf": _csrf(c), "email": email, "password": clave}).status_code == 302


def test_flujo_completo(app):
    registrar(app, "Hospital Z", "z@z.com")
    _pedir(app, "z@z.com")
    r = _cambiar(app, _ruta(app))
    assert r.status_code == 302
    assert _entra(app, "z@z.com", NUEVA) and not _entra(app, "z@z.com", CLAVE)


def test_el_enlace_sirve_una_sola_vez(app):
    registrar(app, "Hospital Z", "z@z.com")
    _pedir(app, "z@z.com")
    ruta = _ruta(app)
    assert _cambiar(app, ruta).status_code == 302
    assert _cambiar(app, ruta, "tercera-clave-789").status_code == 302      # redirige a /olvide: ya no sirve
    assert not _entra(app, "z@z.com", "tercera-clave-789")                  # y NO cambio la clave
    assert _entra(app, "z@z.com", NUEVA)


def test_misma_respuesta_exista_o_no_la_cuenta(app):
    registrar(app, "Hospital Z", "z@z.com")
    _, existe = _pedir(app, "z@z.com")
    app.config["ULTIMO_ENLACE_RESTABLECER"] = None
    _, no_existe = _pedir(app, "nadie@z.com")
    assert existe.status_code == no_existe.status_code == 200
    assert b"Si ese correo tiene una cuenta" in existe.data and b"Si ese correo tiene una cuenta" in no_existe.data
    assert app.config["ULTIMO_ENLACE_RESTABLECER"] is None                   # a quien no existe no se le genera enlace


def test_token_vencido_o_falso(app, monkeypatch):
    registrar(app, "Hospital Z", "z@z.com")
    _pedir(app, "z@z.com")
    ruta = _ruta(app)
    assert app.test_client().get("/restablecer/falso").status_code == 302
    monkeypatch.setattr(web, "RESTABLECER_SEG", -1)                          # todo token ya esta vencido
    assert _cambiar(app, ruta).status_code == 302
    assert _entra(app, "z@z.com", CLAVE)                                     # la clave no cambio


def test_valida_la_clave_nueva(app):
    registrar(app, "Hospital Z", "z@z.com")
    _pedir(app, "z@z.com")
    ruta = _ruta(app)
    assert _cambiar(app, ruta, "corta").status_code == 400
    assert _cambiar(app, ruta, NUEVA, "distinta-clave-1").status_code == 400
    assert _entra(app, "z@z.com", CLAVE)


def test_limite_de_solicitudes(app):
    c = app.test_client()
    codigos = [c.post("/olvide", data={"csrf": _csrf(c, "/olvide"), "email": f"x{i}@z.com"}).status_code
               for i in range(7)]
    assert 429 in codigos


def test_cierra_las_sesiones_abiertas_en_otros_dispositivos(app):
    """Quien tenia la clave vieja (p. ej. un telefono perdido) queda fuera en cuanto se restablece."""
    otro_dispositivo, _ = registrar(app, "Hospital Z", "z@z.com")
    assert otro_dispositivo.get("/panel").status_code == 200
    _pedir(app, "z@z.com")
    _cambiar(app, _ruta(app))
    assert otro_dispositivo.get("/panel").status_code == 302          # lo manda a ingresar de nuevo
