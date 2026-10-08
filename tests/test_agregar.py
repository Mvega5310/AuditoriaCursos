"""
Pruebas del comando agregar_documento, el aviso por documento (avisar_dias) y el catalogo por sector.
Ejecutar:  python -m pytest -q
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import core                      # noqa: E402
import importar_excel            # noqa: E402
import generar_alertas           # noqa: E402
import agregar_documento         # noqa: E402

H = core.hoy()


def _d(dias):
    return (H + timedelta(days=dias)).isoformat()


def _empresa_id(conn, nombre):
    return conn.execute("SELECT id FROM empresas WHERE nombre = ?", (nombre,)).fetchone()[0]


def test_documento_de_empresa_con_aviso_propio(tmp_path):
    db = tmp_path / "t.db"
    base = ["documento", "--empresa", "Clinica X", "--documento", "Poliza todo riesgo", "--referencia", "POL-1"]
    assert agregar_documento.main(base + ["--vence", _d(40), "--avisar-dias", "45"], db_path=db) == 0
    assert agregar_documento.main(["documento", "--empresa", "Clinica X", "--documento", "Otra poliza",
                                   "--vence", _d(40)], db_path=db) == 0

    conn = core.conectar(db)
    eid = _empresa_id(conn, "Clinica X")
    # Ventana de 7 dias: solo aparece la poliza con aviso de 45 dias (vence en 40)
    docs = [r["documento"] for r in generar_alertas.consultar_vencimientos(conn, eid, 7)]
    assert docs == ["Poliza todo riesgo (POL-1)"]
    # Fuera del aviso (vence en 40 pero avisar solo 30) no aparece
    conn.execute("UPDATE documentos SET avisar_dias = 30 WHERE referencia = 'POL-1'")
    assert generar_alertas.consultar_vencimientos(conn, eid, 7) == []
    # Quedo registrado como documento de empresa, tipo nuevo en el catalogo
    t = conn.execute("SELECT categoria, aplica_a, sector FROM tipos_documento WHERE nombre = 'Poliza todo riesgo'").fetchone()
    assert tuple(t) == ("documento_empresa", "empresa", "general")
    conn.close()


def test_documento_de_empleado_y_renovacion(tmp_path):
    db = tmp_path / "t.db"
    args = ["documento", "--empresa", "Ing SAS", "--cedula", "77", "--nombre", "Jorge", "--apellido", "Mendez",
            "--documento", "Certificacion de soldador", "--categoria", "certificacion", "--vence", _d(20)]
    assert agregar_documento.main(args, db_path=db) == 0
    # Repetir con otra fecha = renovar (no duplica) y conserva el historial
    args[-1] = _d(380)
    assert agregar_documento.main(args, db_path=db) == 0
    conn = core.conectar(db)
    assert conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM documentos_historial").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM empleados").fetchone()[0] == 1
    conn.close()


def test_tipo_nuevo_con_periodicidad_calcula_vencimiento(tmp_path):
    db = tmp_path / "t.db"
    code = agregar_documento.main(["documento", "--empresa", "Alim SAS", "--documento", "Informe de auditoria sanitaria",
                                   "--periodicidad", "365", "--emision", _d(-10), "--sector", "Alimentos"], db_path=db)
    assert code == 0
    conn = core.conectar(db)
    fila = conn.execute("""SELECT d.fecha_vencimiento, t.periodicidad_dias, t.sector FROM documentos d
                           JOIN tipos_documento t ON t.id = d.tipo_id""").fetchone()
    assert tuple(fila) == (_d(355), 365, "alimentos")
    conn.close()


def test_errores_de_uso(tmp_path, capsys):
    db = tmp_path / "t.db"
    # categoria inventada
    assert agregar_documento.main(["documento", "--documento", "X", "--categoria", "inventada", "--vence", _d(5)],
                                  db_path=db) == 1
    # documento de persona sin cedula
    assert agregar_documento.main(["documento", "--documento", "X", "--categoria", "curso", "--vence", _d(5)],
                                  db_path=db) == 1
    # fecha invalida
    assert agregar_documento.main(["documento", "--documento", "X", "--vence", "no-es-fecha"], db_path=db) == 1
    # sin fecha: se guarda pero avisa que no generara alertas
    assert agregar_documento.main(["documento", "--empresa", "E", "--documento", "Plan sin fecha"], db_path=db) == 0
    assert "no generara alertas" in capsys.readouterr().out


def test_listar_por_sector(tmp_path, capsys):
    db = tmp_path / "t.db"
    assert agregar_documento.main(["listar", "--sector", "alimentos"], db_path=db) == 0
    salida = capsys.readouterr().out
    assert "Manipulacion de alimentos" in salida and "Registro sanitario INVIMA" in salida
    assert "REPS" not in salida and "izaje" not in salida
    assert agregar_documento.main(["listar", "--sector", "salud", "--categoria", "examen medico"], db_path=db) == 0
    assert "Vacunacion del personal" in capsys.readouterr().out


def test_catalogo_sin_duplicados_ni_sectores_invalidos():
    from catalogo import CATALOGO, SECTORES
    claves = [(c[1], c[2]) for c in CATALOGO]                 # (categoria, nombre) es unico en la base
    assert len(claves) == len(set(claves))
    assert {c[0] for c in CATALOGO} == set(SECTORES)
    assert all(c[3] in ("empleado", "empresa") for c in CATALOGO)


def test_excel_con_columna_avisar_dias(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Empresa", "Documento", "Fecha Vencimiento", "Avisar Dias"])
    ws.append(["E", "Licencia de construccion", _d(50), 60])
    ws.append(["E", "Plan de emergencias", _d(50), "abc"])        # invalido
    ws.append(["E", "Matriz de identificacion de peligros", _d(50), -5])   # invalido
    ruta = tmp_path / "x.xlsx"
    wb.save(ruta)
    db = tmp_path / "t.db"
    s = importar_excel.importar(ruta, db_path=db, verbose=False)
    assert s["nuevos"] == 1 and len(s["errores"]) == 2
    conn = core.conectar(db)
    assert conn.execute("SELECT avisar_dias FROM documentos").fetchone()[0] == 60
    conn.close()


@pytest.mark.solo_sqlite
def test_base_v2_antigua_recibe_columnas_nuevas(tmp_path):
    if sqlite3.sqlite_version_info < (3, 35, 0):
        pytest.skip("DROP COLUMN requiere SQLite >= 3.35 (solo afecta a la prueba)")
    db = tmp_path / "t.db"
    conn = core.conectar(db)
    core.inicializar_db(conn)
    conn.execute("DROP INDEX IF EXISTS ix_documentos_vencimiento")
    conn.execute("ALTER TABLE documentos DROP COLUMN avisar_dias")
    conn.execute("ALTER TABLE tipos_documento DROP COLUMN sector")
    conn.commit()
    core.inicializar_db(conn)                                  # debe reparar sin perder nada
    cols_doc = {r[1] for r in conn.execute("PRAGMA table_info(documentos)")}
    sector = conn.execute("SELECT sector FROM tipos_documento WHERE nombre = 'Manipulacion de alimentos'").fetchone()[0]
    assert "avisar_dias" in cols_doc and sector == "alimentos"
    conn.close()
