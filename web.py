"""
Aplicacion web multiempresa: registro, verificacion de correo, login, panel,
importacion de Excel y responsables de alertas.

Regla de oro: TODA consulta lleva el empresa_id del usuario en sesion. Un usuario
nunca recibe datos de otra empresa (ver tests/test_web.py::test_aislamiento).

Uso local:   python web.py            -> http://localhost:8000
Produccion:  gunicorn 'web:create_app()' --workers 1 --threads 4
             (un solo worker: SQLite, el limite de intentos de login y el scheduler viven en el proceso)
"""
import hashlib
import logging
import mimetypes
import os
import re
import secrets
import sys
import tempfile
import time
from datetime import datetime, timedelta
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
from core import clasificar, conectar, dias_restantes, hoy, inicializar_db, normalizar   # noqa: E402
from edicion import validar_documento                          # noqa: E402

log = logging.getLogger(__name__)

SECTORES = {"salud": "Salud", "alimentos": "Alimentos", "construccion": "Construcción e ingeniería",
            "general": "Otro sector"}
RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10
INTENTOS_MAX, VENTANA_SEG = 5, 15 * 60
RESTABLECER_SEG = 3600          # vigencia del enlace para restablecer la contrasena


mimetypes.add_type("application/manifest+json", ".webmanifest")   # icono al "agregar a la pantalla de inicio"


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder=str(BASE_DIR / "templates" / "web"))
    if config.TRUST_PROXY:
        # Detras del proxy de Railway: la IP real del cliente viene en X-Forwarded-For (limite de intentos de login)
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.update(
        SECRET_KEY=config.FLASK_SECRET_KEY,
        DB_PATH=config.DB_PATH,
        APP_URL=config.APP_URL,
        RUN_SCHEDULER=config.RUN_SCHEDULER,
        SHEETS_ENABLED=config.SHEETS_ENABLED,
        SUPERADMIN_EMAILS=config.SUPERADMIN_EMAILS,
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
        if request.endpoint == "static":
            return
        uid = session.get("uid")
        if uid:
            g.usuario = db().execute("""
                SELECT u.id, u.email, u.rol, u.empresa_id, u.password_hash, e.nombre AS empresa, e.sector
                FROM usuarios u JOIN empresas e ON e.id = u.empresa_id
                WHERE u.id = ? AND u.verificado = 1 AND e.activa = 1""", (uid,)).fetchone()
            # Si la clave cambio despues de abrir esta sesion (p. ej. al restablecerla), la sesion deja de valer
            if g.usuario is None or session.get("ph") != _huella_clave(g.usuario["password_hash"]):
                g.usuario = None
                session.clear()
        if request.method == "POST":
            enviado = request.form.get("csrf", "")
            if not enviado or not secrets.compare_digest(enviado, session.get("csrf", "")):
                abort(400, "Sesión expirada o formulario inválido. Recargue la página.")

    @app.context_processor
    def _plantillas():
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(16)
        return {"csrf": session["csrf"], "usuario": g.get("usuario"), "sheets_habilitado": app.config["SHEETS_ENABLED"],
                "app_url": config.APP_URL, "es_superadmin": _es_superadmin()}

    def _huella_clave(password_hash: str) -> str:
        """Identifica la clave vigente sin exponerla: cambia cuando la clave cambia."""
        return hashlib.sha256(password_hash.encode()).hexdigest()[:16]

    def _es_superadmin() -> bool:
        u = g.get("usuario")
        return bool(u) and u["email"].lower() in app.config["SUPERADMIN_EMAILS"]

    huellas: dict[str, str] = {}

    @app.url_defaults
    def _version_estaticos(endpoint, values):
        """Cada url_for('static', ...) lleva ?v=<huella del contenido>: el navegador guarda el archivo un dia, pero si
        cambia en un despliegue la URL cambia y lo vuelve a descargar (sin quedarse con estilos viejos)."""
        if endpoint != "static" or "v" in values or "filename" not in values:
            return
        nombre = values["filename"]
        if nombre not in huellas:
            ruta = Path(app.static_folder) / nombre
            huellas[nombre] = hashlib.md5(ruta.read_bytes()).hexdigest()[:10] if ruta.is_file() else ""
        if huellas[nombre]:
            values["v"] = huellas[nombre]

    @app.after_request
    def _cabeceras(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        # Paginas con datos: nunca en cache. Logo, iconos y CSS: un dia (no tienen datos de nadie).
        resp.headers["Cache-Control"] = "public, max-age=86400" if request.endpoint == "static" else "no-store"
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
        html = (f"<p>Bienvenido a Vencimientos.</p><p>Confirme su correo para activar la cuenta "
                f"(el enlace vence en 48 horas):</p><p><a href='{enlace}'>{enlace}</a></p>")
        enviar_email("Confirme su cuenta en Vencimientos", html, [email])

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
        if request.method != "POST":       # GET y HEAD muestran el formulario
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
        if request.method != "POST":
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
        session["ph"] = _huella_clave(u["password_hash"])
        return redirect(url_for("panel"))

    # ------------------------------------------------------------ recuperar contrasena
    def _enlace_restablecer(usuario) -> str:
        """El token lleva una huella del hash ACTUAL de la clave: en cuanto se cambia, el enlace deja de servir
        (uso unico) sin tener que guardar tokens en la base."""
        huella = _huella_clave(usuario["password_hash"])
        token = serializer.dumps([usuario["id"], huella], salt="restablecer-clave")
        return f"{app.config['APP_URL']}/restablecer/{token}"

    def _usuario_de_token(token):
        """Usuario al que corresponde un token vigente y no usado, o None."""
        try:
            uid, huella = serializer.loads(token, salt="restablecer-clave", max_age=RESTABLECER_SEG)
        except (BadSignature, ValueError, TypeError):          # incluye SignatureExpired
            return None
        u = db().execute("SELECT id, email, password_hash FROM usuarios WHERE id = ?", (uid,)).fetchone()
        if u is None or _huella_clave(u["password_hash"]) != huella:
            return None
        return u

    @app.route("/olvide", methods=["GET", "POST"])
    def olvide():
        if request.method != "POST":
            return render_template("olvide.html")
        email = request.form.get("email", "").strip().lower()
        clave_rl = f"olvide|{request.remote_addr}"
        mensaje = "Si ese correo tiene una cuenta, le enviamos un enlace para crear una contraseña nueva (vence en 1 hora)."
        if bloqueado(clave_rl):
            flash("Demasiadas solicitudes. Espere unos minutos.", "error")
            return render_template("olvide.html"), 429
        app.config["INTENTOS"].setdefault(clave_rl, []).append(time.time())      # cuenta cada solicitud, exista o no
        u = db().execute("SELECT id, email, password_hash FROM usuarios WHERE email = ?", (email,)).fetchone() \
            if RE_EMAIL.match(email) else None
        if u is not None:
            enlace = _enlace_restablecer(u)
            app.config["ULTIMO_ENLACE_RESTABLECER"] = enlace if app.config.get("TESTING") else None
            if not correo.proveedor() or app.config.get("TESTING"):
                log.warning("Correo no configurado: enlace para restablecer la clave de %s -> %s", email, enlace)
            else:
                from scripts.generar_alertas import enviar_email
                enviar_email("Restablecer su contraseña en Vencimientos",
                             f"<p>Pidieron restablecer la contraseña de esta cuenta. Si fue usted, cree una nueva "
                             f"(el enlace vence en 1 hora y sirve una sola vez):</p><p><a href='{enlace}'>{enlace}</a></p>"
                             f"<p>Si no fue usted, ignore este mensaje: su contraseña no cambia.</p>", [email])
        flash(mensaje, "ok")                       # misma respuesta exista o no la cuenta
        return redirect(url_for("login"))

    @app.route("/restablecer/<token>", methods=["GET", "POST"])
    def restablecer(token):
        u = _usuario_de_token(token)
        if u is None:
            flash("El enlace no es válido o ya venció. Pida uno nuevo.", "error")
            return redirect(url_for("olvide"))
        if request.method != "POST":
            return render_template("restablecer.html", token=token, minimo=MIN_PASSWORD)
        clave = request.form.get("password", "")
        if len(clave) < MIN_PASSWORD:
            flash(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres.", "error")
            return render_template("restablecer.html", token=token, minimo=MIN_PASSWORD), 400
        if clave != request.form.get("password2", ""):
            flash("Las contraseñas no coinciden.", "error")
            return render_template("restablecer.html", token=token, minimo=MIN_PASSWORD), 400
        conn = db()
        # Poder leer el correo prueba que la direccion es suya: tambien queda verificada.
        conn.execute("UPDATE usuarios SET password_hash = ?, verificado = 1 WHERE id = ?",
                     (generate_password_hash(clave), u["id"]))
        conn.commit()
        session.clear()                            # cualquier sesion abierta con la clave vieja se cierra
        log.info("Contraseña restablecida: usuario %s", u["id"])
        flash("Contraseña actualizada. Ya puede ingresar.", "ok")
        return redirect(url_for("login"))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------ panel
    def _vencimientos_filtrados():
        """Vencimientos de la empresa segun los filtros de la URL: ventana (vencidos | N dias), curso y ubicacion."""
        from scripts.generar_alertas import consultar_faltantes, consultar_vencimientos, configuracion_empresa
        eid, conn = g.usuario["empresa_id"], db()
        maximo = max(configuracion_empresa(conn, eid)["ventanas"].values())
        todos = consultar_vencimientos(conn, eid, max(maximo, 30), incluir_indefinidos=True)
        faltantes = consultar_faltantes(conn, eid)
        cursos = sorted({r["tipo"] for r in todos} | {c for x in faltantes for c in x["lista"]}, key=str.lower)
        ubicaciones = sorted({r["area"] for r in todos if r["area"]} | {x["area"] for x in faltantes if x["area"]},
                             key=str.lower)
        curso = request.args.get("curso", "")
        ubicacion = request.args.get("ubicacion", "")
        curso = curso if curso in cursos else ""
        ubicacion = ubicacion if ubicacion in ubicaciones else ""
        ventana = request.args.get("ventana", str(maximo))
        if ventana not in ("vencidos", "indefinidos"):
            ventana = str(min(max(int(ventana), 0), 365)) if ventana.isdigit() else str(maximo)
        base = [r for r in todos if (not curso or r["tipo"] == curso) and (not ubicacion or r["area"] == ubicacion)]
        # Los de vigencia indefinida (pago unico) se ven, pero no cuentan como vencidos ni como por vencer:
        # su fecha impresa es administrativa y no hay nada que renovar.
        vence = [r for r in base if r["clase"] != "indefinido"]
        resumen = {"vencidos": sum(1 for r in vence if r["dias"] < 0),
                   **{f"d{n}": sum(1 for r in vence if 0 <= r["dias"] <= n) for n in (7, 15, 30)},
                   "todos": sum(1 for r in vence if r["dias"] <= maximo),
                   "indefinidos": len(base) - len(vence)}
        if ventana == "vencidos":
            registros, titulo = [r for r in vence if r["dias"] < 0], "Vencidos"
        elif ventana == "indefinidos":
            registros = [r for r in base if r["clase"] == "indefinido"]
            titulo = "Vigencia indefinida — no se renuevan"
        else:
            n = int(ventana)
            if n > max(maximo, 30):
                base = [r for r in consultar_vencimientos(conn, eid, n, incluir_indefinidos=True)
                        if (not curso or r["tipo"] == curso) and (not ubicacion or r["area"] == ubicacion)]
            registros = [r for r in base if r["dias"] <= n]
            ind = sum(1 for r in registros if r["clase"] == "indefinido")
            v = sum(1 for r in registros if r["clase"] != "indefinido" and r["dias"] < 0)
            titulo = f"Vencidos ({v}) y por vencer en {n} días ({len(registros) - v - ind})"
            if ind:
                titulo += f" · {ind} de vigencia indefinida"
        faltantes = [x for x in faltantes if (not curso or curso in x["lista"]) and (not ubicacion or x["area"] == ubicacion)]
        # Los exigidos sin registrar no tienen fecha, asi que no entran en los KPI de vencimientos, pero SI viajan en
        # los avisos: tienen su propio contador para que el panel nunca marque ceros mientras salen correos.
        resumen["sin_registrar"] = sum(x["cantidad"] for x in faltantes)
        opciones = [("vencidos", "Solo vencidos")] + [(str(n), f"Vencidos + próximos {n} días")
                                                      for n in sorted({7, 15, 30, maximo})]
        if resumen["indefinidos"]:
            opciones.append(("indefinidos", "Solo vigencia indefinida"))
        if ventana.isdigit() and ventana not in dict(opciones):
            opciones.append((ventana, f"Vencidos + próximos {ventana} días"))
        filtros = {"ventana": ventana, "curso": curso, "ubicacion": ubicacion}
        return dict(registros=registros, titulo=titulo, resumen=resumen, faltantes=faltantes, cursos=cursos,
                    ubicaciones=ubicaciones, f=filtros, maximo=maximo, str_max=str(maximo), opciones_ventana=opciones,
                    total_empresa=len(todos))

    POR_PAGINA = 20

    def _paginar(items: list, clave: str) -> tuple[list, dict]:
        """Corta `items` en paginas de POR_PAGINA segun el parametro `clave` de la URL (valores raros -> pagina valida)."""
        total = len(items)
        paginas = max(1, -(-total // POR_PAGINA))
        valor = request.args.get(clave, "1")
        pagina = min(max(int(valor), 1), paginas) if valor.isdigit() else 1
        ini = (pagina - 1) * POR_PAGINA
        visibles = sorted({1, paginas, pagina - 1, pagina, pagina + 1} & set(range(1, paginas + 1)))
        numeros = []
        for n in visibles:                                  # 1 … 4 5 6 … 12
            if numeros and n - numeros[-1] > 1:
                numeros.append(None)
            numeros.append(n)
        return items[ini:ini + POR_PAGINA], {"pagina": pagina, "paginas": paginas, "total": total,
                                             "desde": ini + 1 if total else 0, "hasta": min(ini + POR_PAGINA, total),
                                             "numeros": numeros}

    @app.get("/panel")
    @login_requerido
    def panel():
        datos = _vencimientos_filtrados()
        registros, pag_v = _paginar(datos.pop("registros"), "pagina")
        faltantes, pag_f = _paginar(datos.pop("faltantes"), "pf")
        empleados = db().execute("SELECT COUNT(*) FROM empleados WHERE empresa_id = ? AND activo = 1",
                                 (g.usuario["empresa_id"],)).fetchone()[0]
        return render_template("panel.html", registros=registros, pag_v=pag_v, faltantes=faltantes, pag_f=pag_f,
                               # la lista de faltantes empieza plegada; se abre si el usuario esta paginandola
                               abrir_faltantes="pf" in request.args or (not registros and pag_f["total"] > 0),
                               pf_actual=pag_f["pagina"] if "pf" in request.args else None,
                               empleados=empleados, hoy=hoy().strftime("%d/%m/%Y"), **datos)

    @app.get("/panel.xlsx")
    @login_requerido
    def panel_excel():
        """La misma lista filtrada del panel, completa, en Excel (mas los faltantes en otra hoja)."""
        import io
        import openpyxl
        from flask import Response
        from openpyxl.styles import Font
        datos = _vencimientos_filtrados()
        def agregar(hoja, valores):
            """Los datos importados se escriben siempre como texto, nunca como formula (inyeccion en Excel)."""
            hoja.append(valores)
            for c in hoja[hoja.max_row]:
                if c.data_type == "f":
                    c.data_type = "s"

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Vencimientos"
        ws.append(["Titular", "Cédula", "Ubicación", "Ubicación / cargo", "Documento", "Vence", "Días", "Estado"])
        for r in datos["registros"]:
            agregar(ws, [r["titular"], r["identificacion"], r["area"], r["area_cargo"], r["documento"],
                         r["fecha_vencimiento"], r["dias"], r["estado_label"]])
        wf = wb.create_sheet("Sin registrar")
        wf.append(["Persona", "Cédula", "Ubicación / cargo", "Faltan", "Cantidad"])
        for x in datos["faltantes"]:
            agregar(wf, [x["titular"], x["identificacion"], x["area_cargo"], x["faltan"], x["cantidad"]])
        for hoja in (ws, wf):
            for c in hoja[1]:
                c.font = Font(bold=True)
            hoja.freeze_panes = "A2"
            hoja.auto_filter.ref = hoja.dimensions
            for col in hoja.columns:
                hoja.column_dimensions[col[0].column_letter].width = min(45, max(10, *(len(str(c.value or "")) for c in col)) + 2)
        buf = io.BytesIO()
        wb.save(buf)
        nombre = f"vencimientos_{hoy().isoformat()}.xlsx"
        return Response(buf.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={nombre}"})

    @app.route("/avisos", methods=["GET", "POST"])
    @login_requerido
    def avisos():
        from config import ALERTAS, ANTICIPACION_RANGO, DIAS_SEMANA
        from scripts.generar_alertas import configuracion_empresa
        eid, conn = g.usuario["empresa_id"], db()
        if request.method == "POST":
            try:
                anticipacion = int(request.form.get("anticipacion", ""))
            except ValueError:
                anticipacion = -1
            if not ANTICIPACION_RANGO[0] <= anticipacion <= ANTICIPACION_RANGO[1]:
                flash(f"La anticipación debe estar entre {ANTICIPACION_RANGO[0]} y {ANTICIPACION_RANGO[1]} días.", "error")
                return redirect(url_for("avisos"))
            activas = [t for t in ALERTAS if t in request.form.getlist("alertas")]
            dia = configuracion_empresa(conn, eid)["dia_semanal"]          # si el formulario no lo trae, se conserva
            if request.form.get("dia_semanal", "") != "":
                try:
                    dia = int(request.form["dia_semanal"])
                except ValueError:
                    dia = -1
                if not 0 <= dia < len(DIAS_SEMANA):
                    flash("Elija un día de lunes a viernes para el aviso semanal.", "error")
                    return redirect(url_for("avisos"))
            conn.execute("UPDATE empresas SET anticipacion_dias = ?, alertas_activas = ?, dia_semanal = ? WHERE id = ?",
                         (anticipacion, ",".join(activas), dia, eid))
            conn.commit()
            flash("Avisos guardados." if activas else "Avisos guardados. Ojo: no recibirá ningún correo.", "ok")
            return redirect(url_for("avisos"))
        import calendario
        conf = configuracion_empresa(conn, eid)
        proximo = calendario.proximo_envio_semanal(conf["dia_semanal"])
        return render_template("avisos.html", conf=conf, alertas=ALERTAS, rango=ANTICIPACION_RANGO, dias=DIAS_SEMANA,
                               proximo=f"{DIAS_SEMANA[proximo.weekday()]} {proximo.strftime('%d/%m/%Y')}")

    @app.route("/importar", methods=["GET", "POST"])
    @login_requerido
    def importar_vista():
        if request.method != "POST":
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
                        headers={"Content-Disposition": "attachment; filename=plantilla_vencimientos.xlsx"})

    @app.get("/alertas")
    @login_requerido
    def alertas():
        """Vista previa EXACTA del correo que recibirian los responsables + historial de envios."""
        from scripts.generar_alertas import (configuracion_empresa, consultar_faltantes, consultar_vencimientos,
                                             renderizar_html)
        tipo = request.args.get("tipo", "semanal")
        if tipo not in config.ALERTAS:
            tipo = "semanal"
        eid, conn = g.usuario["empresa_id"], db()
        cfg = config.ALERTAS[tipo]
        conf = configuracion_empresa(conn, eid)         # misma ventana y avisos activos que el envio real
        dias = conf["ventanas"][tipo]
        tipos = {k: {"dias": conf["ventanas"][k], "activa": k in conf["activas"]} for k in config.ALERTAS}
        registros = consultar_vencimientos(conn, eid, dias)
        faltantes = consultar_faltantes(conn, eid) if cfg.get("faltantes") else []
        html = renderizar_html(registros, tipo, g.usuario["empresa"], faltantes, dias)
        destinatarios = [r["email"] for r in conn.execute(
            "SELECT email FROM responsables WHERE empresa_id = ? AND activo = 1 ORDER BY id", (eid,))]
        historial = conn.execute("""SELECT fecha_envio, tipo_alerta, documentos_notificados, destinatario, estado, detalle
                                    FROM log_alertas WHERE empresa_id = ? ORDER BY id DESC LIMIT 15""", (eid,)).fetchall()
        return render_template("alertas.html", tipo=tipo, tipos=tipos, html=html, destinatarios=destinatarios,
                               n_registros=len(registros), n_faltantes=len(faltantes), historial=historial,
                               correo_activo=bool(correo.proveedor()))

    @app.post("/alertas/prueba")
    @login_requerido
    def alerta_prueba():
        """Envia la alerta SOLO al correo del usuario que la pide (nunca a los responsables)."""
        from scripts.generar_alertas import (configuracion_empresa, consultar_faltantes, consultar_vencimientos,
                                             renderizar_html)
        tipo = request.form.get("tipo", "semanal")
        if tipo not in config.ALERTAS:
            tipo = "semanal"
        ahora, ultimo = time.time(), app.config.setdefault("ULTIMA_PRUEBA", {})
        if ahora - ultimo.get(g.usuario["id"], 0) < 60:
            flash("Espere un minuto antes de pedir otra prueba.", "error")
            return redirect(url_for("alertas", tipo=tipo))
        ultimo[g.usuario["id"]] = ahora
        eid, conn, cfg = g.usuario["empresa_id"], db(), config.ALERTAS[tipo]
        dias = configuracion_empresa(conn, eid)["ventanas"][tipo]
        html = renderizar_html(consultar_vencimientos(conn, eid, dias), tipo, g.usuario["empresa"],
                               consultar_faltantes(conn, eid) if cfg.get("faltantes") else [], dias)
        if app.config.get("TESTING"):
            app.config["ULTIMO_CORREO_PRUEBA"] = (g.usuario["email"], html)
            ok = True
        else:
            ok = correo.enviar(f"[PRUEBA] {cfg['asunto'].format(dias=dias)} — {g.usuario['empresa']}", html,
                               [g.usuario["email"]], None)
        flash(f"Prueba enviada a {g.usuario['email']}. Revise también la carpeta de spam." if ok
              else "No se pudo enviar la prueba. Intente más tarde.", "ok" if ok else "error")
        return redirect(url_for("alertas", tipo=tipo))

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
            if accion != "opciones" and not app.config["SHEETS_ENABLED"]:
                abort(404)                      # conexion con Google Sheets apagada: solo quedan las opciones de cursos
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
                               habilitado=app.config["SHEETS_ENABLED"],
                               cuenta=sheets.email_cuenta_servicio())

    @app.post("/datos/vaciar")
    @login_requerido
    def vaciar_datos():
        """Borra empleados y documentos (con su historial) de la empresa del usuario, para volver a cargar desde cero.
        Conserva la cuenta, responsables, avisos, requisitos por cargo y cursos controlados."""
        if g.usuario["rol"] != "admin":
            abort(403)
        if normalizar(request.form.get("confirmacion", "")) != normalizar(g.usuario["empresa"]):
            flash("Para vaciar los datos escriba exactamente el nombre de su empresa.", "error")
            return redirect(url_for("importar_vista", _anchor="vaciar"))
        eid, conn = g.usuario["empresa_id"], db()
        n_doc = conn.execute("SELECT COUNT(*) FROM documentos WHERE empresa_id = ?", (eid,)).fetchone()[0]
        n_emp = conn.execute("SELECT COUNT(*) FROM empleados WHERE empresa_id = ?", (eid,)).fetchone()[0]
        conn.execute("DELETE FROM documentos_historial WHERE documento_id IN "
                     "(SELECT id FROM documentos WHERE empresa_id = ?)", (eid,))
        conn.execute("DELETE FROM documentos WHERE empresa_id = ?", (eid,))
        conn.execute("DELETE FROM empleados WHERE empresa_id = ?", (eid,))
        conn.commit()
        log.warning("Datos vaciados: empresa %s, usuario %s (%s documentos, %s empleados)", eid, g.usuario["id"], n_doc, n_emp)
        flash(f"Listo: se borraron {n_doc} documentos y {n_emp} empleados. Ya puede cargar su archivo desde cero.", "ok")
        return redirect(url_for("importar_vista"))

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

    # ------------------------------------------------------------ documentos: ver, agregar, editar, eliminar
    def _fecha_visible(iso):
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y") if iso else ""

    def _titular(d):
        if d["cedula"] is None:
            return "EMPRESA"
        return f"{d['apellido']}, {d['nombre']}" if d["apellido"] else d["nombre"]

    def _estado_documento(d) -> tuple[str, str]:
        """(texto, clase css) del documento tal como lo ve la lista."""
        if d["no_aplica"]:
            return "No aplica", "mut"
        if d["vigencia_indefinida"]:
            return "INDEFINIDO", "indefinido"
        if not d["fecha_vencimiento"]:
            return "Sin vencimiento", "mut"
        dias = dias_restantes(d["fecha_vencimiento"])
        clase, etiqueta = clasificar(dias)
        return f"{etiqueta} ({'hace ' + str(-dias) if dias < 0 else dias} d)", clase

    def _documento_de_la_empresa(did):
        """El documento SOLO si es de la empresa del usuario. Si no, 404: no se revela que existe en otra."""
        d = db().execute("""
            SELECT d.id, d.empleado_id, d.referencia, d.entidad_emisora, d.fecha_emision, d.fecha_vencimiento,
                   d.no_aplica, d.vigencia_indefinida, d.avisar_dias, d.observaciones,
                   t.nombre AS documento, e.cedula, e.nombre, e.apellido, e.cargo, e.area, e.activo
            FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
            LEFT JOIN empleados e ON e.id = d.empleado_id
            WHERE d.id = ? AND d.empresa_id = ?""", (did, g.usuario["empresa_id"])).fetchone()
        if d is None:
            abort(404)
        return d

    @app.get("/documentos")
    @login_requerido
    def documentos():
        q = request.args.get("q", "").strip()[:80]
        buscar = normalizar(q)
        filas = db().execute("""
            SELECT d.id, d.referencia, d.fecha_vencimiento, d.no_aplica, d.vigencia_indefinida,
                   t.nombre AS documento, e.cedula, e.nombre, e.apellido, e.cargo, e.area, e.activo
            FROM documentos d JOIN tipos_documento t ON t.id = d.tipo_id
            LEFT JOIN empleados e ON e.id = d.empleado_id
            WHERE d.empresa_id = ? ORDER BY e.apellido, e.nombre, t.nombre""", (g.usuario["empresa_id"],)).fetchall()
        items = []
        for d in filas:
            titular = _titular(d)
            if buscar and buscar not in normalizar(f"{titular} {d['cedula'] or ''} {d['documento']} {d['referencia']}"):
                continue
            estado, clase = _estado_documento(d)
            items.append({"id": d["id"], "titular": titular, "cedula": d["cedula"] or "—", "documento": d["documento"],
                          "referencia": d["referencia"], "vence": _fecha_visible(d["fecha_vencimiento"]) or "—",
                          "estado": estado, "clase": clase, "retirado": d["cedula"] is not None and not d["activo"]})
        visibles, pag = _paginar(items, "pagina")
        return render_template("documentos.html", items=visibles, pag=pag, q=q, total=len(filas))

    @app.route("/documentos/nuevo", methods=["GET", "POST"])
    @login_requerido
    def documento_nuevo():
        from agregar_documento import agregar
        eid, conn = g.usuario["empresa_id"], db()
        personas = conn.execute("""SELECT cedula, nombre, apellido, cargo, area FROM empleados
                                   WHERE empresa_id = ? AND activo = 1 ORDER BY apellido, nombre""", (eid,)).fetchall()
        sugeridos = [r["nombre"] for r in conn.execute(
            "SELECT DISTINCT nombre FROM tipos_documento WHERE sector IN (?, 'general') ORDER BY nombre",
            (g.usuario["sector"],))]

        def formulario(estado=200, **extra):
            return render_template("documento_form.html", nuevo=True, personas=personas, sugeridos=sugeridos,
                                   v=extra.get("v", {}), **{k: x for k, x in extra.items() if k != "v"}), estado

        if request.method != "POST":
            return formulario()
        v = {k: request.form.get(k, "").strip() for k in ("cedula", "documento", "referencia", "fecha_emision",
                                                           "fecha_vencimiento", "avisar_dias", "entidad", "observaciones")}
        v["vigencia_indefinida"] = bool(request.form.get("vigencia_indefinida"))
        valores, errores = validar_documento(request.form)
        if not v["documento"]:
            errores.append("Escriba el nombre del documento o curso.")
        persona = None
        if v["cedula"]:
            persona = next((p for p in personas if p["cedula"] == v["cedula"]), None)   # solo personas de ESTA empresa
            if persona is None:
                errores.append("Elija una persona de la lista.")
        if errores:
            for e in errores:
                flash(e, "error")
            return formulario(400, v=v)
        campos = {"empresa": g.usuario["empresa"], "documento": v["documento"][:200], "referencia": v["referencia"][:100],
                  "fecha_emision": v["fecha_emision"], "fecha_vencimiento": v["fecha_vencimiento"],
                  "avisar_dias": v["avisar_dias"], "entidad": valores["entidad"], "observaciones": valores["observaciones"],
                  "vigencia_indefinida": "indefinida" if valores["vigencia_indefinida"] else ""}
        if persona:
            campos.update(cedula=persona["cedula"], nombre=persona["nombre"], apellido=persona["apellido"],
                          cargo=persona["cargo"], area=persona["area"])
        res = agregar(campos, db_path=app.config["DB_PATH"], empresa_defecto=g.usuario["empresa"])
        if res["errores"]:
            for e in res["errores"]:
                flash(e, "error")
            return formulario(400, v=v)
        flash("Documento actualizado: ya existía con esa referencia; la versión anterior quedó en el historial."
              if res["actualizados"] else "Documento agregado.", "ok")
        return redirect(url_for("documentos", q=v["documento"]))

    @app.route("/documentos/<int:did>", methods=["GET", "POST"])
    @login_requerido
    def documento_editar(did):
        d = _documento_de_la_empresa(did)
        if request.method == "POST":
            valores, errores = validar_documento(request.form)
            if errores:
                for e in errores:
                    flash(e, "error")
                v = {**{k: request.form.get(k, "").strip() for k in ("fecha_emision", "fecha_vencimiento", "avisar_dias",
                                                                       "entidad", "observaciones")},
                     "no_aplica": bool(request.form.get("no_aplica")),
                     "vigencia_indefinida": bool(request.form.get("vigencia_indefinida"))}
                return render_template("documento_form.html", nuevo=False, d=d, titular=_titular(d), v=v, historial=[]), 400
            conn = db()
            # Si cambia el vencimiento, el disparador de la base guarda la version anterior en el historial
            conn.execute("""UPDATE documentos SET fecha_emision = ?, fecha_vencimiento = ?, no_aplica = ?,
                                vigencia_indefinida = ?, avisar_dias = ?, entidad_emisora = ?, observaciones = ?,
                                actualizado_en = ?
                            WHERE id = ? AND empresa_id = ?""",
                         (valores["fecha_emision"], valores["fecha_vencimiento"], valores["no_aplica"],
                          valores["vigencia_indefinida"], valores["avisar_dias"], valores["entidad"],
                          valores["observaciones"], hoy().isoformat(), did, g.usuario["empresa_id"]))
            conn.commit()
            flash("Documento actualizado.", "ok")
            return redirect(url_for("documentos", q=d["cedula"] or d["documento"]))
        v = {"fecha_emision": _fecha_visible(d["fecha_emision"]), "fecha_vencimiento": _fecha_visible(d["fecha_vencimiento"]),
             "avisar_dias": "" if d["avisar_dias"] is None else str(d["avisar_dias"]), "entidad": d["entidad_emisora"] or "",
             "observaciones": d["observaciones"] or "", "no_aplica": bool(d["no_aplica"]),
             "vigencia_indefinida": bool(d["vigencia_indefinida"])}
        historial = [{"emision": _fecha_visible(h["fecha_emision"]), "vence": _fecha_visible(h["fecha_vencimiento"]),
                      "cuando": (h["registrado_en"] or "")[:10]}
                     for h in db().execute("""SELECT fecha_emision, fecha_vencimiento, registrado_en
                                              FROM documentos_historial WHERE documento_id = ? ORDER BY id DESC""", (did,))]
        return render_template("documento_form.html", nuevo=False, d=d, titular=_titular(d), v=v, historial=historial)

    @app.post("/documentos/<int:did>/eliminar")
    @login_requerido
    def documento_eliminar(did):
        if g.usuario["rol"] != "admin":
            abort(403)
        d = _documento_de_la_empresa(did)
        conn = db()
        conn.execute("DELETE FROM documentos_historial WHERE documento_id = ?", (did,))
        conn.execute("DELETE FROM documentos WHERE id = ? AND empresa_id = ?", (did, g.usuario["empresa_id"]))
        conn.commit()
        log.warning("Documento eliminado: empresa %s, usuario %s, documento %s (%s)",
                    g.usuario["empresa_id"], g.usuario["id"], did, d["documento"])
        flash("Documento eliminado.", "ok")
        return redirect(url_for("documentos"))

    @app.post("/documentos/<int:did>/persona")
    @login_requerido
    def documento_persona(did):
        """Marca a la persona dueña del documento como retirada (deja de recibir avisos) o la reactiva."""
        d = _documento_de_la_empresa(did)
        if d["empleado_id"] is None:
            abort(400, "Este documento es de la empresa, no de una persona.")
        activo = 0 if request.form.get("accion") == "retirar" else 1
        conn = db()
        conn.execute("UPDATE empleados SET activo = ? WHERE id = ? AND empresa_id = ?",
                     (activo, d["empleado_id"], g.usuario["empresa_id"]))
        conn.commit()
        flash("Persona marcada como retirada: ya no genera avisos." if not activo else "Persona reactivada.", "ok")
        return redirect(url_for("documento_editar", did=did))

    # ------------------------------------------------------------ administrador del sistema (cruza empresas)
    @app.get("/admin")
    @login_requerido
    def admin():
        """Todas las empresas, quien recibe sus avisos y que se envio. SOLO cuentas de SUPERADMIN_EMAILS; para el resto
        responde 404 (no revela que existe). Muestra conteos, nunca cedulas ni datos de empleados."""
        if not _es_superadmin():
            abort(404)
        conn = db()

        def por_empresa(sql, params=()):
            return {r[0]: r[1] for r in conn.execute(sql, params)}

        usuarios = por_empresa("SELECT empresa_id, COUNT(*) FROM usuarios GROUP BY empresa_id")
        personas = por_empresa("SELECT empresa_id, COUNT(*) FROM empleados WHERE activo = 1 GROUP BY empresa_id")
        documentos_n = por_empresa("SELECT empresa_id, COUNT(*) FROM documentos GROUP BY empresa_id")
        vencidos = por_empresa("""SELECT d.empresa_id, COUNT(*) FROM documentos d LEFT JOIN empleados e ON e.id = d.empleado_id
                                  WHERE d.fecha_vencimiento IS NOT NULL AND d.fecha_vencimiento < ? AND d.vigencia_indefinida = 0
                                    AND d.no_aplica = 0 AND (d.empleado_id IS NULL OR e.activo = 1)
                                  GROUP BY d.empresa_id""", (hoy().isoformat(),))
        ultimo_envio = por_empresa("SELECT empresa_id, MAX(fecha_envio) FROM log_alertas WHERE estado = 'enviado' GROUP BY empresa_id")
        desde = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
        fallos = por_empresa("""SELECT empresa_id, COUNT(*) FROM log_alertas
                                WHERE estado IN ('error_envio', 'sin_destinatario') AND fecha_envio >= ? GROUP BY empresa_id""", (desde,))
        resp: dict[int, list[str]] = {}
        for r in conn.execute("SELECT empresa_id, email FROM responsables WHERE activo = 1 ORDER BY id"):
            resp.setdefault(r["empresa_id"], []).append(r["email"])

        empresas = []
        for e in conn.execute("""SELECT id, nombre, sector, activa, consentimiento_en, alertas_activas
                                 FROM empresas ORDER BY nombre"""):
            activas = [x for x in (e["alertas_activas"] or "").split(",") if x]
            empresas.append({
                "id": e["id"], "nombre": e["nombre"], "sector": e["sector"] or "—", "activa": bool(e["activa"]),
                "alta": (e["consentimiento_en"] or "")[:10] or "—", "usuarios": usuarios.get(e["id"], 0),
                "personas": personas.get(e["id"], 0), "documentos": documentos_n.get(e["id"], 0),
                "vencidos": vencidos.get(e["id"], 0), "responsables": resp.get(e["id"], []), "avisos": activas,
                "ultimo_envio": (ultimo_envio.get(e["id"]) or "")[:16].replace("T", " ") or "nunca",
                "fallos": fallos.get(e["id"], 0),
                # Una empresa que no recibira nada: sin responsables (ni respaldo) o sin avisos activos
                "silenciosa": e["activa"] and (not activas or not (resp.get(e["id"]) or config.RRHH_EMAIL))})

        nombres = {e["id"]: e["nombre"] for e in empresas}
        envios = [{"cuando": (r["fecha_envio"] or "")[:16].replace("T", " "), "empresa": nombres.get(r["empresa_id"], "—"),
                   "tipo": r["tipo_alerta"], "estado": r["estado"], "destinatario": r["destinatario"] or "—",
                   "detalle": r["detalle"] or ""}
                  for r in conn.execute("""SELECT fecha_envio, empresa_id, tipo_alerta, estado, destinatario, detalle
                                           FROM log_alertas ORDER BY id DESC LIMIT 40""")]
        por_estado = por_empresa("SELECT estado, COUNT(*) FROM log_alertas WHERE fecha_envio >= ? GROUP BY estado", (desde,))
        sch = app.extensions.get("scheduler")
        trabajos = sorted(((j.name, j.next_run_time) for j in sch.get_jobs() if j.next_run_time), key=lambda x: x[1]) if sch else []
        return render_template("admin.html", empresas=empresas, envios=envios, por_estado=por_estado,
                               activas=sum(1 for e in empresas if e["activa"]), silenciosas=[e["nombre"] for e in empresas if e["silenciosa"]],
                               scheduler=bool(sch), trabajos=trabajos, proveedor=correo.proveedor() or "ninguno",
                               rrhh=config.RRHH_EMAIL, admin_email=config.ADMIN_EMAIL, zona=config.ZONA_HORARIA)

    # ------------------------------------------------------------ baja de avisos (sin iniciar sesion)
    def _responsable_de_token(token):
        """Fila del responsable que firma el token, o None. El enlace NO vence: una baja debe seguir
        funcionando meses despues de que salio el correo."""
        try:
            rid = serializer.loads(token, salt="baja-avisos")
        except BadSignature:                      # incluye SignatureExpired
            return None
        return db().execute("""SELECT r.id, r.email, r.activo, e.nombre AS empresa
                               FROM responsables r JOIN empresas e ON e.id = r.empresa_id
                               WHERE r.id = ?""", (rid,)).fetchone()

    @app.get("/baja/<token>")
    def baja(token):
        """Pagina de confirmacion. El GET NO da de baja: el prefetch de Gmail y los escaneres
        corporativos abren los enlaces solos y darian de baja a quien no lo pidio."""
        r = _responsable_de_token(token)
        if r is None:
            abort(404)
        return render_template("baja.html", r=r, hecho=not r["activo"])

    @app.post("/baja/<token>")
    def baja_confirmar(token):
        r = _responsable_de_token(token)
        if r is None:
            abort(404)
        conn = db()
        conn.execute("UPDATE responsables SET activo = 0 WHERE id = ?", (r["id"],))
        conn.commit()
        log.info("Baja de avisos: responsable %s de la empresa %s", r["id"], r["empresa"])
        return render_template("baja.html", r=r, hecho=True)

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
        app.extensions["scheduler"] = sch
        log.info("Alertas programadas activas dentro de la app web.")

    return app


if __name__ == "__main__":
    create_app().run(debug=os.getenv("FLASK_DEBUG") == "1", port=int(os.getenv("PORT", 8000)))
