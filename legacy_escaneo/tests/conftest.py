import os

import pytest

import config
import db


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    """Base aislada por prueba, conectada como motor global de la app.

    Por defecto SQLite temporal. Con TEST_DATABASE_URL=postgresql://... corre contra Postgres
    (se vacían las tablas antes de cada prueba: usa una base de pruebas, nunca la real).
    """
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        eng = db.crear_engine(config._normalizar_db_url(url))
        db.metadata.drop_all(eng)
    else:
        eng = db.crear_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    db.init_db(eng)
    monkeypatch.setattr(db, "_engine", eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def correo_configurado(monkeypatch):
    monkeypatch.setattr(config, "RRHH_EMAILS", ["rrhh@ejemplo.com"])
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "smtp")
    monkeypatch.setattr(config, "GMAIL_USER", "alertas@ejemplo.com")
    monkeypatch.setattr(config, "GMAIL_APP_PASSWORD", "x")
