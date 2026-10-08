"""
Las pruebas corren sobre SQLite (por defecto) o sobre Postgres si se define TEST_DATABASE_URL:

  TEST_DATABASE_URL=postgresql://usuario:clave@localhost:5432/prueba python -m pytest -q

En Postgres cada prueba parte de un esquema vacio. Las marcadas @pytest.mark.solo_sqlite
(migracion desde v1, varias bases a la vez) se omiten en ese modo.
"""
import os

import pytest

import core


def pytest_configure(config):
    config.addinivalue_line("markers", "solo_sqlite: usa archivos .db o la migracion v1 (no aplica a Postgres)")


@pytest.fixture(autouse=True)
def _motor_de_base_de_datos(request, monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        monkeypatch.setattr(core, "DATABASE_URL", None)
        yield
        return
    if request.node.get_closest_marker("solo_sqlite"):
        pytest.skip("solo aplica a SQLite")
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    monkeypatch.setattr(core, "DATABASE_URL", url)
    yield
