"""
Genera un archivo Excel de prueba con datos ficticios de empleados y cursos.
Uso: python scripts/crear_datos_prueba.py
"""
import sys
from pathlib import Path
from datetime import date, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
except ImportError:
    print("Instalando openpyxl...")

hoy = date.today()

datos = [
    # (Cedula, Nombre, Apellido, Cargo, Area, Curso, Fecha Realizacion, Fecha Vencimiento)
    # --- CRITICOS: vencen en <= 7 dias ---
    ("10234567", "Maria",    "Lopez",    "Enfermera",          "UCI",          "Bioseguridad",                hoy - timedelta(days=358), hoy + timedelta(days=3)),
    ("10234568", "Carlos",   "Ramirez",  "Medico",             "Urgencias",    "RCP Avanzado",                hoy - timedelta(days=360), hoy + timedelta(days=5)),
    ("10234569", "Ana",      "Torres",   "Aux. Enfermeria",    "Hospitalizacion","Manejo de Residuos",        hoy - timedelta(days=355), hoy + timedelta(days=7)),
    # --- ALERTA: vencen en 8-15 dias ---
    ("10234570", "Jorge",    "Mendez",   "Bacteriologo",       "Laboratorio",  "Bioseguridad",                hoy - timedelta(days=352), hoy + timedelta(days=10)),
    ("10234571", "Patricia", "Gomez",    "Terapeuta",          "Rehabilitacion","Manejo de Equipos",          hoy - timedelta(days=350), hoy + timedelta(days=14)),
    ("10234572", "Luis",     "Herrera",  "Auxiliar Admision",  "Admisiones",   "Atencion al Usuario",         hoy - timedelta(days=348), hoy + timedelta(days=12)),
    # --- PROXIMO: vencen en 16-30 dias ---
    ("10234573", "Sandra",   "Vargas",   "Enfermera",          "Pediatria",    "RCP Basico",                  hoy - timedelta(days=340), hoy + timedelta(days=20)),
    ("10234574", "Diego",    "Castro",   "Medico",             "Consulta Externa","Farmacologia Clinica",     hoy - timedelta(days=335), hoy + timedelta(days=25)),
    ("10234575", "Monica",   "Rios",     "Nutricionista",      "Nutricion",    "Bioseguridad",                hoy - timedelta(days=330), hoy + timedelta(days=28)),
    # --- NORMAL: vencen en > 30 dias ---
    ("10234576", "Andres",   "Salcedo",  "Medico",             "UCI",          "Soporte Vital Avanzado",      hoy - timedelta(days=300), hoy + timedelta(days=45)),
    ("10234577", "Claudia",  "Pineda",   "Aux. Enfermeria",    "Urgencias",    "Bioseguridad",                hoy - timedelta(days=280), hoy + timedelta(days=60)),
    ("10234578", "Felipe",   "Ortega",   "Medico",             "Cirugia",      "RCP Avanzado",                hoy - timedelta(days=250), hoy + timedelta(days=90)),
]

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "Cursos"

encabezados = ["Cedula", "Nombre", "Apellido", "Cargo", "Area",
               "Curso", "Fecha Realizacion", "Fecha Vencimiento"]

# Estilo encabezado
header_font = Font(bold=True, color="FFFFFF")
header_fill = PatternFill("solid", fgColor="1A5276")

for col, titulo in enumerate(encabezados, 1):
    celda = ws.cell(row=1, column=col, value=titulo)
    celda.font = header_font
    celda.fill = header_fill
    celda.alignment = Alignment(horizontal="center")

for fila, d in enumerate(datos, 2):
    for col, val in enumerate(d, 1):
        ws.cell(row=fila, column=col, value=val)

# Anchos de columna
anchos = [14, 12, 14, 22, 20, 30, 20, 20]
for col, ancho in enumerate(anchos, 1):
    ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = ancho

ruta = BASE_DIR / "datos_prueba.xlsx"
wb.save(ruta)
print(f"Archivo creado: {ruta}")
print(f"  {len(datos)} registros con fechas relativas a hoy ({hoy})")
