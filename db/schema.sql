-- Base de datos: Control de vencimientos de documentacion (multi-empresa)
-- v2: empresas, empleados, tipos de documento y documentos
--     (cursos, certificaciones, licencias, examenes medicos, afiliaciones,
--      documentos internos de la empresa)
--
-- Convenciones:
--   * Fechas en texto ISO (YYYY-MM-DD).
--   * documentos.empleado_id NULL  => el documento es de la EMPRESA.
--   * documentos.fecha_vencimiento NULL => documento sin vencimiento (no genera alertas).
--   * Examenes medicos: guardar SOLO fechas/vigencia. No guardar diagnosticos
--     ni resultados clinicos (datos sensibles, Ley 1581 de 2012).

CREATE TABLE IF NOT EXISTS empresas (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre  TEXT    UNIQUE NOT NULL,
    nit     TEXT    DEFAULT '',
    sector  TEXT    DEFAULT '',
    activa  INTEGER DEFAULT 1
);

-- Quien recibe las alertas de cada empresa (puede haber varios)
CREATE TABLE IF NOT EXISTS responsables (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL,
    nombre      TEXT    DEFAULT '',
    email       TEXT    NOT NULL,
    rol         TEXT    DEFAULT 'HSE',
    activo      INTEGER DEFAULT 1,
    UNIQUE (empresa_id, email),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

CREATE TABLE IF NOT EXISTS empleados (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL,
    cedula      TEXT    NOT NULL,
    nombre      TEXT    NOT NULL,
    apellido    TEXT    NOT NULL,
    cargo       TEXT    DEFAULT '',
    area        TEXT    DEFAULT '',
    activo      INTEGER DEFAULT 1,
    UNIQUE (empresa_id, cedula),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);

-- Catalogo configurable: cada tipo trae su categoria, a quien aplica y su vigencia.
-- periodicidad_dias NULL = la vigencia no es fija; se debe indicar la fecha de vencimiento.
CREATE TABLE IF NOT EXISTS tipos_documento (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    sector             TEXT    NOT NULL DEFAULT 'general',   -- general | alimentos | salud | construccion | (propio)
    categoria          TEXT    NOT NULL,   -- curso | certificacion | licencia | examen_medico | afiliacion | documento_empresa
    nombre             TEXT    NOT NULL,
    aplica_a           TEXT    NOT NULL DEFAULT 'empleado' CHECK (aplica_a IN ('empleado', 'empresa')),
    periodicidad_dias  INTEGER,
    norma_referencia   TEXT    DEFAULT '',
    nota               TEXT    DEFAULT '',
    UNIQUE (categoria, nombre)
);

CREATE TABLE IF NOT EXISTS documentos (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id         INTEGER NOT NULL,
    empleado_id        INTEGER,                 -- NULL = documento de la empresa
    tipo_id            INTEGER NOT NULL,
    referencia         TEXT    NOT NULL DEFAULT '',   -- numero de certificado, poliza, etc.
    entidad_emisora    TEXT    DEFAULT '',
    fecha_emision      TEXT,
    fecha_vencimiento  TEXT,                    -- NULL = sin vencimiento
    avisar_dias        INTEGER,                 -- NULL = solo las ventanas normales; N = aparece en TODAS las alertas desde N dias antes
    archivo            TEXT    DEFAULT '',      -- ruta o enlace al soporte (PDF/foto)
    observaciones      TEXT    DEFAULT '',
    actualizado_en     TEXT,
    FOREIGN KEY (empresa_id)  REFERENCES empresas(id),
    FOREIGN KEY (empleado_id) REFERENCES empleados(id),
    FOREIGN KEY (tipo_id)     REFERENCES tipos_documento(id)
);

-- Un mismo documento (empresa + titular + tipo + referencia) se actualiza, no se duplica
CREATE UNIQUE INDEX IF NOT EXISTS ux_documentos_identidad
    ON documentos (empresa_id, IFNULL(empleado_id, 0), tipo_id, referencia);

CREATE INDEX IF NOT EXISTS ix_documentos_vencimiento ON documentos (fecha_vencimiento);

-- Historial: cuando se renueva un documento (cambia su vencimiento) se conserva la version anterior
CREATE TABLE IF NOT EXISTS documentos_historial (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id       INTEGER NOT NULL,
    fecha_emision      TEXT,
    fecha_vencimiento  TEXT,
    archivo            TEXT    DEFAULT '',
    registrado_en      TEXT    NOT NULL,
    FOREIGN KEY (documento_id) REFERENCES documentos(id)
);

CREATE TRIGGER IF NOT EXISTS trg_documentos_historial
BEFORE UPDATE OF fecha_vencimiento ON documentos
WHEN OLD.fecha_vencimiento IS NOT NEW.fecha_vencimiento
BEGIN
    INSERT INTO documentos_historial (documento_id, fecha_emision, fecha_vencimiento, archivo, registrado_en)
    VALUES (OLD.id, OLD.fecha_emision, OLD.fecha_vencimiento, OLD.archivo, datetime('now'));
END;

-- Requisitos por cargo: que documentos DEBE tener cada cargo (permite detectar los que nunca se han registrado).
-- cargo_clave = cargo normalizado (sin tildes, minusculas); '*' = todos los cargos de la empresa.
CREATE TABLE IF NOT EXISTS requisitos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL,
    cargo       TEXT    NOT NULL,
    cargo_clave TEXT    NOT NULL,
    tipo_id     INTEGER NOT NULL,
    UNIQUE (empresa_id, cargo_clave, tipo_id),
    FOREIGN KEY (empresa_id) REFERENCES empresas(id),
    FOREIGN KEY (tipo_id)    REFERENCES tipos_documento(id)
);

-- Registro de auditoria: cada correo (por empresa) queda guardado aqui
CREATE TABLE IF NOT EXISTS log_alertas (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_envio             TEXT    NOT NULL,
    tipo_alerta             TEXT    NOT NULL,
    empresa_id              INTEGER,
    documentos_notificados  INTEGER DEFAULT 0,
    destinatario            TEXT    NOT NULL,
    estado                  TEXT    NOT NULL,   -- enviado | error_envio | sin_registros | sin_destinatario
    detalle                 TEXT    DEFAULT '',
    FOREIGN KEY (empresa_id) REFERENCES empresas(id)
);
