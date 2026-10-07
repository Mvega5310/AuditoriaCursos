import json
import urllib.error

import pytest

import config
import mailer


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(mailer.time, "sleep", lambda s: None)


def test_sin_destinatarios(monkeypatch):
    monkeypatch.setattr(config, "RRHH_EMAILS", [])
    with pytest.raises(mailer.MailError, match="RRHH_EMAIL"):
        mailer.enviar_correo("a", "<p>h</p>", "t")


def test_proveedor_desconocido(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "pigeon")
    with pytest.raises(mailer.MailError, match="no soportado"):
        mailer.enviar_correo("a", "<p>h</p>", "t", ["x@y.com"])


def test_resend_arma_la_peticion_correcta(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(config, "EMAIL_FROM", "Alertas <a@dominio.com>")
    capturado = {}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def falso_urlopen(req, timeout):
        capturado["url"] = req.full_url
        capturado["auth"] = req.get_header("Authorization")
        capturado["body"] = json.loads(req.data)
        return Resp()

    monkeypatch.setattr(mailer.urllib.request, "urlopen", falso_urlopen)
    mailer.enviar_correo("Asunto", "<p>h</p>", "t", ["rrhh@x.com", "otro@x.com"])
    assert capturado["url"] == "https://api.resend.com/emails"
    assert capturado["auth"] == "Bearer re_test"
    assert capturado["body"]["to"] == ["rrhh@x.com", "otro@x.com"]
    assert capturado["body"]["subject"] == "Asunto"


def test_resend_reintenta_5xx_pero_no_4xx(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "resend")
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(config, "EMAIL_FROM", "a@dominio.com")
    llamadas = []

    def error(codigo):
        def _f(req, timeout):
            llamadas.append(codigo)
            raise urllib.error.HTTPError(req.full_url, codigo, "x", {}, None)
        return _f

    monkeypatch.setattr(mailer.urllib.request, "urlopen", error(503))
    with pytest.raises(mailer.MailError):
        mailer.enviar_correo("a", "h", "t", ["x@y.com"])
    assert len(llamadas) == mailer.INTENTOS

    llamadas.clear()
    monkeypatch.setattr(mailer.urllib.request, "urlopen", error(403))
    with pytest.raises(mailer.MailError):
        mailer.enviar_correo("a", "h", "t", ["x@y.com"])
    assert len(llamadas) == 1
