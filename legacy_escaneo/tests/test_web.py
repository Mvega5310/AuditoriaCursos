import io
import re

import pytest
from sqlalchemy import func, select

import config
import db
from app_escaneo import create_app
from scripts.extraer_certificado import CertificadoExtraido, ErrorExtraccion

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture()
def app(engine):
    return create_app({"TESTING": True, "SECRET_KEY": "x" * 40, "ACCESS_PASSWORD": "clave-segura-1"})


@pytest.fixture()
def cliente(app):
    return app.test_client()


def _csrf(cliente, ruta="/login"):
    html = cliente.get(ruta).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


@pytest.fixture()
def sesion(cliente):
    token = _csrf(cliente)
    r = cliente.post("/login", data={"password": "clave-segura-1", "csrf_token": token})
    assert r.status_code == 302
    return cliente


def test_sin_login_redirige(cliente):
    for ruta in ("/", "/manual"):
        r = cliente.get(ruta)
        assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert cliente.post("/guardar", data={}).status_code == 302


def test_healthz_es_publico(cliente):
    r = cliente.get("/healthz")
    assert r.status_code == 200 and r.json == {"status": "ok"}


def test_login_incorrecto_y_bloqueo_por_intentos(cliente):
    token = _csrf(cliente)
    for _ in range(5):
        r = cliente.post("/login", data={"password": "mala", "csrf_token": token})
        assert r.status_code == 200 and "incorrecta" in r.get_data(as_text=True)
    r = cliente.post("/login", data={"password": "clave-segura-1", "csrf_token": token})
    assert r.status_code == 429  # bloqueado aunque la clave ahora sea correcta


def test_post_sin_csrf_se_rechaza(cliente):
    r = cliente.post("/login", data={"password": "clave-segura-1"})
    assert r.status_code == 400


def test_cabeceras_de_seguridad(cliente):
    r = cliente.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"


def test_flujo_completo_extraer_y_guardar(sesion, engine, monkeypatch):
    monkeypatch.setattr("app_escaneo.extraer_datos_certificado", lambda datos: CertificadoExtraido(
        cedula="1234567", nombre="Ana", apellido="Ruiz", curso="RCP",
        fecha_realizacion="2026-01-10", confianza=0.6))
    token = _csrf(sesion, "/")
    r = sesion.post("/extraer", data={"csrf_token": token, "certificado": (io.BytesIO(PNG), "c.png")},
                    content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Lectura dudosa" in html and 'value="1234567"' in html

    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    r = sesion.post("/guardar", data={"csrf_token": token, "cedula": "1234567", "nombre": "Ana", "apellido": "Ruiz",
                                      "cargo": "", "area": "", "curso": "RCP",
                                      "fecha_realizacion": "2026-01-10", "fecha_vencimiento": ""})
    assert r.status_code == 200 and "registrado correctamente" in r.get_data(as_text=True)
    with engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(db.empleado_cursos)) == 1


def test_guardar_con_datos_invalidos_conserva_el_formulario(sesion, engine):
    token = _csrf(sesion, "/")
    r = sesion.post("/guardar", data={"csrf_token": token, "cedula": "12", "nombre": "Ana", "apellido": "Ruiz",
                                      "curso": "RCP", "fecha_vencimiento": "2026-12-01"})
    html = r.get_data(as_text=True)
    assert r.status_code == 422 and "cédula" in html and 'value="Ana"' in html


def test_extraer_error_de_servicio_se_muestra_sin_detalles_internos(sesion, monkeypatch):
    def falla(datos):
        raise RuntimeError("clave sk-ant-SECRETA rota")

    monkeypatch.setattr("app_escaneo.extraer_datos_certificado", falla)
    token = _csrf(sesion, "/")
    r = sesion.post("/extraer", data={"csrf_token": token, "certificado": (io.BytesIO(PNG), "c.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "No se pudo leer" in html and "SECRETA" not in html


def test_formato_no_soportado(sesion, monkeypatch):
    token = _csrf(sesion, "/")
    r = sesion.post("/extraer", data={"csrf_token": token, "certificado": (io.BytesIO(b"MZ-ejecutable"), "virus.pdf")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Formato no soportado" in r.get_data(as_text=True)


def test_archivo_demasiado_grande(app, sesion):
    app.config["MAX_CONTENT_LENGTH"] = 1024
    token = _csrf(sesion, "/")
    r = sesion.post("/extraer", data={"csrf_token": token, "certificado": (io.BytesIO(PNG + b"0" * 5000), "c.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 413 and "supera el máximo" in r.get_data(as_text=True)


def test_logout_cierra_la_sesion(sesion):
    token = _csrf(sesion, "/")
    assert sesion.post("/logout", data={"csrf_token": token}).status_code == 302
    assert sesion.get("/").status_code == 302


def test_validar_config_exige_secretos_en_produccion(monkeypatch):
    monkeypatch.setattr(config, "IS_PROD", True)
    monkeypatch.setattr(config, "FLASK_SECRET_KEY", "corta")
    monkeypatch.setattr(config, "ACCESS_PASSWORD", "")
    with pytest.raises(config.ConfigError) as e:
        config.validar_config(web=True)
    assert "FLASK_SECRET_KEY" in str(e.value) and "ACCESS_PASSWORD" in str(e.value)
