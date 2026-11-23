"""Vista de administrador del sistema: la unica que cruza empresas, asi que el acceso es lo que se prueba."""
import io
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
from test_web import app, registrar, subir   # noqa: E402,F401

H = core.hoy()


def _excel(cedula, nombre):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documentos"
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Documento", "Fecha Vencimiento"])
    ws.append([cedula, nombre, "Apellido", "Enfermera", "BLS", (H - timedelta(days=3)).strftime("%d/%m/%Y")])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _escenario(app):
    dueno, _ = registrar(app, "Clinica A", "dueno@a.com")
    subir(dueno, _excel("1010101", "Anita"))
    cliente, _ = registrar(app, "Fabrica B", "jefe@b.com")
    subir(cliente, _excel("2020202", "Betico"))
    app.config["SUPERADMIN_EMAILS"] = {"dueno@a.com"}
    return dueno, cliente


def test_un_usuario_normal_no_ve_ni_sabe_que_existe(app):
    dueno, cliente = _escenario(app)
    assert cliente.get("/admin").status_code == 404
    assert "Administración" not in cliente.get("/panel").data.decode()               # ni el enlace del menu


def test_sin_superadmins_configurados_nadie_entra(app):
    dueno, _ = _escenario(app)
    app.config["SUPERADMIN_EMAILS"] = set()
    assert dueno.get("/admin").status_code == 404


def test_sin_sesion_pide_ingresar(app):
    assert app.test_client().get("/admin").status_code == 302


def test_el_superadmin_ve_todas_las_empresas_y_quien_recibe_los_avisos(app, monkeypatch):
    dueno, _ = _escenario(app)
    monkeypatch.setattr(generar_alertas, "enviar_email", lambda *a, **k: True)
    generar_alertas.ejecutar_alerta("semanal", db_path=app.config["DB_PATH"])
    html = dueno.get("/admin").data.decode()
    assert "Clinica A" in html and "Fabrica B" in html
    assert "dueno@a.com" in html and "jefe@b.com" in html                             # responsables de cada una
    assert "Administración" in dueno.get("/panel").data.decode()                      # el enlace del menu
    assert ">2</b>Envíos, últimos 7 días" in html                                      # un correo por empresa


def test_no_expone_datos_de_las_personas(app):
    dueno, _ = _escenario(app)
    html = dueno.get("/admin").data.decode()
    for secreto in ("1010101", "2020202", "Anita", "Betico"):
        assert secreto not in html


def test_avisa_de_las_empresas_que_no_recibiran_nada(app, monkeypatch):
    dueno, _ = _escenario(app)
    monkeypatch.setattr(config, "RRHH_EMAIL", None)
    with core.conectar(app.config["DB_PATH"]) as con:
        con.execute("UPDATE responsables SET activo = 0 WHERE email = 'jefe@b.com'")
        con.commit()
    html = dueno.get("/admin").data.decode()
    assert "No recibirán ningún aviso" in html and "Fabrica B" in html


def test_muestra_a_donde_van_la_copia_y_el_respaldo(app, monkeypatch):
    dueno, _ = _escenario(app)
    monkeypatch.setattr(config, "ADMIN_EMAIL", "copia@mia.com")
    monkeypatch.setattr(config, "RRHH_EMAIL", "respaldo@mia.com")
    html = dueno.get("/admin").data.decode()
    assert "copia@mia.com" in html and "respaldo@mia.com" in html
