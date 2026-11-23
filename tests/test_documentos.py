"""Ver, agregar, editar y eliminar documentos desde la web. Lo critico: nadie toca documentos de otra empresa."""
import io
import sys
from datetime import timedelta
from pathlib import Path

import openpyxl

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import core                      # noqa: E402
import generar_alertas           # noqa: E402
from test_web import app, registrar, subir, _csrf   # noqa: E402,F401

H = core.hoy()


def _f(d):
    return (H + timedelta(days=d)).strftime("%d/%m/%Y")


def _excel(filas):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documentos"
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Documento", "Fecha Vencimiento"])
    for f in filas:
        ws.append(f)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _ids(app, empresa):
    with core.conectar(app.config["DB_PATH"]) as con:
        return [r["id"] for r in con.execute(
            "SELECT d.id FROM documentos d JOIN empresas e ON e.id = d.empresa_id WHERE e.nombre = ? ORDER BY d.id", (empresa,))]


def _doc(app, did):
    with core.conectar(app.config["DB_PATH"]) as con:
        return con.execute("SELECT * FROM documentos WHERE id = ?", (did,)).fetchone()


def _post(c, ruta, **datos):
    return c.post(ruta, data={"csrf": _csrf(c, "/documentos"), **datos})


def _dos_empresas(app):
    a, _ = registrar(app, "Clinica A", "a@a.com")
    subir(a, _excel([["101", "Ana", "Uno", "Enfermera", "BLS", _f(-5)]]))
    b, _ = registrar(app, "Fabrica B", "b@b.com")
    subir(b, _excel([["201", "Beto", "Dos", "Operario", "Alturas", _f(10)]]))
    return a, b, _ids(app, "Clinica A")[0], _ids(app, "Fabrica B")[0]


def test_lista_y_busqueda_solo_de_la_propia_empresa(app):
    a, b, _, _ = _dos_empresas(app)
    html = a.get("/documentos").data.decode()
    assert "Uno, Ana" in html and "BLS" in html and "Dos, Beto" not in html
    assert "Uno, Ana" in a.get("/documentos?q=101").data.decode()                    # por cedula
    assert "Uno, Ana" in a.get("/documentos?q=bls").data.decode()                    # sin importar mayusculas
    assert "Uno, Ana" not in a.get("/documentos?q=inexistente").data.decode()


def test_editar_cambia_la_fecha_y_guarda_la_version_anterior(app):
    a, _, did, _ = _dos_empresas(app)
    antes = _doc(app, did)["fecha_vencimiento"]
    r = _post(a, f"/documentos/{did}", fecha_vencimiento=_f(300), fecha_emision="", entidad="Cruz Roja")
    assert r.status_code == 302
    d = _doc(app, did)
    assert d["fecha_vencimiento"] == (H + timedelta(days=300)).isoformat() and d["entidad_emisora"] == "Cruz Roja"
    with core.conectar(app.config["DB_PATH"]) as con:
        h = con.execute("SELECT fecha_vencimiento FROM documentos_historial WHERE documento_id = ?", (did,)).fetchall()
    assert [x["fecha_vencimiento"] for x in h] == [antes]                            # la version vieja se conserva
    assert "Cruz Roja" in a.get(f"/documentos/{did}").data.decode()


def test_no_se_puede_ver_editar_ni_eliminar_un_documento_de_otra_empresa(app):
    a, b, did_a, _ = _dos_empresas(app)
    antes = dict(_doc(app, did_a))
    assert b.get(f"/documentos/{did_a}").status_code == 404
    assert _post(b, f"/documentos/{did_a}", fecha_vencimiento=_f(900)).status_code == 404
    assert _post(b, f"/documentos/{did_a}/eliminar").status_code == 404
    assert _post(b, f"/documentos/{did_a}/persona", accion="retirar").status_code == 404
    assert dict(_doc(app, did_a)) == antes                                           # intacto


def test_validaciones_no_guardan_nada(app):
    a, _, did, _ = _dos_empresas(app)
    antes = dict(_doc(app, did))
    for datos in ({"fecha_vencimiento": "31/02/2027"},                                # fecha imposible
                  {"fecha_emision": _f(50), "fecha_vencimiento": _f(10)},             # vence antes de emitirse
                  {"fecha_vencimiento": "01/01/2015"},                                # error de digitacion
                  {"fecha_vencimiento": _f(10), "avisar_dias": "abc"}):
        assert _post(a, f"/documentos/{did}", **datos).status_code == 400
    assert dict(_doc(app, did)) == antes


def test_vigencia_indefinida_desde_la_web_apaga_la_alerta(app):
    a, _, did, _ = _dos_empresas(app)
    with core.conectar(app.config["DB_PATH"]) as con:
        assert len(generar_alertas.consultar_vencimientos(con, 1, 60)) == 1          # vencido: alerta
    _post(a, f"/documentos/{did}", fecha_vencimiento=_f(-5), vigencia_indefinida="1")
    with core.conectar(app.config["DB_PATH"]) as con:
        assert generar_alertas.consultar_vencimientos(con, 1, 60) == []              # pago unico: sin alerta
    assert _doc(app, did)["fecha_vencimiento"] == (H - timedelta(days=5)).isoformat()   # y conserva la fecha impresa


def test_no_aplica_borra_las_fechas(app):
    a, _, did, _ = _dos_empresas(app)
    _post(a, f"/documentos/{did}", fecha_vencimiento=_f(10), no_aplica="1")
    d = _doc(app, did)
    assert (d["no_aplica"], d["fecha_vencimiento"]) == (1, None)


def test_eliminar_es_solo_de_administradores(app):
    a, _, did, _ = _dos_empresas(app)
    with core.conectar(app.config["DB_PATH"]) as con:
        con.execute("UPDATE usuarios SET rol = 'usuario' WHERE email = 'a@a.com'")
        con.commit()
    assert _post(a, f"/documentos/{did}/eliminar").status_code == 403
    assert _doc(app, did) is not None
    assert "Eliminar documento" not in a.get(f"/documentos/{did}").data.decode()     # ni siquiera ve el boton
    with core.conectar(app.config["DB_PATH"]) as con:
        con.execute("UPDATE usuarios SET rol = 'admin' WHERE email = 'a@a.com'")
        con.commit()
    assert _post(a, f"/documentos/{did}/eliminar").status_code == 302
    assert _doc(app, did) is None


def test_retirar_y_reactivar_a_una_persona(app):
    a, _, did, _ = _dos_empresas(app)
    _post(a, f"/documentos/{did}/persona", accion="retirar")
    with core.conectar(app.config["DB_PATH"]) as con:
        assert generar_alertas.consultar_vencimientos(con, 1, 60) == []              # retirada: no avisa
    _post(a, f"/documentos/{did}/persona", accion="reactivar")
    with core.conectar(app.config["DB_PATH"]) as con:
        assert len(generar_alertas.consultar_vencimientos(con, 1, 60)) == 1


def test_agregar_a_una_persona_existente_y_a_la_empresa(app):
    a, _, _, _ = _dos_empresas(app)
    r = _post(a, "/documentos/nuevo", cedula="101", documento="RCP", fecha_vencimiento=_f(20))
    assert r.status_code == 302
    r = _post(a, "/documentos/nuevo", cedula="", documento="Poliza todo riesgo", referencia="POL-1", fecha_vencimiento=_f(40))
    assert r.status_code == 302
    html = a.get("/documentos").data.decode()
    assert "RCP" in html and "Poliza todo riesgo" in html and "EMPRESA" in html


def test_agregar_rechaza_personas_de_otra_empresa_y_datos_malos(app):
    a, _, _, _ = _dos_empresas(app)
    antes = len(_ids(app, "Clinica A"))
    assert _post(a, "/documentos/nuevo", cedula="201", documento="RCP", fecha_vencimiento=_f(20)).status_code == 400   # cedula de B
    assert _post(a, "/documentos/nuevo", cedula="101", documento="", fecha_vencimiento=_f(20)).status_code == 400
    assert _post(a, "/documentos/nuevo", cedula="101", documento="RCP", fecha_vencimiento="zzz").status_code == 400
    assert len(_ids(app, "Clinica A")) == antes


def test_agregar_con_la_misma_referencia_actualiza_en_vez_de_duplicar(app):
    a, _, did, _ = _dos_empresas(app)
    _post(a, "/documentos/nuevo", cedula="101", documento="BLS", fecha_vencimiento=_f(200))
    assert _ids(app, "Clinica A") == [did]
    assert _doc(app, did)["fecha_vencimiento"] == (H + timedelta(days=200)).isoformat()


def test_requiere_iniciar_sesion(app):
    c = app.test_client()
    for ruta in ("/documentos", "/documentos/nuevo", "/documentos/1"):
        assert c.get(ruta).status_code == 302
