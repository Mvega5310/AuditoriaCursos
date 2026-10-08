"""
Registra UN documento especifico (de una empresa o de un empleado) sin armar un Excel,
con su propia fecha de vencimiento y, si se desea, su propio aviso de anticipacion.
Si el tipo de documento no existe en el catalogo, se crea automaticamente.

Ejemplos:

  # Documento de la EMPRESA con aviso 45 dias antes de vencer
  python scripts/agregar_documento.py documento --empresa "Clinica Bahia SAS" ^
      --documento "Poliza todo riesgo" --referencia POL-123 --vence 15/03/2027 --avisar-dias 45

  # Documento de un EMPLEADO (con cedula)
  python scripts/agregar_documento.py documento --empresa "Ingenieria y Montajes SAS" ^
      --cedula 3001 --nombre Jorge --apellido Mendez --documento "Certificacion de soldador" ^
      --categoria certificacion --vence 2027-01-20 --avisar-dias 30

  # Tipo nuevo con vigencia fija: con --emision se calcula solo el vencimiento
  python scripts/agregar_documento.py documento --empresa "Alimentos del Caribe SAS" ^
      --documento "Informe de auditoria sanitaria" --periodicidad 365 --emision 01/10/2026

  # Que documentos exige un cargo (permite detectar los que nunca se han registrado)
  python scripts/agregar_documento.py requisito --empresa "Clinica Bahia SAS" ^
      --cargo "Auxiliar de enfermeria" --documentos "RCP; Bioseguridad; Humanizacion del servicio"
  python scripts/agregar_documento.py requisitos --empresa "Clinica Bahia SAS"

  # Ver el catalogo (opcionalmente por sector o categoria)
  python scripts/agregar_documento.py listar --sector alimentos

(En Linux/Mac use \\ en lugar de ^ para continuar la linea.)
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DB_PATH, DEFAULT_EMPRESA, CATEGORIAS
from core import conectar, inicializar_db, es_v1, resolver_categoria
from importar_excel import _importar_documentos, _resolver_tipo, _upsert_empresa, agregar_requisito


def agregar(campos: dict, db_path: Path | str = DB_PATH, empresa_defecto: str = DEFAULT_EMPRESA,
            periodicidad: int | None = None, sector: str | None = None) -> dict:
    """campos usa los nombres de columna del Excel (empresa, cedula, documento, fecha_vencimiento, ...)."""
    conn = conectar(db_path)
    if es_v1(conn):
        conn.close()
        raise SystemExit("La base de datos es del esquema anterior. Ejecute primero: python scripts/migrar_v1.py")
    inicializar_db(conn)
    cur = conn.cursor()
    stats = {"empresas": set(), "responsables": 0, "nuevos": 0, "actualizados": 0,
             "sin_vencimiento": 0, "errores": []}
    try:
        categoria_txt = campos.get("categoria")
        categoria = resolver_categoria(categoria_txt)
        if categoria_txt and not categoria:
            stats["errores"].append(f"categoria no reconocida: '{categoria_txt}'. "
                                    f"Opciones: {', '.join(CATEGORIAS)}")
        elif periodicidad is not None or sector:
            # Se fija la vigencia/sector del tipo ANTES de importar, para que se use al calcular el vencimiento
            tipo_id, _ = _resolver_tipo(cur, categoria, campos["documento"], not campos.get("cedula"))
            if periodicidad is not None:
                cur.execute("UPDATE tipos_documento SET periodicidad_dias = ? WHERE id = ?", (periodicidad, tipo_id))
            if sector:
                cur.execute("UPDATE tipos_documento SET sector = ? WHERE id = ?", (sector.strip().lower(), tipo_id))
        if not stats["errores"]:
            df = pd.DataFrame([campos], dtype=object)
            _importar_documentos(cur, df, empresa_defecto, stats)
        if stats["errores"]:
            conn.rollback()        # no dejar tipos/empresas a medias si el documento no se pudo registrar
        else:
            conn.commit()
    finally:
        conn.close()
    stats["empresas"] = sorted(stats["empresas"])
    return stats


def agregar_requisitos(empresa: str, cargo: str, documentos: list[str], categoria: str | None = None,
                       db_path: Path | str = DB_PATH) -> dict:
    """Define que documentos exige un cargo (cargo '*' = todos). Los tipos nuevos se crean en el catalogo."""
    cat = resolver_categoria(categoria)
    if categoria and not cat:
        return {"nuevos": 0, "errores": [f"categoria no reconocida: '{categoria}'. Opciones: {', '.join(CATEGORIAS)}"]}
    conn = conectar(db_path)
    if es_v1(conn):
        conn.close()
        raise SystemExit("La base de datos es del esquema anterior. Ejecute primero: python scripts/migrar_v1.py")
    inicializar_db(conn)
    cur = conn.cursor()
    nuevos = 0
    try:
        empresa_id = _upsert_empresa(cur, empresa)
        for doc in documentos:
            if not doc.strip():
                continue
            tipo_id, _ = _resolver_tipo(cur, cat, doc.strip(), False)
            nuevos += agregar_requisito(cur, empresa_id, cargo, tipo_id)
        conn.commit()
    finally:
        conn.close()
    return {"nuevos": nuevos, "errores": []}


def listar_requisitos(empresa: str | None = None, db_path: Path | str = DB_PATH) -> int:
    conn = conectar(db_path)
    inicializar_db(conn)
    filas = conn.execute("""
        SELECT e.nombre AS empresa, r.cargo, t.nombre AS documento
        FROM requisitos r JOIN empresas e ON e.id = r.empresa_id JOIN tipos_documento t ON t.id = r.tipo_id
        WHERE (CAST(? AS TEXT) IS NULL OR lower(e.nombre) = lower(?)) ORDER BY e.nombre, r.cargo, t.nombre
    """, (empresa, empresa)).fetchall()
    conn.close()
    actual = None
    for f in filas:
        if (f["empresa"], f["cargo"]) != actual:
            actual = (f["empresa"], f["cargo"])
            print(f"\n{f['empresa']} — cargo: {'TODOS' if f['cargo'] == '*' else f['cargo']}")
        print(f"  - {f['documento']}")
    print(f"\n{len(filas)} requisitos.")
    return 0


def listar(sector: str | None = None, categoria: str | None = None, db_path: Path | str = DB_PATH) -> int:
    conn = conectar(db_path)
    if es_v1(conn):
        conn.close()
        raise SystemExit("La base de datos es del esquema anterior. Ejecute primero: python scripts/migrar_v1.py")
    inicializar_db(conn)
    cat = resolver_categoria(categoria) if categoria else None
    filas = conn.execute("""
        SELECT sector, categoria, nombre, aplica_a, periodicidad_dias, norma_referencia
        FROM tipos_documento
        WHERE (CAST(? AS TEXT) IS NULL OR sector = ?) AND (CAST(? AS TEXT) IS NULL OR categoria = ?)
        ORDER BY sector, categoria, nombre
    """, (sector and sector.lower(), sector and sector.lower(), cat, cat)).fetchall()
    conn.close()
    actual = None
    for f in filas:
        if f["sector"] != actual:
            actual = f["sector"]
            print(f"\n== {actual.upper()} ==")
        vig = f"{f['periodicidad_dias']} dias" if f["periodicidad_dias"] else "segun soporte"
        print(f"  [{CATEGORIAS.get(f['categoria'], f['categoria'])}] {f['nombre']} "
              f"({f['aplica_a']}; vigencia: {vig}){'  — ' + f['norma_referencia'] if f['norma_referencia'] else ''}")
    print(f"\n{len(filas)} tipos de documento.")
    return 0


def main(argv: list[str] | None = None, db_path: Path | str = DB_PATH) -> int:
    p = argparse.ArgumentParser(description="Agregar un documento especifico o ver el catalogo.")
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("listar", help="Muestra el catalogo de tipos de documento")
    l.add_argument("--sector", help="general | alimentos | salud | construccion | (propio)")
    l.add_argument("--categoria", help="curso, certificacion, licencia, examen_medico, afiliacion, documento_empresa")

    q = sub.add_parser("requisito", help="Define que documentos exige un cargo (para detectar faltantes)")
    q.add_argument("--empresa", default=DEFAULT_EMPRESA)
    q.add_argument("--cargo", required=True, help="Cargo, o '*' para todos los cargos de la empresa")
    q.add_argument("--documentos", required=True, help="Lista separada por ; (ej: \"RCP; Bioseguridad; Humanizacion\")")
    q.add_argument("--categoria", help="Categoria de los tipos nuevos (por defecto: curso)")

    r = sub.add_parser("requisitos", help="Muestra los requisitos por cargo")
    r.add_argument("--empresa")

    d = sub.add_parser("documento", help="Registra un documento de una empresa o de un empleado")
    d.add_argument("--empresa", default=DEFAULT_EMPRESA)
    d.add_argument("--documento", required=True, help="Nombre del documento (si no existe, se crea en el catalogo)")
    d.add_argument("--categoria")
    d.add_argument("--cedula", help="Si se indica, el documento es del empleado; si no, de la empresa")
    d.add_argument("--nombre")
    d.add_argument("--apellido")
    d.add_argument("--cargo")
    d.add_argument("--area")
    d.add_argument("--referencia", help="Numero de certificado, poliza, placa, codigo de equipo, etc.")
    d.add_argument("--entidad")
    d.add_argument("--emision", help="dd/mm/aaaa o aaaa-mm-dd")
    d.add_argument("--vence", help="dd/mm/aaaa o aaaa-mm-dd")
    d.add_argument("--avisar-dias", type=int, help="Aparece en TODAS las alertas desde N dias antes de vencer")
    d.add_argument("--periodicidad", type=int, help="Vigencia en dias del tipo (calcula el vencimiento desde --emision)")
    d.add_argument("--sector", help="Sector del tipo nuevo (por defecto: general)")
    d.add_argument("--archivo", help="Ruta o enlace al soporte")
    d.add_argument("--observaciones")

    a = p.parse_args(argv)
    if a.cmd == "listar":
        return listar(a.sector, a.categoria, db_path)

    if a.cmd == "requisitos":
        return listar_requisitos(a.empresa, db_path)
    if a.cmd == "requisito":
        res = agregar_requisitos(a.empresa, a.cargo, a.documentos.split(";"), a.categoria, db_path)
        if res["errores"]:
            for e in res["errores"]:
                print(f"Error: {e}")
            return 1
        print(f"Requisitos agregados: {res['nuevos']} (cargo: {a.cargo}) — {a.empresa}")
        return 0

    campos = {"empresa": a.empresa, "documento": a.documento, "categoria": a.categoria, "cedula": a.cedula,
              "nombre": a.nombre, "apellido": a.apellido, "cargo": a.cargo, "area": a.area,
              "referencia": a.referencia, "entidad": a.entidad, "fecha_emision": a.emision,
              "fecha_vencimiento": a.vence, "avisar_dias": a.avisar_dias, "archivo": a.archivo,
              "observaciones": a.observaciones}
    campos = {k: (str(v) if v is not None else None) for k, v in campos.items()}
    stats = agregar(campos, db_path, periodicidad=a.periodicidad, sector=a.sector)

    if stats["errores"]:
        for e in stats["errores"]:
            print(f"Error: {e}")
        return 1
    accion = "actualizado" if stats["actualizados"] else "registrado"
    print(f"Documento {accion}: {a.documento} — {a.empresa}")
    if stats["sin_vencimiento"]:
        print("  Aviso: sin fecha de vencimiento; no generara alertas. Use --vence (o --periodicidad con --emision).")
    elif a.avisar_dias:
        print(f"  Aparecera en todas las alertas desde {a.avisar_dias} dias antes de su vencimiento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
