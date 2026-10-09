"""
Importa empresas, responsables, empleados, documentos y requisitos desde un Excel a SQLite.

El Excel puede tener varias hojas; cada una se reconoce sola:

  1) Formato LARGO (una fila por documento) — hoja "Documentos", "Cursos" o cualquiera con
     una columna "Documento"/"Curso":
       Empresa | Cedula | Nombre | Apellido | Cargo | Area | Categoria | Documento |
       Referencia | Entidad | Fecha Emision | Fecha Vencimiento | Avisar Dias | Archivo | Observaciones

       - Con Cedula  -> documento de un EMPLEADO. Sin Cedula -> documento de la EMPRESA.
       - Obligatorias: Documento y (Fecha Vencimiento, o un tipo con vigencia + Fecha Emision).
       - "Avisar Dias": el documento aparece en TODAS las alertas desde N dias antes de vencer.
       - Compatible con el formato anterior (Cedula|Nombre|Apellido|Cargo|Area|Curso|
         Fecha Realizacion|Fecha Vencimiento).

  2) Formato MATRIZ (una fila por persona, una columna por curso) — hoja "Matriz" o cualquiera
     con columna Cedula y sin columna "Documento":
       Cedula | Nombre | Apellido | Cargo | Area | RCP | Bioseguridad | Humanizacion | ...
       - Cada celda trae una fecha. Celda vacia = curso no registrado.
         "N/A" / "No aplica" = el curso no le aplica a esa persona (cuenta como cumplido).
         "-" o "Pendiente" tambien se leen como no registrado.
       - Las columnas que no tienen ninguna fecha se ignoran (observaciones, consecutivos...).
       - Por defecto las fechas son de REALIZACION y el vencimiento se calcula con la vigencia:
           * en el encabezado:   "RCP | 730"   (730 dias)
           * del catalogo, si el curso ya tiene vigencia
           * --vigencia-defecto N  para las columnas que no tengan ninguna
         Si las fechas ya son de VENCIMIENTO use --fechas vencimiento.
       - Si hay un solo campo de nombre ("Nombre completo"), se guarda tal cual.

  3) Hoja "Requisitos" — que documentos exige cada cargo (permite detectar los FALTANTES):
       Empresa | Cargo | Documento | Categoria
       Cargo "*" (o vacio / "Todos") = todos los cargos de la empresa.

  4) Hoja "Empresas" (opcional) — una fila por empresa/responsable:
       Empresa | NIT | Sector | Responsable | Email   (varios correos: separados por , o ;)

Fechas: dd/mm/aaaa o aaaa-mm-dd. Se puede importar varias veces: actualiza sin duplicar, y al
renovar un documento se conserva la version anterior en documentos_historial.

Uso:
  python scripts/importar_excel.py <archivo.xlsx> [--empresa "Nombre"]
        [--fechas realizacion|vencimiento] [--vigencia-defecto 365]
"""
import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import DB_PATH, DEFAULT_EMPRESA
from core import (conectar, inicializar_db, es_v1, parsear_fecha, normalizar,
                  resolver_categoria, hoy)

ALIAS_DOCUMENTOS = {
    "empresa": "empresa",
    "cedula": "cedula", "documento de identidad": "cedula", "identificacion": "cedula",
    "numero de identificacion": "cedula", "cc": "cedula", "c.c.": "cedula",
    "no identificacion": "cedula", "nro identificacion": "cedula", "n identificacion": "cedula",
    "numero identificacion": "cedula", "cedula de ciudadania": "cedula", "numero de cedula": "cedula",
    "doc identidad": "cedula", "cedula ciudadania": "cedula", "nro de identificacion": "cedula",
    "n de identificacion": "cedula", "no de identificacion": "cedula", "nro de cedula": "cedula",
    "n de cedula": "cedula", "no de cedula": "cedula", "nro cedula": "cedula", "n cedula": "cedula",
    "no cedula": "cedula", "cedula nit": "cedula", "cedula o nit": "cedula", "cedula de identidad": "cedula",
    "nombre": "nombre", "nombres": "nombre", "nombre completo": "nombre",
    "nombres y apellidos": "nombre", "apellidos y nombres": "nombre", "nombre y apellidos": "nombre",
    "nombre del trabajador": "nombre", "nombre trabajador": "nombre", "trabajador": "nombre",
    "nombre del empleado": "nombre", "empleado": "nombre", "colaborador": "nombre",
    "nombre del colaborador": "nombre", "funcionario": "nombre", "nombre del funcionario": "nombre",
    "apellido": "apellido", "apellidos": "apellido",
    "cargo": "cargo",
    "area": "area", "ubicacion": "area", "servicio": "area", "unidad": "area", "dependencia": "area",
    "categoria": "categoria",
    "documento": "documento", "curso": "documento", "certificacion": "documento",
    "tipo documento": "documento", "tipo de documento": "documento",
    "referencia": "referencia", "numero": "referencia", "numero de documento": "referencia",
    "entidad": "entidad", "entidad emisora": "entidad",
    "fecha emision": "fecha_emision", "fecha de emision": "fecha_emision",
    "fecha expedicion": "fecha_emision", "fecha de expedicion": "fecha_emision",
    "fecha realizacion": "fecha_emision", "fecha de realizacion": "fecha_emision",
    "fecha vencimiento": "fecha_vencimiento", "fecha de vencimiento": "fecha_vencimiento",
    "vencimiento": "fecha_vencimiento", "fecha venc": "fecha_vencimiento", "fecha de venc": "fecha_vencimiento",
    "vence": "fecha_vencimiento", "fecha vence": "fecha_vencimiento", "fecha de expiracion": "fecha_vencimiento",
    "avisar dias": "avisar_dias", "avisar con dias": "avisar_dias", "dias de aviso": "avisar_dias",
    "preaviso": "avisar_dias", "avisar con anticipacion": "avisar_dias",
    "archivo": "archivo", "enlace": "archivo", "soporte": "archivo",
    "observaciones": "observaciones",
    "fila origen": "fila_origen",          # uso interno (importacion de matriz)
    "no aplica": "no_aplica",              # uso interno (celda N/A de una matriz)
}
ALIAS_EMPRESAS = {
    "empresa": "empresa", "nit": "nit", "sector": "sector",
    "responsable": "responsable", "nombre responsable": "responsable",
    "email": "email", "correo": "email", "correos": "email", "e mail": "email",
}
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RE_VIGENCIA_ENCABEZADO = re.compile(r"^(.*?)\s*\|\s*(\d+)\s*(?:d|dias)?\s*$", re.IGNORECASE)
# Celdas de una matriz que significan "no registrado" (ya normalizadas)
CELDAS_VACIAS = {"-", "x", "pendiente", "ninguno", "no"}
# Celdas que significan "este curso NO le aplica a la persona" (cuenta como cumplido, no como faltante)
CELDAS_NO_APLICA = {"n/a", "na", "no aplica", "n.a", "n.a."}
RE_PAR = re.compile(r"^(.*) @(ini|fin)$")                     # columnas inicio/final de un mismo curso
RE_SUBFECHA = re.compile(r"^(fecha\s+)?(de\s+)?(inicial|inicio|actuali[sz]aci[oó]n|actuliz\w*|final|vencimiento|vence)\b", re.I)
RE_VIGENCIA_PAREN = re.compile(r"\(\s*(\d+)\s*(a[nñ]os?|meses|mes|d[ií]as?)\s*\)", re.I)
HOJAS_RETIRADOS = ("retirad", "inactiv", "egresad", "desvincul")
TODOS_LOS_CARGOS = {"*", "todos", "todos los cargos", "todo", ""}


def _txt(valor) -> str:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    texto = str(valor).strip()
    return "" if texto.lower() in ("nan", "nat", "none") else texto


def _canon(columna, alias: dict) -> str | None:
    """Nombre interno de un encabezado ('Documento de Identidad' -> 'cedula') o None. Ignora puntos y simbolos."""
    n = normalizar(columna)
    if n in alias:
        return alias[n]
    return alias.get(re.sub(r"\s+", " ", re.sub(r"[.\u00b0\u00ba\u00aa#:/]", " ", n)).strip())


def _renombrar(df: pd.DataFrame, alias: dict) -> pd.DataFrame:
    df = df.copy()
    df.columns = [_canon(c, alias) or normalizar(c) for c in df.columns]
    return df.loc[:, ~df.columns.duplicated()]


def _renombrar_conservando(df: pd.DataFrame, alias: dict) -> pd.DataFrame:
    """Como _renombrar, pero las columnas que no son de identidad conservan su texto original
    (en la matriz el encabezado ES el nombre del curso)."""
    df = df.copy()
    df.columns = [_canon(c, alias) or str(c).strip() for c in df.columns]
    return df.loc[:, ~df.columns.duplicated()]


def _upsert_empresa(cur, nombre: str, nit: str = "", sector: str = "") -> int:
    cur.execute("""
        INSERT INTO empresas (nombre, nit, sector) VALUES (?, ?, ?)
        ON CONFLICT(nombre) DO UPDATE SET
            nit    = COALESCE(NULLIF(excluded.nit, ''), empresas.nit),
            sector = COALESCE(NULLIF(excluded.sector, ''), empresas.sector)
    """, (nombre, nit, sector))
    return cur.execute("SELECT id FROM empresas WHERE nombre = ?", (nombre,)).fetchone()[0]


def _importar_empresas(cur, df: pd.DataFrame, stats: dict) -> None:
    df = _renombrar(df, ALIAS_EMPRESAS)
    if "empresa" not in df.columns:
        stats["errores"].append("Hoja 'Empresas': falta la columna 'Empresa'")
        return
    for idx, row in df.iterrows():
        nombre = _txt(row.get("empresa"))
        if not nombre:
            continue
        empresa_id = _upsert_empresa(cur, nombre, _txt(row.get("nit")), _txt(row.get("sector")))
        stats["empresas"].add(nombre)
        responsable = _txt(row.get("responsable"))
        for email in re.split(r"[;,\s]+", _txt(row.get("email"))):
            if not email:
                continue
            if not RE_EMAIL.match(email):
                stats["errores"].append(f"Hoja 'Empresas' fila {idx + 2}: correo invalido '{email}'")
                continue
            cur.execute("""
                INSERT INTO responsables (empresa_id, nombre, email) VALUES (?, ?, ?)
                ON CONFLICT(empresa_id, email) DO UPDATE SET
                    nombre = COALESCE(NULLIF(excluded.nombre, ''), responsables.nombre), activo = 1
            """, (empresa_id, responsable, email.lower()))
            stats["responsables"] += 1


def _resolver_tipo(cur, categoria_explicita: str | None, nombre: str, es_empresa: bool) -> tuple[int, int | None]:
    """Busca el tipo por nombre (sin distinguir mayusculas); si no existe lo crea. -> (tipo_id, periodicidad)."""
    aplica_a = "empresa" if es_empresa else "empleado"
    if categoria_explicita:
        fila = cur.execute(
            "SELECT id, periodicidad_dias FROM tipos_documento WHERE lower(nombre) = lower(?) AND categoria = ?",
            (nombre, categoria_explicita)).fetchone()
    else:
        fila = cur.execute(
            "SELECT id, periodicidad_dias FROM tipos_documento WHERE lower(nombre) = lower(?) AND aplica_a = ? ORDER BY id LIMIT 1",
            (nombre, aplica_a)).fetchone()
    if fila:
        return fila[0], fila[1]
    categoria = categoria_explicita or ("documento_empresa" if es_empresa else "curso")
    cur.execute("INSERT INTO tipos_documento (categoria, nombre, aplica_a) VALUES (?, ?, ?)",
                (categoria, nombre, aplica_a))
    return cur.lastrowid, None


def agregar_requisito(cur, empresa_id: int, cargo: str, tipo_id: int) -> bool:
    """Registra que el cargo debe tener ese documento. Cargo '*' = todos. True si es nuevo."""
    cargo = cargo.strip()
    todos = normalizar(cargo) in TODOS_LOS_CARGOS
    cur.execute("""
        INSERT OR IGNORE INTO requisitos (empresa_id, cargo, cargo_clave, tipo_id) VALUES (?, ?, ?, ?)
    """, (empresa_id, "*" if todos else cargo, "*" if todos else normalizar(cargo), tipo_id))
    return cur.rowcount > 0


def _importar_requisitos(cur, df: pd.DataFrame, empresa_defecto: str, stats: dict) -> None:
    df = _renombrar(df, ALIAS_DOCUMENTOS)
    if "documento" not in df.columns:
        stats["errores"].append("Hoja 'Requisitos': falta la columna 'Documento' (o 'Curso')")
        return
    for idx, row in df.iterrows():
        doc = _txt(row.get("documento"))
        if not doc:
            continue
        try:
            categoria_txt = _txt(row.get("categoria"))
            categoria = resolver_categoria(categoria_txt)
            if categoria_txt and not categoria:
                raise ValueError(f"categoria no reconocida: '{categoria_txt}'")
            empresa_nombre = _txt(row.get("empresa")) or empresa_defecto
            empresa_id = _upsert_empresa(cur, empresa_nombre)
            stats["empresas"].add(empresa_nombre)
            tipo_id, _ = _resolver_tipo(cur, categoria, doc, False)
            if agregar_requisito(cur, empresa_id, _txt(row.get("cargo")), tipo_id):
                stats["requisitos"] += 1
        except Exception as e:
            stats["errores"].append(f"Hoja 'Requisitos' fila {idx + 2}: {e} — omitida")


def _importar_documentos(cur, df: pd.DataFrame, empresa_defecto: str, stats: dict) -> None:
    df = _renombrar(df, ALIAS_DOCUMENTOS)
    if "documento" not in df.columns:
        raise SystemExit("Error: falta la columna 'Documento' (o 'Curso') en el Excel.\n"
                         f"Columnas encontradas: {list(df.columns)}")

    for idx, row in df.iterrows():
        fila = _txt(row.get("fila_origen")) or idx + 2  # +2 por el encabezado y porque pandas cuenta desde 0
        if not any(_txt(v) for v in row.values):
            continue
        nombre_doc = _txt(row.get("documento"))
        try:
            if not nombre_doc:
                raise ValueError("falta el nombre del documento/curso")

            empresa_id = _upsert_empresa(cur, _txt(row.get("empresa")) or empresa_defecto)
            stats["empresas"].add(_txt(row.get("empresa")) or empresa_defecto)

            cedula = _txt(row.get("cedula"))
            es_empresa = not cedula
            categoria_txt = _txt(row.get("categoria"))
            categoria = resolver_categoria(categoria_txt)
            if categoria_txt and not categoria:
                raise ValueError(f"categoria no reconocida: '{categoria_txt}'")
            if es_empresa and categoria in ("curso", "licencia", "examen_medico", "afiliacion"):
                raise ValueError(f"'{categoria_txt}' es un documento de persona: falta la cedula")
            if not es_empresa and categoria == "documento_empresa":
                raise ValueError("'Documento de empresa' no lleva cedula: dejela vacia")

            empleado_id = None
            if not es_empresa:
                nombre, apellido = _txt(row.get("nombre")), _txt(row.get("apellido"))
                if not nombre:
                    raise ValueError("falta el nombre del empleado")
                cur.execute("""
                    INSERT INTO empleados (empresa_id, cedula, nombre, apellido, cargo, area)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(empresa_id, cedula) DO UPDATE SET
                        nombre   = excluded.nombre,
                        apellido = excluded.apellido,
                        cargo    = COALESCE(NULLIF(excluded.cargo, ''), empleados.cargo),
                        area     = COALESCE(NULLIF(excluded.area, ''), empleados.area)
                """, (empresa_id, cedula, nombre, apellido, _txt(row.get("cargo")), _txt(row.get("area"))))
                empleado_id = cur.execute(
                    "SELECT id FROM empleados WHERE empresa_id = ? AND cedula = ?", (empresa_id, cedula)
                ).fetchone()[0]

            tipo_id, periodicidad = _resolver_tipo(cur, categoria, nombre_doc, es_empresa)

            no_aplica = 1 if _txt(row.get("no_aplica")) == "1" else 0
            emision = vencimiento = None
            if no_aplica:
                stats["no_aplica"] += 1         # cuenta como cumplido; no genera alertas ni faltantes
            else:
                emision_txt, venc_txt = _txt(row.get("fecha_emision")), _txt(row.get("fecha_vencimiento"))
                emision, vencimiento = parsear_fecha(emision_txt), parsear_fecha(venc_txt)
                if emision_txt and not emision:
                    raise ValueError(f"fecha de emision invalida: '{emision_txt}'")
                if venc_txt and not vencimiento:
                    raise ValueError(f"fecha de vencimiento invalida: '{venc_txt}'")
                if not vencimiento and periodicidad and emision:
                    vencimiento = (date.fromisoformat(emision) + timedelta(days=periodicidad)).isoformat()
                if emision and vencimiento and vencimiento < emision:
                    raise ValueError("la fecha de vencimiento es anterior a la de emision")
                if not vencimiento:
                    stats["sin_vencimiento"] += 1   # se guarda, pero no generara alertas

            avisar_txt, avisar = _txt(row.get("avisar_dias")), None
            if avisar_txt:
                try:
                    avisar = int(float(avisar_txt))
                except ValueError:
                    avisar = -1
                if avisar < 0:
                    raise ValueError(f"'Avisar Dias' debe ser un numero entero positivo: '{avisar_txt}'")

            referencia = _txt(row.get("referencia"))
            existente = cur.execute("""
                SELECT id FROM documentos
                WHERE empresa_id = ? AND COALESCE(empleado_id, 0) = COALESCE(?, 0)
                  AND tipo_id = ? AND referencia = ?
            """, (empresa_id, empleado_id, tipo_id, referencia)).fetchone()

            valores = (emision, vencimiento, no_aplica, avisar, _txt(row.get("entidad")), _txt(row.get("archivo")),
                       _txt(row.get("observaciones")), hoy().isoformat())
            if existente:
                cur.execute("""
                    UPDATE documentos SET
                        fecha_emision = ?, fecha_vencimiento = ?, no_aplica = ?,
                        avisar_dias     = COALESCE(?, avisar_dias),
                        entidad_emisora = COALESCE(NULLIF(?, ''), entidad_emisora),
                        archivo         = COALESCE(NULLIF(?, ''), archivo),
                        observaciones   = COALESCE(NULLIF(?, ''), observaciones),
                        actualizado_en  = ?
                    WHERE id = ?
                """, valores + (existente[0],))
                stats["actualizados"] += 0 if no_aplica else 1
            else:
                cur.execute("""
                    INSERT INTO documentos (empresa_id, empleado_id, tipo_id, referencia,
                        fecha_emision, fecha_vencimiento, no_aplica, avisar_dias, entidad_emisora, archivo,
                        observaciones, actualizado_en)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (empresa_id, empleado_id, tipo_id, referencia) + valores)
                stats["nuevos"] += 0 if no_aplica else 1
        except Exception as e:
            etiqueta = f"Fila {fila}" + (f" ({nombre_doc})" if nombre_doc else "")
            stats["errores"].append(f"{etiqueta}: {e} — omitida")


def _normalizar_curso(base: str) -> tuple[str, int | None]:
    """'BLS (2 AÑOS)' -> ('BLS', 730);  'RCP | 365' -> ('RCP', 365);  'Duelo' -> ('Duelo', None)."""
    m = RE_VIGENCIA_ENCABEZADO.match(base)
    if m:
        return m.group(1).strip(), int(m.group(2))
    m = RE_VIGENCIA_PAREN.search(base)
    if m:
        n, unidad = int(m.group(1)), normalizar(m.group(2))
        dias = n * (365 if unidad.startswith("ano") else 30 if unidad.startswith("mes") else 1)
        return re.sub(r"\s+", " ", (base[:m.start()] + base[m.end():])).strip(), dias
    return re.sub(r"\s+", " ", base).strip(), None


def _coincide(nombre: str, entradas: list[str]) -> bool:
    """El curso esta en la lista de controlados: palabra completa, sin importar tildes ni mayusculas."""
    n = normalizar(nombre)
    return any(re.search(rf"(?<!\w){re.escape(normalizar(e))}(?!\w)", n) for e in entradas if normalizar(e))


def _importar_matriz(cur, df: pd.DataFrame, empresa_defecto: str, stats: dict,
                     fechas: str = "emision", vigencia_defecto: int | None = None,
                     solo_cursos: list[str] | None = None, exigir_cursos: bool = False) -> None:
    """Convierte la matriz persona x curso a filas (formato largo) y las importa.
    - Un curso puede tener UNA columna (la fecha significa realizacion o vencimiento segun `fechas`) o DOS
      (inicio y final): el inicio es la realizacion y el final el vencimiento; si falta el final se calcula con la vigencia.
    - 'N/A' = el curso no le aplica a esa persona. Celda vacia = pendiente.
    - solo_cursos: si hay lista, solo esos cursos se controlan; las demas columnas con fechas se ignoran."""
    df = _renombrar_conservando(df, ALIAS_DOCUMENTOS)
    if "nombre" not in df.columns:
        stats["errores"].append("Matriz: falta la columna de nombre ('Nombre' o 'Nombre completo')")
        return

    reservadas = set(ALIAS_DOCUMENTOS.values())
    cursos: dict[str, dict] = {}                 # base -> {"col", "ini", "fin"}
    for col in df.columns:
        if col in reservadas:
            continue
        m = RE_PAR.match(str(col))
        base, rol = (m.group(1), m.group(2)) if m else (str(col), "col")
        cursos.setdefault(base, {})[rol] = col

    plan = []                                    # {"nombre", "col", "ini", "fin", "vigencia"}
    for base, cols in cursos.items():
        nombre, vig_cabecera = _normalizar_curso(base)
        if solo_cursos and not _coincide(nombre, solo_cursos) and not _coincide(base, solo_cursos):
            if any(parsear_fecha(_txt(v), serial=False) for c in cols.values() for v in df[c]):
                stats["columnas_no_controladas"].append(nombre)
            continue
        if not any(parsear_fecha(_txt(v), serial=False) for c in cols.values() for v in df[c]):
            stats["columnas_ignoradas"].append(nombre)           # ninguna fecha: no es un curso
            continue
        _, vig_catalogo = _resolver_tipo(cur, None, nombre, False)
        vigencia = vig_cabecera or vig_catalogo or vigencia_defecto
        tiene_par = "ini" in cols and "fin" in cols
        if fechas == "emision" and not tiene_par and not vigencia:
            stats["errores"].append(
                f"Columna '{nombre}': las fechas son de realizacion pero no hay vigencia. Use "
                f"'{nombre} | 365' en el encabezado, --vigencia-defecto N, o --fechas vencimiento. Columna omitida")
            continue
        plan.append({"nombre": nombre, "vigencia": vigencia, "par": tiene_par, **cols})

    filas, personas, vistas, requeridos = [], set(), {}, set()
    for idx, row in df.iterrows():
        fila = idx + 2
        cedula, nombre_persona = _txt(row.get("cedula")), _txt(row.get("nombre"))
        if not cedula and not nombre_persona:
            continue
        if not cedula:
            stats["errores"].append(f"Matriz fila {fila}: falta la cedula de '{nombre_persona}' — omitida")
            continue
        clave = (_txt(row.get("empresa")) or empresa_defecto, cedula)
        if clave in vistas:
            stats["errores"].append(f"Matriz fila {fila}: la cedula {cedula} ya aparece en la fila {vistas[clave]}; "
                                    "se omite esta fila (revise cual es la correcta)")
            continue
        vistas[clave] = fila
        # La persona se registra aunque no tenga ningun curso: asi se detectan sus faltantes
        empresa_fila = clave[0]
        eid = _upsert_empresa(cur, empresa_fila)
        stats["empresas"].add(empresa_fila)
        if nombre_persona:
            cur.execute("""
                INSERT INTO empleados (empresa_id, cedula, nombre, apellido, cargo, area)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(empresa_id, cedula) DO UPDATE SET
                    nombre   = excluded.nombre,
                    apellido = excluded.apellido,
                    cargo    = COALESCE(NULLIF(excluded.cargo, ''), empleados.cargo),
                    area     = COALESCE(NULLIF(excluded.area, ''), empleados.area),
                    activo   = 1
            """, (eid, cedula, nombre_persona, _txt(row.get("apellido")), _txt(row.get("cargo")), _txt(row.get("area"))))
            personas.add(cedula)
        # Requisito = (cargo de esta persona) x (cursos que trae SU hoja): una hoja de terapistas sin la columna BLS
        # no vuelve faltante el BLS. El N/A de la persona la exime.
        cargo_fila = _txt(row.get("cargo"))
        if exigir_cursos and cargo_fila and (eid, normalizar(cargo_fila)) not in requeridos:
            requeridos.add((eid, normalizar(cargo_fila)))
            for c in plan:
                tipo_id, _ = _resolver_tipo(cur, None, c["nombre"], False)
                stats["requisitos"] += agregar_requisito(cur, eid, cargo_fila, tipo_id)
        base_fila = {"empresa": _txt(row.get("empresa")), "cedula": cedula, "nombre": nombre_persona,
                     "apellido": _txt(row.get("apellido")), "cargo": _txt(row.get("cargo")),
                     "area": _txt(row.get("area")), "fila_origen": fila}
        for c in plan:
            celdas = {r: _txt(row.get(c[r])) for r in ("col", "ini", "fin") if r in c}
            celdas = {r: v for r, v in celdas.items() if v and normalizar(v) not in CELDAS_VACIAS}
            if not celdas:
                continue                                          # pendiente: sin registro (sera un faltante si se exige)
            fechas_ok, malas = {}, []
            for r, v in celdas.items():
                if normalizar(v) in CELDAS_NO_APLICA:
                    continue
                f = parsear_fecha(v)
                if f:
                    fechas_ok[r] = f
                else:
                    malas.append(v)
            for v in malas:
                stats["errores"].append(f"Matriz fila {fila}, columna '{c['nombre']}': fecha invalida '{v}' — omitida")
            if not fechas_ok:
                if not malas:                                     # todo N/A
                    filas.append({**base_fila, "documento": c["nombre"], "no_aplica": "1"})
                continue
            if c["par"]:
                emision = fechas_ok.get("ini")
                venc = fechas_ok.get("fin")
                if not venc and emision and c["vigencia"]:
                    venc = (date.fromisoformat(emision) + timedelta(days=c["vigencia"])).isoformat()
            elif fechas == "emision":
                emision = fechas_ok["col"]
                venc = (date.fromisoformat(emision) + timedelta(days=c["vigencia"])).isoformat()
            else:
                emision, venc = None, fechas_ok["col"]
            filas.append({**base_fila, "documento": c["nombre"], "fecha_emision": emision, "fecha_vencimiento": venc})
    if filas:
        _importar_documentos(cur, pd.DataFrame(filas, dtype=object), empresa_defecto, stats)
    stats["personas_matriz"] += len(personas)


def _marcar_retirados(cur, crudo: pd.DataFrame, empresa_defecto: str, stats: dict) -> None:
    """Hoja de personal retirado (con o sin encabezado): las cedulas que ya estan registradas pasan a inactivas,
    asi dejan de recibir alertas y de contar como faltantes. No crea personas nuevas."""
    mejor, n = None, 0
    for col in crudo.columns:
        k = sum(1 for v in crudo[col] if re.fullmatch(r"\d{6,11}", _txt(v)))
        if k > n:
            mejor, n = col, k
    if mejor is None or n < 1:
        stats["errores"].append("Hoja de retirados: no encontre una columna con cedulas")
        return
    eid = _upsert_empresa(cur, empresa_defecto)
    for v in crudo[mejor]:
        ced = _txt(v)
        if re.fullmatch(r"\d{6,11}", ced):
            cur.execute("UPDATE empleados SET activo = 0 WHERE empresa_id = ? AND cedula = ? AND activo = 1", (eid, ced))
            stats["retirados"] += max(cur.rowcount, 0)


def _clasificar_hoja(df: pd.DataFrame) -> str:
    columnas = {_canon(c, ALIAS_DOCUMENTOS) or normalizar(c) for c in df.columns}
    if "documento" in columnas:
        return "largo"
    if "cedula" in columnas:
        return "matriz"
    return "ignorada"


def _leer_hojas(ruta: Path) -> dict[str, pd.DataFrame]:
    """Lee todas las hojas de un Excel (ver _hojas_desde_crudos)."""
    return _hojas_desde_crudos(pd.read_excel(ruta, sheet_name=None, dtype=str, header=None, keep_default_na=False, na_values=[""]))


def _nombres_columnas(encabezado, grupos) -> list[str]:
    """Nombres de columna cuando el encabezado ocupa varias filas (celdas combinadas):
       - columna con nombre propio  -> ese nombre;
       - columna sin nombre         -> el titulo que tiene encima (primero la fila contigua, luego la de mas arriba);
       - 'FECHA INICIAL' / 'FECHA ACTUALIZACION' (o inicio/final/vencimiento) bajo un titulo de curso -> el curso, como
         par: la primera columna es el inicio y la ultima el final, sin importar como esten rotuladas."""
    n = len(encabezado)
    sub = [_txt(v) for v in encabezado]
    arriba = [[_txt(v) for v in g] for g in grupos]

    def titulo(j):
        for fila in arriba:                                       # la combinada solo guarda el texto en su primera celda
            if fila[j]:
                return fila[j]
        return ""

    def titulo_a_la_izquierda(j):
        fila = arriba[0] if arriba else []
        for k in range(j, -1, -1):
            if k < j and sub[k] and not RE_SUBFECHA.match(normalizar(sub[k])):
                return ""                                         # otra columna con nombre propio: se corta el grupo
            if fila and fila[k]:
                return fila[k]
        return ""

    nombres, pares = [None] * n, {}
    for j in range(n):
        es_sub = bool(sub[j]) and bool(RE_SUBFECHA.match(normalizar(sub[j])))
        if sub[j] and not es_sub:
            nombres[j] = sub[j]
        elif es_sub:
            g = titulo(j) or titulo_a_la_izquierda(j)
            if g:
                pares.setdefault(g, []).append(j)
            else:
                nombres[j] = sub[j]
        else:
            nombres[j] = titulo(j) or f"Columna {j + 1}"
    for g, cols in pares.items():
        if len(cols) == 1:
            nombres[cols[0]] = g
        else:
            nombres[cols[0]], nombres[cols[-1]] = f"{g} @ini", f"{g} @fin"
            for extra in cols[1:-1]:
                nombres[extra] = f"{g} {sub[extra]}"
    # 'APELLIDO' seguido de una columna sin titulo: son los nombres
    canon = [_canon(x, ALIAS_DOCUMENTOS) for x in nombres]
    if "nombre" not in canon:
        for j in range(1, n):
            if canon[j - 1] == "apellido" and nombres[j].startswith("Columna "):
                nombres[j] = "Nombre"
                break
    vistos = {}                                                   # nombres unicos (no se pierde ninguna columna)
    for j, x in enumerate(nombres):
        vistos[x] = vistos.get(x, 0) + 1
        if vistos[x] > 1:
            nombres[j] = f"{x} ({vistos[x]})"
    return nombres


def _hojas_desde_crudos(crudos: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Recibe cada hoja SIN encabezado (todo texto). Si el encabezado no esta en la fila 1 (titulos, logos,
    filas vacias arriba), lo busca en las primeras 15 filas: la fila con mas columnas reconocibles.
    Sirve igual para un Excel y para una hoja de Google Sheets."""
    conocidos = set(ALIAS_DOCUMENTOS) | set(ALIAS_EMPRESAS) | {"cargo", "documento"}
    hojas = {}
    for nombre, crudo in crudos.items():
        crudo = crudo.dropna(how="all").dropna(axis=1, how="all")
        if crudo.empty:
            hojas[nombre] = pd.DataFrame()
            continue
        mejor, puntos = 0, 0
        for i in range(min(15, len(crudo))):
            n = sum(1 for v in crudo.iloc[i] if _txt(v) and (normalizar(v) in conocidos or _canon(v, ALIAS_DOCUMENTOS)))
            if n > puntos:
                mejor, puntos = i, n
        fila_enc = crudo.iloc[mejor]
        grupos = [crudo.iloc[mejor - k] for k in (1, 2) if mejor - k >= 0]       # filas de titulos de grupo
        df = crudo.iloc[mejor + 1:].copy()
        df.columns = _nombres_columnas(fila_enc, grupos)
        # mantiene la numeracion de filas del Excel en los mensajes de error (idx + 2)
        df.index = crudo.index[mejor + 1:] - 1
        hojas[nombre] = df
    return hojas


def _forzar_empresa(df: pd.DataFrame, nombre: str) -> pd.DataFrame:
    """Reemplaza la columna Empresa por una sola: un usuario web no puede escribir en otras empresas."""
    df = df.drop(columns=[c for c in df.columns if normalizar(c) == "empresa"])
    df["Empresa"] = nombre
    return df


def importar(ruta_excel: str | Path | None, db_path: Path | str = DB_PATH,
             empresa_defecto: str = DEFAULT_EMPRESA, verbose: bool = True,
             fechas: str = "emision", vigencia_defecto: int | None = None,
             empresa_forzada: str | None = None, solo_validar: bool = False,
             hojas_crudas: dict[str, pd.DataFrame] | None = None,
             solo_cursos: list[str] | None = None, exigir_cursos: bool = False) -> dict:
    """fechas: 'emision' (realizacion) o 'vencimiento' -- que significan las fechas de una MATRIZ.
    empresa_forzada: todo el contenido se asigna a esa empresa, ignorando la columna Empresa del archivo.
    solo_validar: procesa todo y devuelve el resultado, pero no guarda nada.
    hojas_crudas: hojas ya leidas (p. ej. de Google Sheets) en lugar de un archivo; ruta_excel se ignora.
    solo_cursos: en una matriz, controla solo estos cursos (palabras completas, sin importar tildes/mayusculas).
    exigir_cursos: cada cargo de la matriz debe tener los cursos controlados que trae su hoja (N/A = no aplica)."""
    ruta = Path(ruta_excel) if ruta_excel is not None else None
    if hojas_crudas is None and not ruta.exists():
        raise SystemExit(f"Error: No se encuentra el archivo '{ruta}'")
    if fechas not in ("emision", "realizacion", "vencimiento"):
        raise SystemExit("--fechas debe ser 'realizacion' o 'vencimiento'")
    fechas = "emision" if fechas == "realizacion" else fechas

    if empresa_forzada:
        empresa_defecto = empresa_forzada
    crudos = hojas_crudas if hojas_crudas is not None else pd.read_excel(ruta, sheet_name=None, dtype=str, header=None, keep_default_na=False, na_values=[""])
    # Hojas de personal retirado: se tratan aparte (suelen venir sin encabezado)
    retirados = {n: c for n, c in crudos.items() if normalizar(n).startswith(HOJAS_RETIRADOS)}
    hojas = _hojas_desde_crudos({n: c for n, c in crudos.items() if n not in retirados})
    if empresa_forzada:
        hojas = {n: _forzar_empresa(df, empresa_forzada) for n, df in hojas.items()}
    empresas, requisitos, datos = [], [], []
    for nombre_hoja, df in hojas.items():
        n = normalizar(nombre_hoja)
        if n in ("instrucciones", "leeme", "ayuda") or df.empty:
            continue
        if n == "empresas":
            empresas.append(df)
        elif n == "requisitos":
            requisitos.append(df)
        else:
            modo = _clasificar_hoja(df)
            datos.append((nombre_hoja, modo, df))
    if not (empresas or requisitos or retirados or any(m != "ignorada" for _, m, _ in datos)):
        vistas = "; ".join(f"hoja '{n}': {', '.join(str(c) for c in df.columns[:10])}" for n, df in hojas.items() if not df.empty)
        raise SystemExit(
            "No encontre una lista de documentos ni una matriz de cursos en el archivo. "
            "Una LISTA necesita una columna 'Documento' (o 'Curso'); una MATRIZ necesita una columna 'Cedula' "
            "(o 'Identificacion') y una de nombre. "
            f"Columnas que encontre -> {vistas or 'el archivo esta vacio'}.")

    conn = conectar(db_path)
    if es_v1(conn):
        conn.close()
        raise SystemExit("Esta base de datos es del esquema anterior. Ejecute primero:\n"
                         "  python scripts/migrar_v1.py")
    inicializar_db(conn)
    cur = conn.cursor()

    stats = {"empresas": set(), "responsables": 0, "nuevos": 0, "actualizados": 0,
             "sin_vencimiento": 0, "requisitos": 0, "personas_matriz": 0, "no_aplica": 0, "retirados": 0,
             "columnas_ignoradas": [], "columnas_no_controladas": [], "hojas_ignoradas": [], "errores": []}
    if verbose:
        print(f"Leyendo: {ruta.name if ruta else 'hojas en memoria'}")
    for df in empresas:
        _importar_empresas(cur, df, stats)
    for nombre_hoja, modo, df in datos:
        if modo == "largo":
            _importar_documentos(cur, df, empresa_defecto, stats)
        elif modo == "matriz":
            _importar_matriz(cur, df, empresa_defecto, stats, fechas, vigencia_defecto, solo_cursos, exigir_cursos)
        else:
            stats["hojas_ignoradas"].append(nombre_hoja)
    for df in requisitos:
        _importar_requisitos(cur, df, empresa_defecto, stats)
    for crudo in retirados.values():                    # al final: quien figura como retirado queda inactivo
        _marcar_retirados(cur, crudo, empresa_defecto, stats)
    if solo_validar:
        conn.rollback()
    else:
        conn.commit()
    conn.close()

    stats["solo_validar"] = solo_validar
    stats["empresas"] = sorted(stats["empresas"])
    if verbose:
        print("\nImportacion completada:")
        print(f"  Empresas:               {len(stats['empresas'])} ({', '.join(stats['empresas'])})")
        print(f"  Responsables cargados:  {stats['responsables']}")
        print(f"  Documentos nuevos:      {stats['nuevos']}")
        print(f"  Documentos actualizados:{stats['actualizados']:>3}")
        if stats["personas_matriz"]:
            print(f"  Personas en matriz:     {stats['personas_matriz']}")
        print(f"  Requisitos por cargo:   {stats['requisitos']}")
        print(f"  Cursos marcados N/A:    {stats['no_aplica']}   Personal retirado (inactivo): {stats['retirados']}")
        print(f"  Sin fecha de vencimiento (no generan alertas): {stats['sin_vencimiento']}")
        if stats["columnas_ignoradas"]:
            print(f"  Columnas de la matriz ignoradas (sin fechas): {', '.join(stats['columnas_ignoradas'])}")
        if stats["columnas_no_controladas"]:
            print(f"  Columnas fuera de los cursos controlados: {', '.join(stats['columnas_no_controladas'])}")
        if stats["hojas_ignoradas"]:
            print(f"  Hojas no reconocidas e ignoradas: {', '.join(stats['hojas_ignoradas'])}")
        print(f"  Filas con error:        {len(stats['errores'])}")
        for e in stats["errores"]:
            print(f"    - {e}")
        print(f"  Base de datos:          {db_path}")
    return stats


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Importa un Excel (formato largo, matriz, requisitos y empresas).")
    p.add_argument("archivo")
    p.add_argument("--empresa", default=DEFAULT_EMPRESA, help="Empresa para las filas que no traen la columna Empresa")
    p.add_argument("--fechas", default="realizacion", choices=["realizacion", "vencimiento"],
                   help="Que significan las fechas de una MATRIZ (por defecto: realizacion)")
    p.add_argument("--vigencia-defecto", type=int, default=None,
                   help="Vigencia en dias para las columnas de la matriz que no tengan una")
    p.add_argument("--solo", default="", help='Cursos a controlar en una matriz, separados por ; (ej: "BLS; ACLS; Duelo")')
    p.add_argument("--exigir", action="store_true", help="Los cursos de --solo se exigen a todos (N/A = no aplica)")
    p.add_argument("--validar", action="store_true", help="Procesa todo pero no guarda nada")
    a = p.parse_args()
    importar(a.archivo, empresa_defecto=a.empresa, fechas=a.fechas, vigencia_defecto=a.vigencia_defecto,
             solo_cursos=[x.strip() for x in a.solo.split(";") if x.strip()], exigir_cursos=a.exigir,
             solo_validar=a.validar)
