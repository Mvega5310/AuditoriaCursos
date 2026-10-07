"""
Catalogo inicial de tipos de documento, organizado por SECTOR.

Es un PUNTO DE PARTIDA editable: se carga con INSERT OR IGNORE y luego puedes ajustar
vigencias directamente en la tabla tipos_documento, o agregar tipos propios con
    python scripts/agregar_documento.py documento ...   (ver README)

periodicidad_dias = None  -> la vigencia no es fija: se debe registrar la fecha de
                              vencimiento que figura en el certificado/soporte.

IMPORTANTE: las vigencias legales cambian y algunas fuentes difieren. La columna 'nota'
indica donde hay que verificar contra el texto oficial o la entidad emisora. Solo se fija
una periodicidad cuando la fuente es clara; en los demas casos se usa la fecha del soporte.

Tupla por tipo: (categoria, nombre, aplica_a, periodicidad_dias, norma_referencia, nota)
Sectores: general | alimentos | salud | construccion
"""

_SIN_DIAGNOSTICOS = (
    "Registrar solo fecha y vigencia; no guardar diagnosticos ni resultados "
    "clinicos (datos sensibles, Ley 1581/2012)."
)
_VERIF_MENSUAL = "La fecha de vencimiento es la proxima verificacion (planilla PILA / ADRES)."
_ISO = "Tipicamente 3 anos con auditorias de seguimiento anuales. Confirmar con la certificadora."
_VERIFICAR = "Verificar vigencia en la norma o con la entidad emisora."
_POR_PLACA = "Un registro por vehiculo: usar la placa como referencia."

# ------------------------------------------------------------------ general (aplica a cualquier empresa)
GENERAL = [
    # Empleados: cursos / licencias / certificaciones
    ("curso", "Trabajo en alturas", "empleado", None, "Res. 4272/2021",
     "Usar la fecha de vigencia del certificado. Las fuentes consultadas difieren "
     "(3 anos / reentrenamiento a 18 meses): verificar en el texto oficial y con el centro de entrenamiento."),
    ("curso", "Curso SG-SST 50 horas", "empleado", None, "Res. 0312/2019", ""),
    ("curso", "Primeros auxilios", "empleado", None, "", ""),
    ("curso", "Brigada de emergencias", "empleado", None, "", ""),
    ("curso", "Manejo defensivo", "empleado", None, "", ""),
    ("licencia", "Licencia en Seguridad y Salud en el Trabajo", "empleado", None, "",
     "Usar la fecha de vencimiento de la licencia."),
    ("licencia", "Licencia de conduccion", "empleado", None, "", ""),
    ("certificacion", "Certificacion de competencia laboral", "empleado", None, "", ""),
    # Empleados: examenes medicos
    ("examen_medico", "Examen medico ocupacional de ingreso", "empleado", None, "Res. 2346/2007", _SIN_DIAGNOSTICOS),
    ("examen_medico", "Examen medico ocupacional periodico", "empleado", None, "Res. 2346/2007",
     "La periodicidad la define el medico/profesiograma de cada cargo. " + _SIN_DIAGNOSTICOS),
    ("examen_medico", "Examen medico con enfasis en alturas", "empleado", None, "Res. 4272/2021",
     "Verificar periodicidad vigente. " + _SIN_DIAGNOSTICOS),
    # Empleados: afiliaciones
    ("afiliacion", "Afiliacion EPS", "empleado", 30, "", _VERIF_MENSUAL),
    ("afiliacion", "Afiliacion ARL", "empleado", 30, "", _VERIF_MENSUAL),
    ("afiliacion", "Afiliacion fondo de pensiones", "empleado", 30, "", _VERIF_MENSUAL),
    ("afiliacion", "Afiliacion caja de compensacion", "empleado", 30, "", _VERIF_MENSUAL),
    # Empresa: certificaciones
    ("certificacion", "ISO 9001 (calidad)", "empresa", 1095, "", _ISO),
    ("certificacion", "ISO 45001 (seguridad y salud)", "empresa", 1095, "", _ISO),
    ("certificacion", "ISO 14001 (ambiental)", "empresa", 1095, "", _ISO),
    # Empresa: SST y administrativos
    ("documento_empresa", "Autoevaluacion estandares minimos SG-SST", "empresa", 365, "Res. 0312/2019",
     "Anual, con corte a 31 de diciembre. El reporte lo fija cada ano una circular del Ministerio del Trabajo."),
    ("documento_empresa", "Plan anual de trabajo SG-SST", "empresa", 365, "Decreto 1072/2015", "Verificar."),
    ("documento_empresa", "Politica de SST (revision)", "empresa", 365, "Decreto 1072/2015", "Verificar."),
    ("documento_empresa", "Matriz de identificacion de peligros", "empresa", 365, "Decreto 1072/2015", "Verificar."),
    ("documento_empresa", "Plan de emergencias", "empresa", None, "", ""),
    ("documento_empresa", "Reglamento interno de trabajo", "empresa", None, "", ""),
    ("documento_empresa", "Acta de conformacion COPASST / Vigia SST", "empresa", None, "", ""),
    ("documento_empresa", "Certificado de Camara de Comercio", "empresa", 365, "", ""),
    ("documento_empresa", "Recarga de extintores", "empresa", 365, "",
     "Tipicamente anual; confirmar con el proveedor y la etiqueta del extintor."),
    ("documento_empresa", "Poliza de responsabilidad civil", "empresa", None, "", "Usar la fecha de la poliza."),
    ("documento_empresa", "SOAT", "empresa", 365, "", _POR_PLACA),
    ("documento_empresa", "Revision tecnico-mecanica y de emisiones", "empresa", None, "",
     _POR_PLACA + " La vigencia depende del tipo de vehiculo: usar la fecha del certificado."),
]

# ------------------------------------------------------------------ alimentos
ALIMENTOS = [
    ("curso", "Manipulacion de alimentos", "empleado", None, "Res. 2674/2013", ""),
    ("examen_medico", "Reconocimiento medico de manipulador de alimentos", "empleado", None, "Res. 2674/2013 art. 11",
     "Verificar periodicidad en el articulo 11. " + _SIN_DIAGNOSTICOS),
    ("documento_empresa", "Registro sanitario INVIMA (alimento riesgo alto)", "empresa", 1825, "Res. 2674/2013 art. 39", ""),
    ("documento_empresa", "Permiso sanitario INVIMA (alimento riesgo medio)", "empresa", 2555, "Res. 2674/2013 art. 39", ""),
    ("documento_empresa", "Notificacion sanitaria INVIMA (alimento riesgo bajo)", "empresa", 3650, "Res. 2674/2013 art. 41", ""),
    ("documento_empresa", "Concepto sanitario del establecimiento", "empresa", None, "Res. 2674/2013",
     "Lo emite la autoridad sanitaria tras la inspeccion."),
    ("documento_empresa", "Plan de saneamiento basico", "empresa", None, "Res. 2674/2013 art. 26",
     "Incluye limpieza y desinfeccion, residuos, control de plagas y agua potable."),
    ("documento_empresa", "Certificado de control de plagas (fumigacion)", "empresa", None, "",
     "Frecuencia segun el programa de saneamiento: " + _VERIFICAR),
    ("documento_empresa", "Certificado de lavado y desinfeccion de tanques de agua", "empresa", None, "",
     "Frecuencia segun el programa de saneamiento: " + _VERIFICAR),
    ("documento_empresa", "Analisis fisicoquimico y microbiologico del agua", "empresa", None, "", _VERIFICAR),
    ("documento_empresa", "Calibracion de equipos de medicion (balanzas, termometros)", "empresa", None, "",
     "Usar la fecha del certificado de calibracion."),
    ("certificacion", "Certificacion BPM / HACCP", "empresa", None, "", _VERIFICAR),
    ("certificacion", "ISO 22000 / FSSC 22000 (inocuidad)", "empresa", 1095, "", _ISO),
]

# ------------------------------------------------------------------ salud
SALUD = [
    ("curso", "Bioseguridad", "empleado", None, "", ""),
    ("curso", "RCP (reanimacion cardiopulmonar)", "empleado", None, "", ""),
    ("licencia", "Registro profesional en salud (RETHUS)", "empleado", None, "", "Usar la fecha de vencimiento si aplica."),
    ("examen_medico", "Vacunacion del personal (hepatitis B y otras)", "empleado", None, "",
     "Registrar solo la fecha de la dosis/refuerzo. " + _SIN_DIAGNOSTICOS),
    ("documento_empresa", "Inscripcion REPS / habilitacion de servicios", "empresa", None, "Res. 3100/2019",
     "Inscripcion inicial: 4 anos; renovaciones anuales previa autoevaluacion. Registrar la fecha real de vencimiento."),
    ("documento_empresa", "Autoevaluacion anual de condiciones de habilitacion (REPS)", "empresa", 365, "Res. 3100/2019",
     "Se declara en el REPS antes de cada vencimiento anual."),
    ("documento_empresa", "Plan de gestion integral de residuos en salud (PGIRASA)", "empresa", None, "Decreto 351/2014",
     _VERIFICAR),
    ("documento_empresa", "Certificado de disposicion de residuos (gestor autorizado)", "empresa", None, "",
     "Usar la fecha de vencimiento del contrato/certificado del gestor."),
    ("documento_empresa", "Licencia de rayos X / proteccion radiologica", "empresa", None, "Res. 482/2018",
     _VERIFICAR),
    ("documento_empresa", "Calibracion y mantenimiento de equipos biomedicos", "empresa", None, "",
     "Un registro por equipo: usar el codigo del equipo como referencia."),
    ("documento_empresa", "Poliza de responsabilidad civil profesional", "empresa", None, "", "Usar la fecha de la poliza."),
]

# ------------------------------------------------------------------ construccion / ingenieria / metalmecanica
CONSTRUCCION = [
    ("curso", "Espacios confinados", "empleado", None, "", ""),
    ("curso", "Riesgo electrico", "empleado", None, "", ""),
    ("curso", "Izaje de cargas", "empleado", None, "", ""),
    ("licencia", "Matricula profesional (COPNIA / CONTE)", "empleado", None, "", ""),
    ("certificacion", "Certificacion de soldador", "empleado", None, "",
     "Usar la fecha de vencimiento de la calificacion."),
    ("certificacion", "Certificado RUC (evaluacion de contratistas)", "empresa", None, "",
     "Lo emite el Consejo Colombiano de Seguridad: " + _VERIFICAR),
    ("documento_empresa", "Certificacion de equipos de izaje (gruas, tecles, montacargas)", "empresa", None, "",
     "Un registro por equipo: usar el codigo del equipo como referencia. " + _VERIFICAR),
    ("documento_empresa", "Inspeccion de eslingas, arneses y lineas de vida", "empresa", None, "",
     "Frecuencia segun el programa de inspecciones y las instrucciones del fabricante."),
    ("documento_empresa", "Certificacion de andamios", "empresa", None, "", _VERIFICAR),
    ("documento_empresa", "Calibracion de equipos (torquimetros, manometros)", "empresa", None, "",
     "Usar la fecha del certificado de calibracion."),
    ("documento_empresa", "Poliza de cumplimiento", "empresa", None, "", "Usar la fecha de la poliza."),
    ("documento_empresa", "Licencia de construccion", "empresa", None, "", "Usar la fecha de la licencia."),
]

SECTORES = {"general": GENERAL, "alimentos": ALIMENTOS, "salud": SALUD, "construccion": CONSTRUCCION}

# Lista plana lista para insertar: (sector, categoria, nombre, aplica_a, periodicidad, norma, nota)
CATALOGO = [(sector,) + tipo for sector, tipos in SECTORES.items() for tipo in tipos]
