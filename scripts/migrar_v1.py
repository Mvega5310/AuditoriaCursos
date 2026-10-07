"""
Migra una base de datos del esquema anterior (un solo cliente: empleados / cursos /
empleado_cursos) al esquema multi-empresa, sin perder datos.

Que hace:
  1. Copia de seguridad del archivo .db (cursos.db.bak-AAAAMMDD).
  2. Renombra las tablas antiguas a *_v1 (quedan como respaldo dentro del mismo archivo).
  3. Crea el esquema nuevo y carga el catalogo de tipos.
  4. Crea la empresa indicada y le asigna todos los empleados y cursos existentes.
  5. Verifica que los conteos coincidan.

Uso:
  python scripts/migrar_v1.py [--empresa "Nombre de la empresa"] [--db ruta/cursos.db]
"""
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import DB_PATH, DEFAULT_EMPRESA
from core import conectar, inicializar_db, es_v1, hoy


def migrar(db_path: Path | str = DB_PATH, empresa: str = DEFAULT_EMPRESA, verbose: bool = True) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"No se encuentra la base de datos: {db_path}")

    conn = conectar(db_path)
    if not es_v1(conn):
        conn.close()
        raise SystemExit("La base de datos ya esta en el esquema nuevo (o no es del esquema anterior). Nada que migrar.")

    n_emp = conn.execute("SELECT COUNT(*) FROM empleados").fetchone()[0]
    n_cur = conn.execute("SELECT COUNT(*) FROM cursos").fetchone()[0]
    n_ec = conn.execute("SELECT COUNT(*) FROM empleado_cursos").fetchone()[0]
    n_log = conn.execute("SELECT COUNT(*) FROM log_alertas").fetchone()[0]

    respaldo = db_path.with_name(f"{db_path.name}.bak-{hoy().strftime('%Y%m%d')}")
    conn.close()
    shutil.copy2(db_path, respaldo)
    if verbose:
        print(f"Respaldo creado: {respaldo.name}")

    conn = conectar(db_path)
    try:
        for tabla in ("log_alertas", "empleado_cursos", "cursos", "empleados"):
            conn.execute(f"ALTER TABLE {tabla} RENAME TO {tabla}_v1")
        inicializar_db(conn)

        conn.execute("INSERT INTO empresas (nombre) VALUES (?)", (empresa,))
        empresa_id = conn.execute("SELECT id FROM empresas WHERE nombre = ?", (empresa,)).fetchone()[0]

        # Empleados (conservan su id para poder enlazar los documentos)
        conn.execute("""
            INSERT INTO empleados (id, empresa_id, cedula, nombre, apellido, cargo, area, activo)
            SELECT id, ?, cedula, nombre, apellido, cargo, area, activo FROM empleados_v1
        """, (empresa_id,))

        # Cursos -> tipos_documento (el 365 por defecto del esquema anterior no era una regla real: se deja sin periodicidad)
        for c in conn.execute("SELECT id, nombre, descripcion FROM cursos_v1").fetchall():
            conn.execute("""INSERT OR IGNORE INTO tipos_documento (categoria, nombre, aplica_a, nota)
                            VALUES ('curso', ?, 'empleado', ?)""", (c["nombre"], c["descripcion"] or ""))

        # empleado_cursos -> documentos
        conn.execute("""
            INSERT INTO documentos (empresa_id, empleado_id, tipo_id, referencia, fecha_emision,
                                    fecha_vencimiento, actualizado_en)
            SELECT ?, ec.empleado_id, t.id, '', ec.fecha_realizacion, ec.fecha_vencimiento, ?
            FROM empleado_cursos_v1 ec
            JOIN cursos_v1 c         ON c.id = ec.curso_id
            JOIN tipos_documento t   ON t.categoria = 'curso' AND t.nombre = c.nombre
        """, (empresa_id, hoy().isoformat()))

        # Historial de alertas
        conn.execute("""
            INSERT INTO log_alertas (fecha_envio, tipo_alerta, empresa_id, documentos_notificados,
                                     destinatario, estado, detalle)
            SELECT fecha_envio, tipo_alerta, ?, cursos_notificados, destinatario, estado, detalle
            FROM log_alertas_v1
        """, (empresa_id,))

        d_emp = conn.execute("SELECT COUNT(*) FROM empleados").fetchone()[0]
        d_doc = conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0]
        d_log = conn.execute("SELECT COUNT(*) FROM log_alertas").fetchone()[0]
        if (d_emp, d_doc, d_log) != (n_emp, n_ec, n_log):
            raise RuntimeError(f"Los conteos no coinciden: empleados {d_emp}/{n_emp}, "
                               f"documentos {d_doc}/{n_ec}, log {d_log}/{n_log}")
        conn.commit()
    except Exception:
        conn.close()
        shutil.copy2(respaldo, db_path)     # restaura el respaldo: la base queda como estaba
        raise

    conn.close()
    resultado = {"empresa": empresa, "empleados": d_emp, "documentos": d_doc, "log_alertas": d_log,
                 "cursos_v1": n_cur, "respaldo": str(respaldo)}
    if verbose:
        print(f"Migracion completada -> empresa '{empresa}'")
        print(f"  Empleados: {d_emp} | Documentos: {d_doc} | Registros de log: {d_log}")
        print("  Las tablas antiguas quedaron como *_v1 dentro del mismo archivo (respaldo).")
        print("  Siguiente paso: agregue los correos de los responsables (hoja 'Empresas' del Excel).")
    return resultado


if __name__ == "__main__":
    args = sys.argv[1:]
    empresa, ruta = DEFAULT_EMPRESA, DB_PATH
    for flag in ("--empresa", "--db"):
        if flag in args:
            i = args.index(flag)
            valor = args[i + 1] if i + 1 < len(args) else None
            if flag == "--empresa" and valor:
                empresa = valor
            if flag == "--db" and valor:
                ruta = Path(valor)
    migrar(ruta, empresa)
