import config
import db
import mailer
from scripts import generar_alertas
from scripts.guardar_certificado import guardar_registro
from sqlalchemy import select


def _estados(engine):
    with engine.connect() as conn:
        return [(r.estado, r.destinatario) for r in conn.execute(select(db.log_alertas))]


def test_sin_registros_no_envia(engine, correo_configurado, monkeypatch):
    monkeypatch.setattr(mailer, "enviar_correo", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debía enviar")))
    assert generar_alertas.ejecutar_alerta("diaria") == "sin_registros"
    assert _estados(engine) == [("sin_registros", "rrhh@ejemplo.com")]


def test_envio_correcto_incluye_vencidos_y_registra_log(engine, correo_configurado, monkeypatch):
    guardar_registro(cedula="1001", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2020-01-01")
    enviados = []
    monkeypatch.setattr(mailer, "enviar_correo", lambda asunto, html, texto, dest=None: enviados.append((asunto, html, texto)))

    assert generar_alertas.ejecutar_alerta("diaria") == "enviado"
    asunto, html, texto = enviados[0]
    assert "VENCIDO" in html and "Ruiz" in html and "VENCIDO" in texto
    assert _estados(engine) == [("enviado", "rrhh@ejemplo.com")]


def test_fallo_de_envio_queda_registrado_y_no_revienta(engine, correo_configurado, monkeypatch):
    guardar_registro(cedula="1001", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2020-01-01")

    def falla(*a, **k):
        raise mailer.MailError("SMTP caído")

    monkeypatch.setattr(mailer, "enviar_correo", falla)
    assert generar_alertas.ejecutar_alerta("diaria") == "error_envio"
    assert _estados(engine) == [("error_envio", "rrhh@ejemplo.com")]


def test_sin_destinatario_configurado_no_pierde_el_log(engine, monkeypatch):
    """Regresión: antes, RRHH_EMAIL vacío rompía el INSERT (NOT NULL) y el fallo no quedaba registrado."""
    monkeypatch.setattr(config, "RRHH_EMAILS", [])
    guardar_registro(cedula="1001", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2020-01-01")
    assert generar_alertas.ejecutar_alerta("diaria") == "error_envio"
    assert _estados(engine) == [("error_envio", "(sin configurar)")]


def test_el_html_escapa_contenido_malicioso(engine, correo_configurado, monkeypatch):
    guardar_registro(cedula="1001", nombre="<script>alert(1)</script>", apellido="X", curso="RCP", fecha_vencimiento="2020-01-01")
    capturado = {}
    monkeypatch.setattr(mailer, "enviar_correo", lambda asunto, html, texto, dest=None: capturado.update(html=html))
    generar_alertas.ejecutar_alerta("diaria")
    assert "<script>" not in capturado["html"]
    assert "&lt;script&gt;" in capturado["html"]


def test_dry_run_guarda_html_y_no_envia(engine, tmp_path, monkeypatch):
    guardar_registro(cedula="1001", nombre="Ana", apellido="Ruiz", curso="RCP", fecha_vencimiento="2020-01-01")
    monkeypatch.setattr(mailer, "enviar_correo", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debía enviar")))
    salida = tmp_path / "vista.html"
    assert generar_alertas.ejecutar_alerta("semanal", dry_run=True, salida=salida) == "simulado"
    assert "Ruiz" in salida.read_text(encoding="utf-8")
    assert _estados(engine) == []


def test_tipo_invalido(engine):
    import pytest
    with pytest.raises(ValueError):
        generar_alertas.ejecutar_alerta("anual")
