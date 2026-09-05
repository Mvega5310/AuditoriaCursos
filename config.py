from pathlib import Path
from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RRHH_EMAIL = os.getenv("RRHH_EMAIL")

DB_PATH = BASE_DIR / "db" / "cursos.db"
LOG_PATH = BASE_DIR / "logs" / "alertas.log"
TEMPLATE_PATH = BASE_DIR / "templates" / "alerta_email.html"

# Ventana de dias y configuracion de cada tipo de alerta
ALERTAS = {
    "diaria": {
        "dias": 7,
        "asunto": "[URGENTE] Cursos a vencer en 7 dias o menos",
        "label": "Alerta Diaria — Vencen en 7 dias o menos",
    },
    "semanal": {
        "dias": 30,
        "asunto": "Reporte Semanal — Cursos proximos a vencer",
        "label": "Reporte Semanal — Vencen en los proximos 30 dias",
    },
    "quincenal": {
        "dias": 15,
        "asunto": "Reporte Quincenal — Cursos proximos a vencer",
        "label": "Reporte Quincenal — Vencen en los proximos 15 dias",
    },
    "mensual": {
        "dias": 60,
        "asunto": "Reporte Mensual — Panorama de vencimientos",
        "label": "Reporte Mensual — Vencen en los proximos 60 dias",
    },
}
