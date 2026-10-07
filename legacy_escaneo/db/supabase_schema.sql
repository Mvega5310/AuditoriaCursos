-- Migración a Supabase (Postgres) del sistema de control de vencimiento de cursos.
-- Ejecutar una sola vez en Supabase: Project > SQL Editor > New query > pegar y correr.
-- Es idempotente (usa IF NOT EXISTS), se puede volver a correr sin romper nada.

CREATE TABLE IF NOT EXISTS empleados (
    id                  SERIAL PRIMARY KEY,
    cedula              TEXT    UNIQUE NOT NULL,
    nombre              TEXT    NOT NULL,
    apellido            TEXT    NOT NULL,
    cargo               TEXT    DEFAULT '',
    area                TEXT    DEFAULT '',
    activo              BOOLEAN DEFAULT true,
    fecha_registro      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cursos (
    id                  SERIAL PRIMARY KEY,
    nombre              TEXT    UNIQUE NOT NULL,
    descripcion         TEXT    DEFAULT '',
    periodicidad_dias   INTEGER DEFAULT 365
);

CREATE TABLE IF NOT EXISTS empleado_cursos (
    id                  SERIAL PRIMARY KEY,
    empleado_id         INTEGER NOT NULL REFERENCES empleados(id),
    curso_id            INTEGER NOT NULL REFERENCES cursos(id),
    fecha_realizacion   DATE,
    fecha_vencimiento   DATE    NOT NULL,
    fecha_registro      TIMESTAMPTZ DEFAULT now(),
    fecha_actualizacion TIMESTAMPTZ DEFAULT now(),
    UNIQUE (empleado_id, curso_id)
);

-- Auditoria: cada correo enviado por el Motor de Vencimientos queda aqui
CREATE TABLE IF NOT EXISTS log_alertas (
    id                  SERIAL PRIMARY KEY,
    fecha_envio         TIMESTAMPTZ NOT NULL,
    tipo_alerta         TEXT    NOT NULL,
    cursos_notificados  INTEGER DEFAULT 0,
    destinatario        TEXT    NOT NULL,
    estado              TEXT    NOT NULL,   -- enviado | error_envio | sin_registros | ingreso
    detalle             TEXT    DEFAULT ''
);

-- Cursor de "ultimo visto" que usa el workflow Monitor de Ingresos para detectar
-- empleados/cursos nuevos o renovados por polling incremental.
CREATE TABLE IF NOT EXISTS control_monitor (
    clave       TEXT PRIMARY KEY,
    valor       TEXT,
    actualizado TIMESTAMPTZ DEFAULT now()
);

INSERT INTO control_monitor (clave, valor)
VALUES
    ('ultimo_empleado_registro', '1970-01-01T00:00:00Z'),
    ('ultimo_ec_actualizacion',  '1970-01-01T00:00:00Z')
ON CONFLICT (clave) DO NOTHING;

-- Mantiene fecha_actualizacion al dia en cada UPDATE de empleado_cursos
-- (necesario para que el Monitor detecte renovaciones, no solo altas nuevas).
CREATE OR REPLACE FUNCTION set_fecha_actualizacion()
RETURNS TRIGGER AS $$
BEGIN
  NEW.fecha_actualizacion = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_empleado_cursos_actualizacion ON empleado_cursos;
CREATE TRIGGER trg_empleado_cursos_actualizacion
  BEFORE UPDATE ON empleado_cursos
  FOR EACH ROW
  EXECUTE FUNCTION set_fecha_actualizacion();
