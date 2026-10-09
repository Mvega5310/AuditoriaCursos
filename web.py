"""
Aplicacion web multiempresa: registro, verificacion de correo, login, panel,
importacion de Excel y responsables de alertas.

Regla de oro: TODA consulta lleva el empresa_id del usuario en sesion. Un usuario
nunca recibe datos de otra empresa (ver tests/test_web.py::test_aislamiento).

Uso local:   python web.py            -> http://localhost:8000
Produccion:  gunicorn 'web:create_app()' --workers 1 --threads 4
             (un solo worker: SQLite, el limite de intentos de login y el scheduler viven en el proceso)
"""
import logging
import os
import re
import secrets
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "scripts"))

import config                                                  # noqa: E402
import sheets                                                  # noqa: E402
import correo                                                  # noqa: E402
from catalogo import CATALOGO                                  # noqa: E402
from core import conectar, hoy, inicializar_db, normalizar     # noqa: E402

log = logging.getLogger(__name__)

SECTORES = {"salud": "Salud", "alimentos": "Alimentos", "construccion": "Construcción e ingeniería",
            "general": "Otro sector"}
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
INTENTOS_MAX, VENTANA_SEG = 5, 15 * 60


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder=str(BASE_DIR / "templates" / "web"))
    app.config.update(
        SECRET_KEY=config.FLASK_SECRET_KEY,
        DB_PATH=config.DB_PATH,
        APP_URL=config.APP_URL,
        RUN_SCHEDULER=config.RUN_SCHEDULER,
        MAX_CONTENT_LENGTH=config.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=config.COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=8 * 3600,
        INTENTOS={},
    )
    app.config.update(overrides or {})
    if not app.config["SECRET_KEY"]:
        if app.config.get("TESTING") or os.getenv("FLASK_DEBUG") == "1":
            app.config["SECRET_KEY"] = secrets.token_hex(32)
        else:
            raise RuntimeError("Defina FLASK_SECRET_KEY (cadena aleatoria larga) en el entorno.")

    with conectar(app.config["DB_PATH"]) as conn:
        inicializar_db(conn)

    serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])

    # ------------------------------------------------------------ infraestructura
    def db():
        if "db" not in g:
            g.db = conectar(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def _cerrar(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.before_request
    def _cargar_usuario_y_csrf():
        g.usuario = None
        uid = session.get("uid")
        if uid:
            g.usuario = db().execute("""
                SELECT u.id, u.email, u.rol, u.empresa_id, e.nombre AS empresa, e.sector
                FROM usuarios u JOIN empresas e ON e.id = u.empresa_id
                WHERE u.id = ? AND u.verificado = 1 AND e.activa = 1""", (uid,)).fetchone()
            if g.usuario is None:
                session.clear()
        if request.method == "POST":
            enviado = request.form.get("csrf", "")
            if not enviado or not secrets.compare_digest(enviado, session.get("csrf", "")):
                abort(400, "Sesión expirada o formulario inválido. Recargue la página.")

    @app.context_processor
    def _plantillas():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf": session["csrf"], "usuario": g.get("usuario")}

    @app.after_request
    def _cabeceras(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def login_requerido(vista):
        def envuelta(*a, **k):
            if g.usuario is None:
                return redirect(url_for("login"))
            return vista(*a, **k)
        envuelta.__name__ = vista.__name__
        return envuelta

    def enviar_verificacion(usuario_id: int, email: str) -> None:
        token = serializer.dumps(usuario_id, salt="verificar-correo")
        enlace = f"{app.config['APP_URL']}/verificar/{token}"
        app.config["ULTIMO_ENLACE"] = enlace if app.config.get("TESTING") else None
        if not correo.proveedor() or app.config.get("TESTING"):
            log.warning("Correo no configurado: enlace de verificacion para %s -> %s", email, enlace)
            return
        from scripts.generar_alertas import enviar_email
        html = (f"<p>Bienvenido a autCursos.</p><p>Confirme su correo para activar la cuenta "
                f"(el enlace vence en 48 horas):</p><p><a href='{enlace}'>{enlace}</a></p>")
        enviar_email("Confirme su cuenta en autCursos", html, [email])

    def bloqueado(clave: str) -> bool:
        ahora = time.time()
        intentos = [t for t in app.config["INTENTOS"].get(clave, []) if ahora - t < VENTANA_SEG]
        app.config["INTENTOS"][clave] = intentos
        return len(intentos) >= INTENTOS_MAX

    # ------------------------------------------------------------ registro y acceso
    @app.get("/")
    def inicio():
        return redirect(url_for("panel" if g.usuario else "login"))

    @app.route("/registro", methods=["GET", "POST"])
    def registro():
        if request.method == "GET":
            return render_template("registro.html", sectores=SECTORES, form={})
        f = {k: request.form.get(k, "").strip() for k in ("empresa", "nit", "sector", "email")}
        clave = request.form.get("password", "")
        errores = []
        if len(f["empresa"]) < 3:
            errores.append("Escriba el nombre de la empresa.")
        if f["sector"] not in SECTORES:
            errores.append("Seleccione el sector.")
        if not RE_EMAIL.match(f["email"]):
            errores.append("Correo no válido.")
        if len(clave) < MIN_PASSWORD:
            errores.append(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.")
        if clave != request.form.get("password2", ""):
            errores.append("Las contraseñas no coinciden.")
        if not request.form.get("consentimiento"):
            errores.append("Debe aceptar el tratamiento de datos personales.")
        conn = db()
        if not errores and conn.execute("SELECT 1 FROM empresas WHERE nombre = ?", (f["empresa"],)).fetchone():
            errores.append("Ya existe una empresa con ese nombre. Si es la suya, pida acceso a su administrador.")
        if errores:
            for e in errores:
                flash(e, "error")
            return render_template("registro.html", sectores=SECTORES, form=f), 400

        mensaje = "Revise su correo: le enviamos un enlace para activar la cuenta."
        if conn.execute("SELECT 1 FROM usuarios WHERE email = ?", (f["email"].lower(),)).fetchone():
            flash(mensaje, "ok")          # misma respuesta: no revela si el correo ya existe
            return redirect(url_for("login"))
        ahora = datetime.now().isoformat(timespec="seconds")
        empresa_id = conn.execute(
            "INSERT INTO empresas (nombre, nit, sector, consentimiento_en) VALUES (?, ?, ?, ?)",
            (f["empresa"], f["nit"], f["sector"], ahora)).lastrowid
        usuario_id = conn.execute(
            "INSERT INTO usuarios (empresa_id, email, password_hash, rol, creado_en) VALUES (?, ?, ?, 'admin', ?)",
            (empresa_id, f["email"].lower(), generate_password_hash(clave), ahora)).lastrowid
        # El correo del administrador tambien recibe las alertas hasta que agregue otros responsables
        conn.execute("INSERT INTO responsables (empresa_id, nombre, email) VALUES (?, 'Administrador', ?)",
                     (empresa_id, f["email"].lower()))
        conn.commit()
        enviar_verificacion(usuario_id, f["email"].lower())
        flash(mensaje, "ok")
        return redirect(url_for("login"))

    @app.get("/verificar/<token>")
    def verificar(token):
        try:
            uid = serializer.loads(token, salt="verificar-correo", max_age=48 * 3600)
        except SignatureExpired:
            flash("El enlace venció. Inicie sesión para solicitar uno nuevo.", "error")
            return redirect(url_for("login"))
        except BadSignature:
            abort(404)
        conn = db()
        conn.execute("UPDATE usuarios SET verificado = 1 WHERE id = ?", (uid,))
        conn.commit()
        flash("Correo confirmado. Ya puede iniciar sesión.", "ok")
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            return render_template("login.html")
        email = request.form.get("email", "").strip().lower()
        clave_rl = f"{request.remote_addr}|{email}"
        if bloqueado(clave_rl):
            flash("Demasiados intentos. Espere unos minutos.", "error")
            return render_template("login.html"), 429
        u = db().execute("SELECT id, password_hash, verificado FROM usuarios WHERE email = ?", (email,)).fetchone()
        if not u or not check_password_hash(u["password_hash"], request.form.get("password", "")):
            app.config["INTENTOS"].setdefault(clave_rl, []).append(time.time())
            flash("Correo o contraseña incorrectos.", "error")
            return render_template("login.html"), 401
        if not u["verificado"]:
            enviar_verificacion(u["id"], email)
            flash("Debe confirmar su correo. Le enviamos un nuevo enlace.", "error")
            return render_template("login.html"), 403
        session.clear()
        session.permanent = True
        session["uid"] = u["id"]
        return redirect(url_for("panel"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------ panel
    @app.get("/panel")
    @login_requerido
    def panel():
        from scripts.generar_alertas import consultar_faltantes, consultar_vencimientos
        eid = g.usuario["empresa_id"]
        conn = db()
        registros = consultar_vencimientos(conn, eid, 60)
        resumen = {k: sum(1 for r in registros if r["clase"] == k) for k in ("vencido", "critico", "alerta", "proximo")}
        faltantes = consultar_faltantes(conn, eid)
        empleados = conn.execute("SELECT COUNT(*) FROM empleados WHERE empresa_id = ? AND activo = 1", (eid,)).fetchone()[0]
        return render_template("panel.html", registros=registros[:300], total=len(registros), resumen=resumen,
                               faltantes=faltantes, empleados=empleados, hoy=hoy().strftime("%d/%m/%Y"))

    @app.route("/importar", methods=["GET", "POST"])
    @login_requerido
    def importar_vista():
        if request.method == "GET":
            return render_template("importar.html", stats=None)
        archivo = request.files.get("archivo")
        if not archivo or not archivo.filename.lower().endswith(".xlsx"):
            flash("Suba un archivo Excel (.xlsx).", "error")
            return render_template("importar.html", stats=None), 400
        from scripts.importar_excel import importar
        fd, ruta = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        validar = request.form.get("accion") == "validar"
        fechas = request.form.get("fechas", "realizacion")
        if fechas not in ("realizacion", "vencimiento"):
            fechas = "realizacion"
        try:
            archivo.save(ruta)
            stats = importar(ruta, db_path=app.config["DB_PATH"], verbose=False,
                             fechas=fechas,
                             empresa_forzada=g.usuario["empresa"], solo_validar=validar,
                             **sheets.opciones_importacion(db(), g.usuario["empresa_id"]))
        except SystemExit as exc:            # mensajes propios del importador: explican que falta
            mensaje = re.sub(r"^Error:\s*", "", str(exc))
            if "migrar_v1" in mensaje or "scripts/" in mensaje:       # instrucciones de consola: no son para el usuario web
                log.error("Importacion bloqueada (empresa %s): %s", g.usuario["empresa_id"], mensaje)
                mensaje = "El sistema no pudo procesar el archivo en este momento. Contacte al administrador."
            flash(mensaje, "error")
            return render_template("importar.html", stats=None), 400
        except Exception as exc:
            log.warning("Importacion fallida (empresa %s): %r", g.usuario["empresa_id"], exc)
            flash("No se pudo leer el archivo. Verifique que sea un .xlsx válido y no esté protegido con contraseña.", "error")
            return render_template("importar.html", stats=None), 400
        finally:
            Path(ruta).unlink(missing_ok=True)
        return render_template("importar.html", stats=stats)

    @app.get("/plantilla.xlsx")
    def plantilla_descarga():
        from flask import Response
        from plantilla import construir_plantilla
        return Response(construir_plantilla(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": "attachment; filename=plantilla_autcursos.xlsx"})

    @app.route("/sheets", methods=["GET", "POST"])
    @login_requerido
    def hoja_google():
        eid, conn = g.usuario["empresa_id"], db()

        def estado():
            return conn.execute("""SELECT sheet_url, sheet_fechas, sheet_sync_en, sheet_sync_estado, sheet_sync_detalle,
                                          cursos_controlados, exigir_cursos
                                   FROM empresas WHERE id = ?""", (eid,)).fetchone()

        if request.method == "POST":
            accion = request.form.get("accion")
            actual = estado()
            if accion == "quitar":
                conn.execute("UPDATE empresas SET sheet_url = NULL, sheet_sync_estado = NULL, sheet_sync_detalle = NULL "
                             "WHERE id = ?", (eid,))
                conn.commit()
                flash("Se desconectó la hoja. Los documentos ya cargados se conservan.", "ok")
                return redirect(url_for("hoja_google"))
            if accion == "opciones":
                cursos = "\n".join(sheets.lineas_cursos(request.form.get("cursos", "")[:3000]))
                conn.execute("UPDATE empresas SET cursos_controlados = ?, exigir_cursos = ? WHERE id = ?",
                             (cursos, 1 if request.form.get("exigir") else 0, eid))
                conn.commit()
                flash("Opciones guardadas. Se aplican en la próxima importación o sincronización.", "ok")
                return redirect(url_for("hoja_google"))
            if not sheets.configurado():
                flash("La sincronización con Google Sheets aún no está activada. Contacte al administrador.", "error")
                return redirect(url_for("hoja_google"))
            if accion == "guardar":
                url = request.form.get("url", "").strip()
                fechas = "vencimiento" if request.form.get("fechas") == "vencimiento" else "realizacion"
                if not sheets.extraer_id(url):
                    flash("Eso no parece un enlace de Google Sheets (debe empezar por https://docs.google.com/spreadsheets/).", "error")
                    return redirect(url_for("hoja_google"))
                conn.execute("UPDATE empresas SET sheet_url = ?, sheet_fechas = ? WHERE id = ?", (url, fechas, eid))
                conn.commit()
            elif accion == "sincronizar":
                if not actual["sheet_url"]:
                    flash("Primero pegue el enlace de su hoja.", "error")
                    return redirect(url_for("hoja_google"))
                if actual["sheet_sync_en"]:
                    try:
                        hace = (datetime.now() - datetime.fromisoformat(actual["sheet_sync_en"])).total_seconds()
                    except ValueError:
                        hace = sheets.ESPERA_MANUAL_SEG
                    if hace < sheets.ESPERA_MANUAL_SEG:
                        flash("Acaba de sincronizar. Espere un minuto antes de volver a intentar.", "error")
                        return redirect(url_for("hoja_google"))
            else:
                abort(400)
            res = sheets.sincronizar_empresa(app.config["DB_PATH"], eid)
            flash(res["estado"], "ok" if res["estado"].startswith("ok") else "error")
            return redirect(url_for("hoja_google"))

        return render_template("sheets.html", e=estado(), activo=sheets.configurado(),
                               cuenta=sheets.email_cuenta_servicio())

    @app.route("/responsables", methods=["GET", "POST"])
    @login_requerido
    def responsables():
        eid, conn = g.usuario["empresa_id"], db()
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            if not RE_EMAIL.match(email):
                flash("Correo no válido.", "error")
            else:
                conn.execute("""INSERT INTO responsables (empresa_id, nombre, email) VALUES (?, ?, ?)
                                ON CONFLICT(empresa_id, email) DO UPDATE SET activo = 1""",
                             (eid, request.form.get("nombre", "").strip(), email))
                conn.commit()
                flash("Responsable agregado.", "ok")
            return redirect(url_for("responsables"))
        filas = conn.execute("SELECT id, nombre, email FROM responsables WHERE empresa_id = ? AND activo = 1 ORDER BY id",
                             (eid,)).fetchall()
        return render_template("responsables.html", filas=filas)

    @app.post("/responsables/<int:rid>/quitar")
    @login_requerido
    def quitar_responsable(rid):
        conn = db()
        conn.execute("UPDATE responsables SET activo = 0 WHERE id = ? AND empresa_id = ?",
                     (rid, g.usuario["empresa_id"]))
        conn.commit()
        return redirect(url_for("responsables"))

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def _error(e):
        mensajes = {413: f"El archivo supera {config.MAX_UPLOAD_MB} MB."}
        return render_template("error.html", mensaje=mensajes.get(e.code) or getattr(e, "description", "")), e.code

    # ------------------------------------------------------------ alertas programadas dentro de la app
    if app.config["RUN_SCHEDULER"] and not app.config.get("TESTING"):
        from apscheduler.schedulers.background import BackgroundScheduler
        from scheduler import registrar_jobs
        sch = BackgroundScheduler(timezone=config.ZONA_HORARIA)
        registrar_jobs(sch)
        sch.start()
        log.info("Alertas programadas activas dentro de la app web.")

    return app


if __name__ == "__main__":
    create_app().run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 8000)))
