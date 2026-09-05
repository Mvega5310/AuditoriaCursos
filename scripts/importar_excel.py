"""
Importa empleados y cursos desde un archivo Excel a la base de datos SQLite.

Formato esperado del Excel (una fila por empleado-curso):
  Cedula | Nombre | Apellido | Cargo | Area | Curso | Fecha Realizacion | Fecha Vencimiento

Uso:
  python scripts/importar_excel.py <ruta_al_archivo.xlsx>

El script puede ejecutarse multiples veces (actualiza registros existentes sin duplicar).
"""
import sqlite3
import sys
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "db" / "cursos.db"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

# Mapeo flexible de nombres de columna (acepta variantes con/sin tilde)
COLUMNAS = {
    "Cedula": "cedula", "Cédula": "cedula",
    "Nombre": "nombre",
    "Apellido": "apellido",
    "Cargo": "cargo",
    "Area": "area", "Área": "area",
    "Curso": "curso",
    "Fecha Realizacion": "fecha_realizacion", "Fecha Realización": "fecha_realizacion",
    "Fecha Vencimiento": "fecha_vencimiento",
}


def inicializar_db(conn: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def parsear_fecha(valor) -> str | None:
    if valor is None or (hasattr(valor, '__class__') and 'NaT' in type(valor).__name__):
        return None
    try:
        return pd.to_datetime(valor).strftime("%Y-%m-%d")
    except Exception:
        return None


def importar(ruta_excel: str) -> None:
    ruta = Path(ruta_excel)
    if not ruta.exists():
        print(f"Error: No se encuentra el archivo '{ruta}'")
        sys.exit(1)

    print(f"Leyendo: {ruta.name}")
    df = pd.read_excel(ruta, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df.rename(columns=COLUMNAS, inplace=True)
    df = df.where(pd.notna(df), None)

    columnas_requeridas = {"cedula", "nombre", "apellido", "curso", "fecha_vencimiento"}
    faltantes = columnas_requeridas - set(df.columns)
    if faltantes:
        print(f"Error: Faltan columnas en el Excel: {faltantes}")
        print(f"Columnas encontradas: {list(df.columns)}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    inicializar_db(conn)
    cur = conn.cursor()

    insertados = actualizados = errores = 0

    for idx, row in df.iterrows():
        fila_num = idx + 2  # +2 por encabezado y base-0
        try:
            cedula = str(row["cedula"]).strip()
            nombre = str(row["nombre"]).strip()
            apellido = str(row["apellido"]).strip()
            cargo = str(row.get("cargo") or "").strip()
            area = str(row.get("area") or "").strip()
            nombre_curso = str(row["curso"]).strip()
            fecha_venc = parsear_fecha(row["fecha_vencimiento"])
            fecha_real = parsear_fecha(row.get("fecha_realizacion"))

            if not fecha_venc:
                print(f"  Fila {fila_num}: Fecha de vencimiento invalida para {nombre} {apellido} — omitida")
                errores += 1
                continue

            # Upsert empleado
            cur.execute("""
                INSERT INTO empleados (cedula, nombre, apellido, cargo, area)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cedula) DO UPDATE SET
                    nombre    = excluded.nombre,
                    apellido  = excluded.apellido,
                    cargo     = excluded.cargo,
                    area      = excluded.area
            """, (cedula, nombre, apellido, cargo, area))

            empleado_id = cur.execute(
                "SELECT id FROM empleados WHERE cedula = ?", (cedula,)
            ).fetchone()[0]

            # Insert curso si no existe
            cur.execute("INSERT OR IGNORE INTO cursos (nombre) VALUES (?)", (nombre_curso,))
            curso_id = cur.execute(
                "SELECT id FROM cursos WHERE nombre = ?", (nombre_curso,)
            ).fetchone()[0]

            # Verificar si el registro de empleado-curso ya existe
            existente = cur.execute(
                "SELECT id FROM empleado_cursos WHERE empleado_id = ? AND curso_id = ?",
                (empleado_id, curso_id)
            ).fetchone()

            if existente:
                cur.execute("""
                    UPDATE empleado_cursos
                    SET fecha_realizacion = ?, fecha_vencimiento = ?
                    WHERE id = ?
                """, (fecha_real, fecha_venc, existente[0]))
                actualizados += 1
            else:
                cur.execute("""
                    INSERT INTO empleado_cursos (empleado_id, curso_id, fecha_realizacion, fecha_vencimiento)
                    VALUES (?, ?, ?, ?)
                """, (empleado_id, curso_id, fecha_real, fecha_venc))
                insertados += 1

        except Exception as e:
            print(f"  Fila {fila_num}: Error — {e}")
            errores += 1

    conn.commit()
    conn.close()

    print(f"\nImportacion completada:")
    print(f"  Registros nuevos:       {insertados}")
    print(f"  Registros actualizados: {actualizados}")
    print(f"  Filas con error:        {errores}")
    print(f"  Base de datos:          {DB_PATH}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/importar_excel.py <ruta_al_archivo.xlsx>")
        sys.exit(1)
    importar(sys.argv[1])
