"""
Genera un Excel de prueba con datos ficticios de 3 empresas (alimentos, salud, ingenieria):
empleados con cursos, certificaciones, examenes medicos y afiliaciones, mas documentos
internos de cada empresa. Incluye una hoja "Empresas" con los responsables.

Uso: python scripts/crear_datos_prueba.py
     python scripts/importar_excel.py datos_prueba.xlsx
     python scripts/generar_alertas.py semanal --dry-run
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from core import hoy

h = hoy()


def d(dias: int) -> date:
    return h + timedelta(days=dias)


ALIM, SALUD, ING = "Alimentos del Caribe SAS", "Clinica Bahia SAS", "Ingenieria y Montajes SAS"

EMPRESAS = [
    # Empresa, NIT, Sector, Responsable, Email
    (ALIM,  "900111222-1", "Alimentos",  "Coordinador HSE Alimentos",  "hse.alimentos@ejemplo.com"),
    (SALUD, "900333444-2", "Salud",      "Coordinadora SST Clinica",   "sst.clinica@ejemplo.com; calidad.clinica@ejemplo.com"),
    (ING,   "900555666-3", "Ingenieria", "Supervisor HSE Ingenieria",  "hse.ingenieria@ejemplo.com"),
]

# Empresa, Cedula, Nombre, Apellido, Cargo, Area, Categoria, Documento, Referencia, Entidad, Fecha Emision, Fecha Vencimiento
DOCUMENTOS = [
    # ------------------------------------------------ Alimentos
    (ALIM, "1001", "Luz",     "Perez",   "Manipuladora", "Produccion", "Curso",         "Manipulacion de alimentos", "", "SENA", d(-350), d(15)),
    (ALIM, "1001", "Luz",     "Perez",   "Manipuladora", "Produccion", "Examen medico", "Examen medico ocupacional periodico", "", "IPS Salud Laboral", d(-340), d(25)),
    (ALIM, "1001", "Luz",     "Perez",   "Manipuladora", "Produccion", "Afiliacion",    "Afiliacion ARL", "", "", d(-20), d(10)),
    (ALIM, "1002", "Mario",   "Diaz",    "Operario",     "Empaque",    "Curso",         "Manipulacion de alimentos", "", "SENA", d(-400), d(-12)),
    (ALIM, "1002", "Mario",   "Diaz",    "Operario",     "Empaque",    "Afiliacion",    "Afiliacion EPS", "", "", d(-35), d(-5)),
    (ALIM, "1003", "Rosa",    "Nieto",   "Jefe de calidad", "Calidad", "Curso",         "Curso SG-SST 50 horas", "", "ARL", d(-300), d(65)),
    (ALIM, "", "", "", "", "", "Documento de empresa", "Permiso sanitario INVIMA (alimento riesgo medio)", "PS-2024-0457", "INVIMA", d(-2000), d(555)),
    (ALIM, "", "", "", "", "", "Certificacion",        "ISO 9001 (calidad)", "CERT-9001-77", "Certificadora X", d(-1000), d(95)),
    (ALIM, "", "", "", "", "", "Documento de empresa", "Autoevaluacion estandares minimos SG-SST", "", "", d(-370), d(-5)),
    # ------------------------------------------------ Salud
    (SALUD, "2001", "Maria",   "Lopez",   "Enfermera", "UCI",       "Curso",         "Primeros auxilios", "", "Cruz Roja", d(-358), d(3)),
    (SALUD, "2001", "Maria",   "Lopez",   "Enfermera", "UCI",       "Examen medico", "Examen medico ocupacional periodico", "", "IPS Salud Laboral", d(-300), d(65)),
    (SALUD, "2002", "Carlos",  "Ramirez", "Medico",    "Urgencias", "Licencia",      "Licencia en Seguridad y Salud en el Trabajo", "LIC-8891", "Secretaria de Salud", d(-3000), d(500)),
    (SALUD, "2002", "Carlos",  "Ramirez", "Medico",    "Urgencias", "Afiliacion",    "Afiliacion fondo de pensiones", "", "", d(-28), d(2)),
    (SALUD, "2003", "Ana",     "Torres",  "Auxiliar de enfermeria", "Hospitalizacion", "Curso", "Brigada de emergencias", "", "Bomberos", d(-200), d(160)),
    (SALUD, "", "", "", "", "", "Documento de empresa", "Inscripcion REPS / habilitacion de servicios", "REPS-0802", "Secretaria de Salud", d(-1000), d(20)),
    (SALUD, "", "", "", "", "", "Documento de empresa", "Plan de emergencias", "", "", d(-100), ""),   # sin vencimiento
    # ------------------------------------------------ Ingenieria
    (ING, "3001", "Jorge",   "Mendez",  "Soldador",      "Montaje", "Curso",         "Trabajo en alturas", "CERT-ALT-1", "Centro Autorizado", d(-340), d(-3)),
    (ING, "3001", "Jorge",   "Mendez",  "Soldador",      "Montaje", "Examen medico", "Examen medico con enfasis en alturas", "", "IPS Salud Laboral", d(-300), d(6)),
    (ING, "3002", "Patricia","Gomez",   "Ingeniera civil","Obra",   "Curso",         "Trabajo en alturas", "CERT-ALT-2", "Centro Autorizado", d(-100), d(260)),
    (ING, "3002", "Patricia","Gomez",   "Ingeniera civil","Obra",   "Afiliacion",    "Afiliacion ARL", "", "", d(-10), d(20)),
    (ING, "3003", "Luis",    "Herrera", "Supervisor HSE","HSE",     "Licencia",      "Licencia en Seguridad y Salud en el Trabajo", "LIC-5310", "Secretaria de Salud", d(-2000), d(1600)),
    (ING, "", "", "", "", "", "Certificacion",        "ISO 45001 (seguridad y salud)", "CERT-45001-12", "Certificadora Y", d(-900), d(195)),
    (ING, "", "", "", "", "", "Documento de empresa", "Matriz de identificacion de peligros", "", "", d(-340), d(25)),
]

ENCABEZADOS_DOC = ["Empresa", "Cedula", "Nombre", "Apellido", "Cargo", "Area", "Categoria", "Documento",
                   "Referencia", "Entidad", "Fecha Emision", "Fecha Vencimiento"]
ENCABEZADOS_EMP = ["Empresa", "NIT", "Sector", "Responsable", "Email"]


def _hoja(ws, encabezados, filas, anchos):
    for col, titulo in enumerate(encabezados, 1):
        c = ws.cell(row=1, column=col, value=titulo)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1A5276")
        c.alignment = Alignment(horizontal="center")
    for fila, datos in enumerate(filas, 2):
        for col, val in enumerate(datos, 1):
            ws.cell(row=fila, column=col, value=val if val != "" else None)
    for col, ancho in enumerate(anchos, 1):
        ws.column_dimensions[get_column_letter(col)].width = ancho


wb = openpyxl.Workbook()
_hoja(wb.active, ENCABEZADOS_DOC, DOCUMENTOS, [26, 10, 12, 12, 22, 16, 20, 42, 16, 22, 16, 18])
wb.active.title = "Documentos"
_hoja(wb.create_sheet("Empresas"), ENCABEZADOS_EMP, EMPRESAS, [28, 14, 12, 28, 50])

# Ejemplo estilo hospital: matriz persona x curso (celda = fecha de realizacion; vigencia en el encabezado)
CURSOS_H = ["RCP | 730", "Bioseguridad | 365", "Humanizacion del servicio | 730", "Manejo de residuos | 365",
            "Seguridad del paciente | 365", "Higiene de manos | 365", "Violencia sexual | 1095"]
PERSONAS_H = [
    ("1001", "Ana Perez", "Enfermera", "Urgencias", [-100, -400, -50, -30, -500, -20, -200]),
    ("1002", "Luis Ruiz", "Auxiliar de enfermeria", "Hospitalizacion", [-700, -100, None, -330, -10, "N/A", -300]),
    ("1003", "Eva Gil", "Auxiliar de enfermeria", "UCI", [-20, -360, -40, -340, -100, -5, None]),
    ("1004", "Mario Diaz", "Enfermera", "Consulta externa", [None] * 7),
]
wm = wb.create_sheet("Matriz")
_hoja(wm, ["Empresa", "Cedula", "Nombre completo", "Cargo", "Area"] + CURSOS_H,
      [["Hospital Universitario Demo", c, n, cg, a] +
       [(v if isinstance(v, str) else d(v).strftime("%d/%m/%Y")) if v is not None else "" for v in vals]
       for c, n, cg, a, vals in PERSONAS_H], [28, 10, 20, 24, 18] + [18] * 7)
wr = wb.create_sheet("Requisitos")
_hoja(wr, ["Empresa", "Cargo", "Documento"],
      [["Hospital Universitario Demo", cg, c.split(" | ")[0]] for cg in ("Enfermera", "Auxiliar de enfermeria") for c in CURSOS_H],
      [28, 24, 32])
wb["Empresas"].append(["Hospital Universitario Demo", "900111222", "salud", "Coord. HSE", "hse@hospital-demo.test"])

ruta = BASE_DIR / "datos_prueba.xlsx"
wb.save(ruta)
print(f"Archivo creado: {ruta}")
print(f"  {len(EMPRESAS)} empresas, {len(DOCUMENTOS)} documentos con fechas relativas a hoy ({h})")
