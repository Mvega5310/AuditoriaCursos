"""Pruebas del envio de correo (sin red: se simula urllib / smtplib)."""
import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config  # noqa: E402
import correo  # noqa: E402


@pytest.fixture(autouse=True)
def _sin_esperas(monkeypatch):
    monkeypatch.setattr(correo.time, "sleep", lambda s: None)


def _resend(monkeypatch):
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(config, "EMAIL_FROM", "autCursos <alertas@dominio.com>")


def test_proveedor_prefiere_resend(monkeypatch):
    monkeypatch.setattr(config, "GMAIL_USER", "a@gmail.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "x")
    monkeypatch.setattr(config, "RESEND_API_KEY", None)
    assert correo.proveedor() == "smtp"
    _resend(monkeypatch)
    assert correo.proveedor() == "resend"
    monkeypatch.setattr(config, "GMAIL_USER", None)
    monkeypatch.setattr(config, "RESEND_API_KEY", None)
    assert correo.proveedor() is None and correo.enviar("a", "b", ["x@y.com"]) is False


def test_resend_arma_la_peticion(monkeypatch):
    _resend(monkeypatch)
    visto = {}

    def falso(req, timeout):
        visto["url"], visto["auth"] = req.full_url, req.headers["Authorization"]
        visto["cuerpo"] = json.loads(req.data)
        return io.BytesIO()

    monkeypatch.setattr(correo.urllib.request, "urlopen", falso)
    assert correo.enviar("Asunto", "<p>hola</p>", ["a@x.com"], ["admin@x.com", "a@x.com"]) is True
    assert visto["url"] == "https://api.resend.com/emails" and visto["auth"] == "Bearer re_test"
    assert visto["cuerpo"]["to"] == ["a@x.com"] and visto["cuerpo"]["bcc"] == ["admin@x.com"]


def _http(codigo):
    return urllib.error.HTTPError("u", codigo, "e", {}, io.BytesIO(b"{}"))


def test_resend_reintenta_5xx_pero_no_4xx(monkeypatch):
    _resend(monkeypatch)
    llamadas = []

    def falla(codigo):
        def _f(req, timeout):
            llamadas.append(codigo)
            raise _http(codigo)
        return _f

    monkeypatch.setattr(correo.urllib.request, "urlopen", falla(503))
    assert correo.enviar("a", "b", ["x@y.com"]) is False and len(llamadas) == correo.INTENTOS
    llamadas.clear()
    monkeypatch.setattr(correo.urllib.request, "urlopen", falla(403))     # dominio no verificado, etc.
    assert correo.enviar("a", "b", ["x@y.com"]) is False and len(llamadas) == 1
