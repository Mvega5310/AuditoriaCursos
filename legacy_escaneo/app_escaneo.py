"""
Mini-app web: RRHH sube la foto/PDF de un certificado, Claude extrae los datos,
RRHH los revisa/corrige y confirma antes de que se guarden en la base de datos.

Uso:
  python app_escaneo.py
  Luego abrir http://localhost:5000 en el navegador.
"""
import sys
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from config import FLASK_SECRET_KEY
from scripts.extraer_certificado import extraer_datos_certificado
from scripts.guardar_certificado import guardar_registro

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

EXTENSIONES_PERMITIDAS = {".pdf", ".jpg", ".jpeg", ".png"}


@app.route("/", methods=["GET"])
def index():
    return render_template("escaneo_subir.html")


@app.route("/extraer", methods=["POST"])
def extraer():
    archivo = request.files.get("certificado")
    if not archivo or archivo.filename == "":
        flash("Selecciona un archivo antes de continuar.")
        return redirect(url_for("index"))

    extension = Path(archivo.filename).suffix.lower()
    if extension not in EXTENSIONES_PERMITIDAS:
        flash("Formato no soportado. Usa PDF, JPG o PNG.")
        return redirect(url_for("index"))

    datos_binarios = archivo.read()

    try:
        extraido = extraer_datos_certificado(datos_binarios)
    except Exception as e:
        flash(f"No se pudo leer el certificado: {e}")
        return redirect(url_for("index"))

    return render_template("escaneo_confirmar.html", datos=extraido)


@app.route("/guardar", methods=["POST"])
def guardar():
    try:
        guardar_registro(
            cedula=request.form.get("cedula", "").strip(),
            nombre=request.form.get("nombre", "").strip(),
            apellido=request.form.get("apellido", "").strip(),
            cargo=request.form.get("cargo", "").strip(),
            area=request.form.get("area", "").strip(),
            curso=request.form.get("curso", "").strip(),
            fecha_realizacion=request.form.get("fecha_realizacion", "").strip() or None,
            fecha_vencimiento=request.form.get("fecha_vencimiento", "").strip() or None,
        )
    except ValueError as e:
        flash(f"Faltan datos: {e}")
        return redirect(url_for("index"))

    return render_template("escaneo_exito.html")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
