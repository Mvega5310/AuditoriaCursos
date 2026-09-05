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
