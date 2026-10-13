"""Marca Vencimientos: logo, favicon, manifiesto e imagen en el correo."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import generar_alertas           # noqa: E402
from test_web import app, registrar   # noqa: E402,F401


def test_logo_favicon_y_manifiesto(app):
    c = app.test_client()
    html = c.get("/login").data.decode()
    for ruta in ("marca/simbolo.svg", "marca/favicon.svg", "marca/favicon-32.png", "marca/apple-touch-icon.png",
                 "marca/manifest.webmanifest"):
        assert ruta in html
        r = c.get("/static/" + ruta)
        assert r.status_code == 200 and "max-age=86400" in r.headers["Cache-Control"]
    assert c.get("/static/marca/manifest.webmanifest").mimetype == "application/manifest+json"
    assert c.get("/login").headers["Cache-Control"] == "no-store"            # las paginas siguen sin cache
    for icono in ("icono-192.png", "icono-512.png", "icono-correo.png"):
        assert c.get("/static/marca/" + icono).status_code == 200


def test_correo_lleva_el_logo_con_url_absoluta():
    html = generar_alertas.renderizar_html([], "semanal", "Hospital Z", [])
    assert "/static/marca/icono-correo.png" in html and 'alt="Vencimientos"' in html
    assert 'src="http' in html                                            # los correos no aceptan rutas relativas
