"""
Calendario laboral de Colombia: dias habiles y fecha de envio del aviso semanal.

Reglas (todas usan dias habiles de Colombia, lunes a viernes sin festivos):
  - Diaria:    solo sale en dia habil (no se envia en festivos ni fines de semana).
  - Semanal:   cada empresa elige un dia (lunes a viernes). Si es festivo, sale el SIGUIENTE dia habil.
  - Quincenal: dias 1 y 15. Si caen en fin de semana o festivo, sale el siguiente dia habil.
  - Mensual:   dia 1. Misma regla.
Si hay varios dias no habiles seguidos (Semana Santa, puentes), sale el primer dia habil despues de ellos.
"""
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache

import pytz

from config import ZONA_HORARIA

log = logging.getLogger(__name__)

HORA_SEMANAL = (7, 30)          # coincide con el programador (scheduler.py)

try:
    import holidays as _holidays
except ImportError:             # sin la libreria solo se descartan fines de semana
    _holidays = None
    log.warning("Falta el paquete 'holidays': no se tendran en cuenta los festivos de Colombia.")


@lru_cache(maxsize=8)
def _festivos(anio: int):
    return _holidays.country_holidays("CO", years=anio) if _holidays else {}


def es_festivo(d: date) -> bool:
    return d in _festivos(d.year)


def es_habil(d: date) -> bool:
    """Lunes a viernes que no es festivo en Colombia."""
    return d.weekday() < 5 and not es_festivo(d)


def _toca(hoy: date, coincide) -> bool:
    """Regla comun: hoy es habil y `coincide(d)` se cumple hoy o en algun dia no habil JUSTO antes de hoy
    (el envio programado se corrio al siguiente dia habil)."""
    if not es_habil(hoy):
        return False
    d = hoy
    while True:
        if coincide(d):
            return True
        d -= timedelta(days=1)
        if es_habil(d):
            return False


def toca_dia_del_mes(hoy: date, dias_del_mes: tuple[int, ...]) -> bool:
    """True si hoy sale el aviso programado para esos dias del mes (p. ej. (1, 15)), corrido al siguiente habil."""
    return _toca(hoy, lambda d: d.day in dias_del_mes)


def toca_hoy(tipo: str, hoy: date, dia_semanal: int = 0) -> bool:
    """Decide si hoy corresponde enviar la alerta `tipo` (diaria | semanal | quincenal | mensual)."""
    if tipo == "diaria":
        return es_habil(hoy)
    if tipo == "semanal":
        return toca_semanal(hoy, dia_semanal)
    if tipo == "quincenal":
        return toca_dia_del_mes(hoy, (1, 15))
    if tipo == "mensual":
        return toca_dia_del_mes(hoy, (1,))
    raise ValueError(f"tipo de alerta desconocido: {tipo}")


def toca_semanal(hoy: date, dia_semanal: int) -> bool:
    """True si HOY se debe enviar el aviso semanal de una empresa que eligio `dia_semanal` (0 = lunes ... 4 = viernes).

    - Hoy debe ser un dia habil.
    - Si hoy es el dia elegido, se envia.
    - Si el dia elegido (o una cadena de dias seguidos que lo incluye) fue festivo o fin de semana justo antes de hoy,
      el aviso se corrio y sale hoy, el primer dia habil despues.
    """
    return _toca(hoy, lambda d: d.weekday() == dia_semanal)


def proximo_envio_semanal(dia_semanal: int, ahora: datetime | None = None) -> date:
    """Proxima fecha en que sale el aviso semanal (si hoy ya paso la hora de envio, cuenta desde manana)."""
    ahora = ahora or datetime.now(pytz.timezone(ZONA_HORARIA))
    d = ahora.date()
    if (ahora.hour, ahora.minute) >= HORA_SEMANAL:
        d += timedelta(days=1)
    for _ in range(21):                 # hay festivos seguidos de a 4 dias como maximo; 21 sobra
        if toca_semanal(d, dia_semanal):
            return d
        d += timedelta(days=1)
    return d
