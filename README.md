# autCursos — Control de vencimientos de documentación (multi-empresa)

Controla, para **varias empresas**, los vencimientos de:

| Categoría | Ejemplos |
|---|---|
| Curso | Trabajo en alturas, primeros auxilios, SG-SST 50 h |
| Certificación | ISO 9001 / 45001 (empresa), competencia laboral (empleado) |
| Licencia | Licencia SST, licencia de conducción |
| Examen médico | Ocupacional periódico, con énfasis en alturas |
| Afiliación | EPS, ARL, pensiones, caja de compensación |
| Documento de empresa | Autoevaluación SG-SST, plan anual, registro sanitario INVIMA, REPS, Cámara de Comercio |

Envía por correo, a los responsables de **cada empresa**, los documentos **vencidos** y los próximos a vencer.

## Puesta en marcha

```bash
pip install -r requirements.txt
copy .env.example .env          # y completar GMAIL_USER / GMAIL_APP_PASSWORD
python scripts/crear_datos_prueba.py
python scripts/importar_excel.py datos_prueba.xlsx
python scripts/generar_alertas.py semanal --dry-run     # genera HTML en salidas/, no envía correos
python scheduler.py                                     # alertas automáticas
```

Pruebas: `pip install -r requirements-dev.txt` y `python -m pytest -q`.

## Si ya usaba la versión anterior (una sola empresa)

```bash
python scripts/migrar_v1.py --empresa "Nombre de su empresa"
```

Hace copia de seguridad (`cursos.db.bak-AAAAMMDD`), mueve todo a la empresa indicada y verifica los conteos.
Si algo falla, restaura la copia automáticamente. Las tablas antiguas quedan como `*_v1` dentro del mismo archivo.

## Formato del Excel

**Hoja `Documentos`** (una fila por documento):

`Empresa | Cedula | Nombre | Apellido | Cargo | Area | Categoria | Documento | Referencia | Entidad | Fecha Emision | Fecha Vencimiento | Avisar Dias | Archivo | Observaciones`

- **Con cédula** → documento del empleado. **Sin cédula** → documento de la empresa.
- Obligatorios: `Documento` y una fecha de vencimiento (o un tipo con vigencia fija + `Fecha Emision`).
- Sin `Empresa` se usa `--empresa` o "Empresa principal". El formato anterior (`Curso`, `Fecha Realizacion`) sigue funcionando.
- Fechas `dd/mm/aaaa` o `aaaa-mm-dd`.
- Un documento sin fecha de vencimiento se guarda pero no genera alertas.
- Importar de nuevo **actualiza sin duplicar**; al renovar, la versión anterior queda en `documentos_historial`.

**Hoja `Empresas`** (opcional): `Empresa | NIT | Sector | Responsable | Email`
(varios correos separados por `;` o `,`). Cada empresa recibe sus alertas en sus propios correos.

## Formato matriz (una fila por persona, una columna por curso)

Pensado para listas como las de un hospital (≈7 cursos por enfermera/auxiliar). Hoja `Matriz`:

`Empresa | Cedula | Nombre completo | Cargo | Area | RCP \| 730 | Bioseguridad \| 365 | ...`

- Cada celda es una **fecha**; vacía, `N/A`, `-` o `Pendiente` = no registrado. Las columnas sin fechas (observaciones, consecutivos) se ignoran.
- Por defecto la fecha es de **realización** y el vencimiento se calcula con la vigencia: la del encabezado (`RCP | 730` = 730 días), la del catálogo, o `--vigencia-defecto N`.
  Si las fechas ya son de vencimiento: `--fechas vencimiento`.
- Todas las personas de la matriz se registran, aunque no tengan ningún curso (así se detectan sus faltantes).
- Un Excel puede mezclar hojas (`Documentos`, `Matriz`, `Requisitos`, `Empresas`); cada una se reconoce sola.

```bash
python scripts/importar_excel.py hospital.xlsx --vigencia-defecto 365
```

## Requisitos por cargo y documentos faltantes

Hoja `Requisitos`: `Empresa | Cargo | Documento` (cargo `*` = todos los cargos). El sistema detecta a quien **nunca** registró un documento exigido
y lo incluye en una sección "sin registrar" de los reportes **semanal, quincenal y mensual** (la diaria solo trae lo urgente; se cambia con `faltantes` en `config.py`).

```bash
python scripts/agregar_documento.py requisito --empresa "Clinica Bahia SAS" --cargo "Auxiliar de enfermeria" --documentos "RCP; Bioseguridad"
python scripts/agregar_documento.py requisitos --empresa "Clinica Bahia SAS"     # ver lo definido
```

Un cargo se compara sin importar mayúsculas ni tildes. Un documento registrado sin fecha de vencimiento cuenta como presente.

## Agregar un documento específico (sin armar un Excel)

Para que una empresa pida alertar sobre un documento que no está en el catálogo, o con un aviso propio:

```bash
# Documento de la EMPRESA, con aviso desde 45 días antes de vencer
python scripts/agregar_documento.py documento --empresa "Clinica Bahia SAS" --documento "Poliza todo riesgo" --referencia POL-123 --vence 15/03/2027 --avisar-dias 45

# Documento de un EMPLEADO (con cédula)
python scripts/agregar_documento.py documento --empresa "Ingenieria y Montajes SAS" --cedula 3001 --nombre Jorge --apellido Mendez --documento "Certificacion de soldador" --categoria certificacion --vence 2027-01-20

# Tipo nuevo con vigencia fija: con --emision se calcula solo el vencimiento
python scripts/agregar_documento.py documento --empresa "Alimentos del Caribe SAS" --documento "Informe de auditoria sanitaria" --periodicidad 365 --emision 01/10/2026 --sector alimentos

# Ver el catálogo
python scripts/agregar_documento.py listar --sector salud
```

- Si el tipo de documento no existe, **se crea en el catálogo** (con `--periodicidad` y `--sector` si los indica).
- Repetir el comando con otra fecha **renueva** el documento y conserva la versión anterior en el historial.
- `--avisar-dias N` (o la columna **Avisar Dias** del Excel): el documento aparece en **todas** las alertas (diaria, semanal, etc.) desde N días antes de vencer, además de las ventanas normales.
- Sin fecha de vencimiento el documento se guarda, pero no genera alertas.

## Estados

`VENCIDO` (< 0 días) · `CRITICO` (0–7) · `ALERTA` (8–15) · `PROXIMO` (16–30) · `NORMAL` (> 30). Los umbrales se ajustan en `config.py`.

## Catálogo de tipos y vigencias

La tabla `tipos_documento` (se carga desde `catalogo.py`) define la vigencia de cada tipo y está organizada por **sector**:
`general` (aplica a todas), `alimentos`, `salud` y `construccion` (ingeniería, metalmecánica, obras). Cada empresa usa el
sector que le corresponda más el general; `agregar_documento.py listar --sector X` muestra lo disponible. Es un **punto de partida**:
las vigencias legales cambian y algunas fuentes difieren (por ejemplo, trabajo en alturas). Donde la vigencia no es fija
se deja vacía y se usa la fecha que figura en el certificado. Revise la columna `nota` y confirme contra la norma o la entidad emisora.
Cualquier documento con un nombre nuevo se crea automáticamente en el catálogo al importar.

## Datos personales

- **Exámenes médicos:** registre solo la fecha y la vigencia. No guarde diagnósticos ni resultados clínicos (datos sensibles, Ley 1581 de 2012).
- Los correos incluyen nombre y cédula: envíelos solo a responsables autorizados.
- `db/*.db`, `.env` y `salidas/` están en `.gitignore`; no los suba al repositorio.

## Estructura

```
config.py            configuración (umbrales, alertas, categorías)
core.py              base de datos, fechas, estados
catalogo.py          tipos de documento iniciales
db/schema.sql        esquema v2
scripts/importar_excel.py   Excel -> base de datos
scripts/agregar_documento.py agrega un documento específico / lista el catálogo
scripts/migrar_v1.py        migra la base anterior
scripts/generar_alertas.py  alertas por empresa (--dry-run)
scheduler.py         programa las alertas (Lun-Vie 07:00, etc.)
templates/alerta_email.html
tests/test_flujo.py, tests/test_agregar.py, tests/test_matriz.py
```

## Aplicación web (registro de empresas)

`web.py` permite que cada empresa se registre, verifique su correo, inicie sesión, suba su Excel, vea su panel y gestione los
responsables que reciben las alertas. **Cada usuario pertenece a una sola empresa** y todas las consultas se filtran por ella
(la prueba `tests/test_web.py::test_aislamiento_entre_empresas` lo verifica; al importar, la columna *Empresa* del archivo se ignora).

```bash
set FLASK_SECRET_KEY=una-cadena-larga   # o en .env
python web.py                            # http://localhost:8000
```

Para dar acceso a una empresa que ya existe en la base (por ejemplo, la migrada desde v1):
`python scripts/crear_usuario.py --empresa "Hospital Universitario" --email admin@hospital.com`

## Base de datos: SQLite o Postgres

- Sin `DATABASE_URL` se usa el archivo SQLite `db/cursos.db` (desarrollo).
- Con `DATABASE_URL` (Railway la inyecta) se usa **Postgres**; el esquema se crea solo al arrancar. El esquema de Postgres se deriva de `db/schema.sql`.
- Pruebas sobre Postgres: `TEST_DATABASE_URL=postgresql://... python -m pytest -q` (las marcadas `solo_sqlite` se omiten).
- `migrar_v1.py` solo existe para SQLite.

## Despliegue en Railway

1. Proyecto nuevo desde este repositorio de GitHub (usa el `Dockerfile`) y un servicio **PostgreSQL** en el mismo proyecto.
2. Variables del servicio web: `DATABASE_URL=${{Postgres.DATABASE_URL}}`, `FLASK_SECRET_KEY`, `APP_URL` (dominio público),
   `COOKIE_SECURE=1`, `RUN_SCHEDULER=1`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `RRHH_EMAIL`, `ADMIN_EMAIL`.
3. Generar un dominio público. Las alertas programadas corren dentro del mismo proceso (`RUN_SCHEDULER=1`), por eso el servicio debe
   mantener **una sola réplica**.
