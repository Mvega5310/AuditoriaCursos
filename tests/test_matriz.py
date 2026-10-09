"""Pruebas de la importacion en formato matriz y de los requisitos por cargo (faltantes)."""
import sys
from datetime import timedelta
from pathlib import Path

import openpyxl

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import core                      # noqa: E402
import importar_excel            # noqa: E402
import generar_alertas           # noqa: E402
import agregar_documento         # noqa: E402

H = core.hoy()


def _f(dias):
    return (H + timedelta(days=dias)).strftime("%d/%m/%Y")


def _excel(tmp_path, hojas):
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for nombre, filas in hojas.items():
        ws = wb.create_sheet(nombre)
        for f in filas:
            ws.append(f)
    ruta = tmp_path / "m.xlsx"
    wb.save(ruta)
    return ruta


def _matriz():
    return [
        ["Empresa", "Cedula", "Nombre completo", "Cargo", "RCP | 730", "Bioseguridad | 365", "Observaciones"],
        ["Hospital U", "1", "Ana Perez", "Enfermera", _f(-100), _f(-400), "ok"],      # RCP vigente; bioseg vencida
        ["Hospital U", "2", "Luis Ruiz", "Auxiliar de enfermeria", _f(-10), "N/A", ""],
        ["Hospital U", "3", "Eva Gil", "Enfermera", "", "", ""],
    ]


def test_matriz_calcula_vencimientos_y_omite_vacios(tmp_path):
    db = tmp_path / "t.db"
    s = importar_excel.importar(_excel(tmp_path, {"Matriz": _matriz()}), db_path=db, verbose=False)
    assert s["nuevos"] == 3 and s["errores"] == [] and s["personas_matriz"] == 3
    assert s["columnas_ignoradas"] == []
    conn = core.conectar(db)
    f = conn.execute("""SELECT d.fecha_vencimiento FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
                        JOIN empleados e ON e.id = d.empleado_id WHERE e.cedula = '1' AND t.nombre = 'RCP'""").fetchone()
    assert f[0] == (H + timedelta(days=-100 + 730)).isoformat()
    # nombre completo sin apellido: se guarda tal cual
    assert conn.execute("SELECT nombre, apellido FROM empleados WHERE cedula = '1'").fetchone()[:] == ("Ana Perez", "")
    conn.close()


def test_matriz_sin_vigencia_se_omite_columna_y_fechas_vencimiento(tmp_path):
    db = tmp_path / "t.db"
    filas = [["Cedula", "Nombre", "Curso X"], ["9", "Pedro Mar", _f(-5)]]
    s = importar_excel.importar(_excel(tmp_path, {"Matriz": filas}), db_path=db, verbose=False)
    assert s["nuevos"] == 0 and "vigencia" in s["errores"][0]
    s = importar_excel.importar(_excel(tmp_path, {"Matriz": filas}), db_path=db, verbose=False, vigencia_defecto=100)
    assert s["nuevos"] == 1
    db2 = tmp_path / "t2.db"
    importar_excel.importar(_excel(tmp_path, {"Matriz": filas}), db_path=db2, verbose=False, fechas="vencimiento")
    conn = core.conectar(db2)
    assert conn.execute("SELECT fecha_vencimiento FROM documentos").fetchone()[0] == (H - timedelta(days=5)).isoformat()
    conn.close()


def test_faltantes_por_cargo(tmp_path):
    db = tmp_path / "t.db"
    ruta = _excel(tmp_path, {
        "Matriz": _matriz(),
        "Requisitos": [["Empresa", "Cargo", "Documento"],
                       ["Hospital U", "Enfermera", "RCP"],
                       ["Hospital U", "Enfermera", "Bioseguridad"],
                       ["Hospital U", "Auxiliar de enfermeria", "RCP"],
                       ["Hospital U", "Auxiliar de enfermeria", "Bioseguridad"],
                       ["Hospital U", "*", "Humanizacion del servicio"]],
    })
    s = importar_excel.importar(ruta, db_path=db, verbose=False)
    assert s["requisitos"] == 5
    conn = core.conectar(db)
    eid = conn.execute("SELECT id FROM empresas").fetchone()[0]
    falt = {f["identificacion"]: f for f in generar_alertas.consultar_faltantes(conn, eid)}
    assert falt["1"]["faltan"] == "Humanizacion del servicio"                      # Ana: solo el de todos
    assert falt["2"]["faltan"] == "Humanizacion del servicio"                      # Luis: Bioseguridad 'N/A' = no le aplica
    assert falt["3"]["cantidad"] == 3                                              # Eva: nada registrado
    html = generar_alertas.renderizar_html([], "semanal", "Hospital U", list(falt.values()))
    assert "sin registrar" in html and "Eva Gil" in html
    conn.close()


def test_comando_requisito_y_cargo_con_tildes(tmp_path, capsys):
    db = tmp_path / "t.db"
    assert agregar_documento.main(["requisito", "--empresa", "Clinica", "--cargo", "Auxiliar de Enfermería",
                                   "--documentos", "RCP; Bioseguridad"], db_path=db) == 0
    assert agregar_documento.main(["requisitos", "--empresa", "Clinica"], db_path=db) == 0
    assert "2 requisitos" in capsys.readouterr().out
    importar_excel.importar(_excel(tmp_path, {"D": [["Empresa", "Cedula", "Nombre", "Cargo", "Documento", "Fecha Vencimiento"],
                                                    ["Clinica", "5", "Mia Soto", "auxiliar de enfermeria", "RCP", _f(200)]]}),
                            db_path=db, verbose=False)
    conn = core.conectar(db)
    eid = conn.execute("SELECT id FROM empresas").fetchone()[0]
    assert [f["faltan"] for f in generar_alertas.consultar_faltantes(conn, eid)] == ["Bioseguridad"]
    conn.close()
    assert agregar_documento.main(["requisito", "--cargo", "X", "--documentos", "Y", "--categoria", "zzz"], db_path=db) == 1
