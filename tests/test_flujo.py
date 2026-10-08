"""
Pruebas del flujo completo: importar -> consultar -> alertas (dry-run) -> migrar desde v1.
Ejecutar:  python -m pytest -q
"""
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import openpyxl
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import core                      # noqa: E402
import importar_excel            # noqa: E402
import generar_alertas           # noqa: E402
import migrar_v1                 # noqa: E402

ESQUEMA_V1 = """
-- Base de datos: Sistema de control de vencimiento de cursos
-- Empresa sector salud | Modulo RRHH

CREATE TABLE IF NOT EXISTS empleados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cedula          TEXT    UNIQUE NOT NULL,
    nombre          TEXT    NOT NULL,
    apellido        TEXT    NOT NULL,
    cargo           TEXT    DEFAULT '',
    area            TEXT    DEFAULT '',
    activo          INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS cursos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre              TEXT    UNIQUE NOT NULL,
    descripcion         TEXT    DEFAULT '',
    periodicidad_dias   INTEGER DEFAULT 365
);

CREATE TABLE IF NOT EXISTS empleado_cursos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id         INTEGER NOT NULL,
    curso_id            INTEGER NOT NULL,
    fecha_realizacion   TEXT,
    fecha_vencimiento   TEXT    NOT NULL,
    UNIQUE (empleado_id, curso_id),
    FOREIGN KEY (empleado_id) REFERENCES empleados(id),
    FOREIGN KEY (curso_id)    REFERENCES cursos(id)
);

-- Registro de auditoria: cada correo enviado queda guardado aqui
CREATE TABLE IF NOT EXISTS log_alertas (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_envio         TEXT    NOT NULL,
    tipo_alerta         TEXT    NOT NULL,
    cursos_notificados  INTEGER DEFAULT 0,
    destinatario        TEXT    NOT NULL,
    estado              TEXT    NOT NULL,   -- enviado | error_envio | sin_registros
    detalle             TEXT    DEFAULT ''
);
"""

H = core.hoy()
ENC = ["Empresa", "Cedula", "Nombre", "Apellido", "Cargo", "Area", "Categoria", "Documento",
       "Referencia", "Entidad", "Fecha Emision", "Fecha Vencimiento"]


def _excel(tmp_path, filas, empresas=None, nombre="in.xlsx"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Documentos"
    ws.append(ENC)
    for f in filas:
        ws.append([None if v == "" else v for v in f])
    if empresas:
        we = wb.create_sheet("Empresas")
        we.append(["Empresa", "NIT", "Sector", "Responsable", "Email"])
        for e in empresas:
            we.append(list(e))
    ruta = tmp_path / nombre
    wb.save(ruta)
    return ruta


def _d(dias):
    return (H + timedelta(days=dias)).isoformat()


def test_fechas_dia_primero_y_formatos():
    assert core.parsear_fecha("05/03/2026") == "2026-03-05"          # dd/mm, no mm/dd
    assert core.parsear_fecha("2026-03-05 00:00:00") == "2026-03-05"
    assert core.parsear_fecha("basura") is None
    assert core.parsear_fecha(None) is None


def test_clasificacion_incluye_vencidos():
    assert core.clasificar(-1)[0] == "vencido"
    assert core.clasificar(0)[0] == "critico"
    assert core.clasificar(7)[0] == "critico"
    assert core.clasificar(8)[0] == "alerta"
    assert core.clasificar(16)[0] == "proximo"
    assert core.clasificar(31)[0] == "normal"


def test_importar_multi_empresa_y_alertas(tmp_path):
    db = tmp_path / "t.db"
    ruta = _excel(tmp_path, [
        # misma cedula en dos empresas distintas: son personas/registros independientes
        ("Empresa A", "1", "Ana", "Uno", "Op", "X", "Curso", "Trabajo en alturas", "", "", _d(-300), _d(5)),
        ("Empresa B", "1", "Ana", "Uno", "Op", "X", "Curso", "Trabajo en alturas", "", "", _d(-300), _d(200)),
        ("Empresa A", "1", "Ana", "Uno", "Op", "X", "Examen medico", "Examen medico ocupacional periodico", "", "", _d(-100), _d(-2)),
        ("Empresa A", "1", "Ana", "Uno", "Op", "X", "Afiliacion", "Afiliacion EPS", "", "", _d(-10), ""),   # 30 dias por catalogo
        ("Empresa A", "", "", "", "", "", "Documento de empresa", "Politica de SST (revision)", "", "", _d(-400), _d(-30)),
        ("Empresa A", "", "", "", "", "", "Documento de empresa", "Plan de emergencias", "", "", _d(-10), ""),  # sin vencimiento
    ], empresas=[("Empresa A", "1-1", "Alimentos", "Coord", "a@x.com; b@x.com"), ("Empresa B", "", "", "", "")])

    stats = importar_excel.importar(ruta, db_path=db, verbose=False)
    assert stats["errores"] == []
    assert stats["nuevos"] == 6 and stats["sin_vencimiento"] == 1

    conn = core.conectar(db)
    assert conn.execute("SELECT COUNT(*) FROM empleados").fetchone()[0] == 2        # Ana existe en A y en B
    assert conn.execute("SELECT COUNT(*) FROM responsables").fetchone()[0] == 2
    ids = {r["nombre"]: r["id"] for r in conn.execute("SELECT id, nombre FROM empresas")}

    # Empresa A, ventana de 7 dias: alturas (5), examen (vencido), politica (vencida).
    # La afiliacion (emision -10 + 30 = +20 dias) queda fuera de la ventana.
    reg_a = generar_alertas.consultar_vencimientos(conn, ids["Empresa A"], 7)
    estados = sorted((r["documento"], r["clase"]) for r in reg_a)
    assert estados == [("Examen medico ocupacional periodico", "vencido"),
                       ("Politica de SST (revision)", "vencido"),
                       ("Trabajo en alturas", "critico")]
    assert any(r["titular"] == "EMPRESA" for r in reg_a)

    # Ventana de 30 dias: aparece la afiliacion como "alerta" (20 dias -> proximo)
    reg_a30 = generar_alertas.consultar_vencimientos(conn, ids["Empresa A"], 30)
    afil = [r for r in reg_a30 if r["documento"] == "Afiliacion EPS"][0]
    assert afil["dias"] == 20 and afil["clase"] == "proximo"
    assert not any(r["documento"] == "Plan de emergencias" for r in reg_a30)        # sin vencimiento: nunca alerta

    # Empresa B no ve nada de A y tiene un documento a 200 dias
    assert generar_alertas.consultar_vencimientos(conn, ids["Empresa B"], 30) == []
    assert generar_alertas.obtener_destinatarios(conn, ids["Empresa A"]) == ["a@x.com", "b@x.com"]
    conn.close()

    # dry-run: genera un HTML por empresa con registros y NO escribe en log_alertas
    generar_alertas.SALIDAS_DIR = tmp_path / "salidas"
    generar_alertas.ejecutar_alerta("diaria", dry_run=True, db_path=db)
    archivos = sorted(p.name for p in (tmp_path / "salidas").glob("*.html"))
    assert archivos == ["alerta_diaria_empresa_a.html"]
    html = (tmp_path / "salidas" / archivos[0]).read_text(encoding="utf-8")
    assert "VENCIDO" in html and "hace 2" in html and "Empresa A" in html
    conn = core.conectar(db)
    assert conn.execute("SELECT COUNT(*) FROM log_alertas").fetchone()[0] == 0
    conn.close()


def test_renovacion_actualiza_y_guarda_historial(tmp_path):
    db = tmp_path / "t.db"
    fila = ("E", "9", "Rosa", "Peña", "", "", "Curso", "Primeros auxilios", "", "", _d(-360), _d(5))
    importar_excel.importar(_excel(tmp_path, [fila], nombre="a.xlsx"), db_path=db, verbose=False)
    renovado = ("E", "9", "Rosa", "Peña", "", "", "Curso", "Primeros auxilios", "", "", _d(0), _d(365))
    s = importar_excel.importar(_excel(tmp_path, [renovado], nombre="b.xlsx"), db_path=db, verbose=False)
    assert s["actualizados"] == 1 and s["nuevos"] == 0

    conn = core.conectar(db)
    assert conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0] == 1
    assert conn.execute("SELECT fecha_vencimiento FROM documentos").fetchone()[0] == _d(365)
    hist = conn.execute("SELECT fecha_vencimiento FROM documentos_historial").fetchall()
    assert [h["fecha_vencimiento"] for h in hist] == [_d(5)]
    conn.close()


def test_filas_invalidas_se_reportan_sin_detener_la_importacion(tmp_path):
    db = tmp_path / "t.db"
    ruta = _excel(tmp_path, [
        ("E", "1", "Ok", "Ok", "", "", "Curso", "Primeros auxilios", "", "", "", _d(30)),
        ("E", "2", "Mala", "Fecha", "", "", "Curso", "Primeros auxilios", "", "", "", "no-es-fecha"),
        ("E", "3", "Sin", "Cat", "", "", "Inventada", "Algo", "", "", "", _d(30)),
        ("E", "", "", "", "", "", "Curso", "Algo", "", "", "", _d(30)),          # curso sin cedula
        ("E", "4", "", "", "", "", "Curso", "Primeros auxilios", "", "", "", _d(30)),   # sin nombre
        ("E", "5", "Al", "Reves", "", "", "Curso", "Primeros auxilios", "", "", _d(10), _d(-10)),  # vence antes de emitirse
    ])
    s = importar_excel.importar(ruta, db_path=db, verbose=False)
    assert s["nuevos"] == 1
    assert len(s["errores"]) == 5


def test_formato_anterior_sigue_funcionando(tmp_path):
    """Excel del esquema v1: sin Empresa ni Categoria, con 'Curso' y 'Fecha Realizacion'."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Cedula", "Nombre", "Apellido", "Cargo", "Area", "Curso", "Fecha Realizacion", "Fecha Vencimiento"])
    ws.append(["10", "Maria", "Lopez", "Enfermera", "UCI", "Bioseguridad", "01/01/2026", "31/12/2026"])
    ruta = tmp_path / "v1.xlsx"
    wb.save(ruta)
    db = tmp_path / "t.db"
    s = importar_excel.importar(ruta, db_path=db, empresa_defecto="Mi Empresa", verbose=False)
    assert s["nuevos"] == 1 and s["errores"] == []
    conn = core.conectar(db)
    fila = conn.execute("SELECT fecha_emision, fecha_vencimiento FROM documentos").fetchone()
    assert (fila[0], fila[1]) == ("2026-01-01", "2026-12-31")
    assert conn.execute("SELECT nombre FROM empresas").fetchone()[0] == "Mi Empresa"
    conn.close()


@pytest.mark.solo_sqlite
def test_migracion_desde_v1(tmp_path):
    db = tmp_path / "cursos.db"
    c = sqlite3.connect(db)
    c.executescript(ESQUEMA_V1)
    c.execute("INSERT INTO empleados (cedula, nombre, apellido, cargo, area) VALUES ('1','Ana','Uno','Enf','UCI')")
    c.execute("INSERT INTO empleados (cedula, nombre, apellido, activo) VALUES ('2','Beto','Dos',0)")
    c.execute("INSERT INTO cursos (nombre) VALUES ('Bioseguridad')")
    c.execute("INSERT INTO cursos (nombre) VALUES ('Primeros auxilios')")      # tambien existe en el catalogo nuevo
    c.execute("INSERT INTO empleado_cursos (empleado_id, curso_id, fecha_realizacion, fecha_vencimiento) VALUES (1,1,'2026-01-01','2026-12-31')")
    c.execute("INSERT INTO empleado_cursos (empleado_id, curso_id, fecha_realizacion, fecha_vencimiento) VALUES (1,2,'2026-02-01','2027-02-01')")
    c.execute("INSERT INTO empleado_cursos (empleado_id, curso_id, fecha_realizacion, fecha_vencimiento) VALUES (2,1,'2025-01-01','2025-12-31')")
    c.execute("INSERT INTO log_alertas (fecha_envio, tipo_alerta, cursos_notificados, destinatario, estado) VALUES ('2026-09-01T07:00:00','diaria',3,'rrhh@x.com','enviado')")
    c.commit()
    c.close()

    # La alerta/importacion se niegan a trabajar sobre una base v1 hasta migrarla
    with pytest.raises(SystemExit):
        importar_excel.importar(_excel(tmp_path, [("E", "1", "A", "B", "", "", "Curso", "X", "", "", "", _d(5))]),
                                db_path=db, verbose=False)

    r = migrar_v1.migrar(db, "Clinica Vieja", verbose=False)
    assert (r["empleados"], r["documentos"], r["log_alertas"]) == (2, 3, 1)
    assert Path(r["respaldo"]).exists()

    conn = core.conectar(db)
    assert not core.es_v1(conn)
    assert conn.execute("SELECT COUNT(*) FROM tipos_documento WHERE nombre = 'Primeros auxilios'").fetchone()[0] == 1  # sin duplicar
    fila = conn.execute("""
        SELECT em.nombre AS empresa, t.categoria, d.fecha_emision, d.fecha_vencimiento
        FROM documentos d JOIN empresas em ON em.id = d.empresa_id JOIN tipos_documento t ON t.id = d.tipo_id
        JOIN empleados e ON e.id = d.empleado_id WHERE e.cedula = '1' AND t.nombre = 'Bioseguridad'
    """).fetchone()
    assert tuple(fila) == ("Clinica Vieja", "curso", "2026-01-01", "2026-12-31")
    assert conn.execute("SELECT COUNT(*) FROM empleados_v1").fetchone()[0] == 2        # respaldo conservado
    conn.close()

    # Tras migrar, el flujo normal funciona y el empleado inactivo no genera alertas
    conn = core.conectar(db)
    emp_id = conn.execute("SELECT id FROM empresas").fetchone()[0]
    assert generar_alertas.consultar_vencimientos(conn, emp_id, 36500)[0]["titular"] == "Uno, Ana"
    assert len(generar_alertas.consultar_vencimientos(conn, emp_id, 36500)) == 2
    conn.close()

    with pytest.raises(SystemExit):          # migrar dos veces no hace nada
        migrar_v1.migrar(db, "Otra", verbose=False)
