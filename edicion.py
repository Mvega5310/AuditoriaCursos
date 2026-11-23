"""
Validacion de los datos de un documento que se edita o agrega a mano desde la web.

Las mismas reglas que el importador de Excel (fechas dd/mm/aaaa o aaaa-mm-dd, vencimiento no anterior a la emision,
rechazo de vencimientos de hace mas de N anos por error de digitacion), pero devolviendo mensajes para una persona
que esta mirando un formulario, no filas de un archivo.
"""
from datetime import timedelta

from config import VENCIMIENTO_MAX_ANTIGUEDAD_ANIOS
from core import hoy, parsear_fecha

AVISAR_MAX = 365


def validar_documento(form) -> tuple[dict, list[str]]:
    """(valores, errores). `valores` queda listo para guardar; si hay errores no debe guardarse."""
    errores: list[str] = []

    def texto(clave: str, largo: int) -> str:
        return (form.get(clave) or "").strip()[:largo]

    no_aplica = 1 if form.get("no_aplica") else 0
    # 'No aplica' y 'vigencia indefinida' no conviven: si no aplica, no hay nada que vencer
    indefinida = 1 if form.get("vigencia_indefinida") and not no_aplica else 0
    emision_txt, venc_txt = texto("fecha_emision", 20), texto("fecha_vencimiento", 20)
    emision = vencimiento = None

    if not no_aplica:
        emision, vencimiento = parsear_fecha(emision_txt), parsear_fecha(venc_txt)
        if emision_txt and not emision:
            errores.append(f"La fecha de emisión «{emision_txt}» no es válida. Use dd/mm/aaaa.")
        if venc_txt and not vencimiento:
            errores.append(f"La fecha de vencimiento «{venc_txt}» no es válida. Use dd/mm/aaaa.")
        if emision and vencimiento and vencimiento < emision:
            errores.append("El vencimiento no puede ser anterior a la emisión.")
        limite = hoy() - timedelta(days=365 * VENCIMIENTO_MAX_ANTIGUEDAD_ANIOS)
        if vencimiento and not indefinida and vencimiento < limite.isoformat():
            errores.append(f"El vencimiento es de hace más de {VENCIMIENTO_MAX_ANTIGUEDAD_ANIOS} años: ¿error de "
                           f"digitación? Si el curso no se renueva, marque «Vigencia indefinida».")

    avisar = None
    avisar_txt = texto("avisar_dias", 5)
    if avisar_txt:
        if avisar_txt.isdigit() and int(avisar_txt) <= AVISAR_MAX:
            avisar = int(avisar_txt)
        else:
            errores.append(f"«Avisar desde» debe ser un número de días entre 0 y {AVISAR_MAX}.")

    valores = {"fecha_emision": emision, "fecha_vencimiento": vencimiento, "no_aplica": no_aplica,
               "vigencia_indefinida": indefinida, "avisar_dias": avisar,
               "entidad": texto("entidad", 200), "observaciones": texto("observaciones", 1000)}
    return valores, errores
