from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# Destinatario de respaldo: recibe la alerta de una empresa que no tiene responsables registrados
RRHH_EMAIL = os.getenv("RRHH_EMAIL")
# Opcional: recibe copia de TODAS las alertas de TODAS las empresas (administrador del sistema)
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")

DB_PATH = BASE_DIR / "db" / "cursos.db"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
LOG_PATH = BASE_DIR / "logs" / "alertas.log"
SALIDAS_DIR = BASE_DIR / "salidas"          # HTML generado con --dry-run
TEMPLATE_PATH = BASE_DIR / "templates" / "alerta_email.html"

ZONA_HORARIA = "America/Bogota"

# Empresa a la que se asignan las filas del Excel que no traen columna "Empresa"
DEFAULT_EMPRESA = "Empresa principal"

# Limites (en dias restantes) de cada estado. Menos de 0 dias = VENCIDO.
UMBRALES = {"critico": 7, "alerta": 15, "proximo": 30}

# Ventana de dias y configuracion de cada tipo de alerta.
# Todas las alertas incluyen ademas los documentos ya VENCIDOS.
# 'faltantes': incluir los documentos exigidos por el cargo que nunca se han registrado.
ALERTAS = {
    "diaria": {
        "dias": 7,
        "faltantes": False,   # la diaria solo trae lo urgente
        "asunto": "[URGENTE] Documentos vencidos o por vencer en 7 dias o menos",
        "label": "Alerta Diaria — Vencidos y por vencer en 7 dias o menos",
    },
    "semanal": {
        "dias": 30,
        "faltantes": True,
        "asunto": "Reporte Semanal — Documentos vencidos y proximos a vencer",
        "label": "Reporte Semanal — Vencidos y por vencer en los proximos 30 dias",
    },
    "quincenal": {
        "dias": 15,
        "faltantes": True,
        "asunto": "Reporte Quincenal — Documentos vencidos y proximos a vencer",
        "label": "Reporte Quincenal — Vencidos y por vencer en los proximos 15 dias",
    },
    "mensual": {
        "dias": 60,
        "faltantes": True,
        "asunto": "Reporte Mensual — Panorama de vencimientos",
        "label": "Reporte Mensual — Vencidos y por vencer en los proximos 60 dias",
    },
}

# Etiquetas legibles de cada categoria
CATEGORIAS = {
    "curso":             "Curso",
    "certificacion":     "Certificación",
    "licencia":          "Licencia",
    "examen_medico":     "Examen médico",
    "afiliacion":        "Afiliación",
    "documento_empresa": "Documento de empresa",
}
