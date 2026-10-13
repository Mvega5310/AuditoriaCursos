"""Festivos de Colombia y fecha de envio del aviso semanal."""
import sys
from datetime import date, datetime
from pathlib import Path

import pytest
import pytz

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import calendario                    # noqa: E402

LUN, MAR, MIE, JUE, VIE = range(5)


def test_dias_habiles_y_festivos_2026():
    assert calendario.es_festivo(date(2026, 10, 12)) and not calendario.es_habil(date(2026, 10, 12))   # Dia de la Raza (lunes)
    assert calendario.es_habil(date(2026, 10, 13))
    assert not calendario.es_habil(date(2026, 10, 10))                                                  # sabado
    assert not calendario.es_habil(date(2026, 4, 3))                                                    # Viernes Santo
    assert calendario.es_habil(date(2026, 4, 6))


@pytest.mark.parametrize("hoy,dia,esperado", [
    (date(2026, 10, 12), LUN, False),     # lunes festivo: no sale
    (date(2026, 10, 13), LUN, True),      # ... sale el martes
    (date(2026, 10, 14), LUN, False),     # y no se repite
    (date(2026, 10, 19), LUN, True),      # lunes normal
    (date(2026, 10, 13), MAR, True),      # quien eligio martes no cambia
    (date(2026, 10, 20), MAR, True),
    (date(2026, 10, 14), MAR, False),
    (date(2026, 10, 17), LUN, False),     # sabado: nunca
    (date(2026, 10, 18), VIE, False),     # domingo: nunca
])
def test_toca_semanal_un_festivo(hoy, dia, esperado):
    assert calendario.toca_semanal(hoy, dia) is esperado


@pytest.mark.parametrize("hoy,dia,esperado", [
    (date(2026, 4, 2), JUE, False),       # Jueves Santo
    (date(2026, 4, 3), VIE, False),       # Viernes Santo
    (date(2026, 4, 6), JUE, True),        # jueves y viernes festivos: ambos salen el lunes
    (date(2026, 4, 6), VIE, True),
    (date(2026, 4, 7), JUE, False),       # y no se repiten el martes
    (date(2026, 4, 7), VIE, False),
    (date(2026, 4, 1), MIE, True),        # un dia normal de esa semana
    (date(2026, 4, 6), LUN, True),        # el lunes de siempre sigue siendo lunes
])
def test_toca_semanal_semana_santa(hoy, dia, esperado):
    assert calendario.toca_semanal(hoy, dia) is esperado


def test_cada_semana_sale_exactamente_una_vez_en_todo_el_anio():
    """Para cualquier dia elegido, el aviso sale una sola vez por semana (nunca dos, nunca ninguna)
    salvo el desplazamiento de un festivo, que sigue siendo una sola vez."""
    from datetime import timedelta
    for dia in range(5):
        d, envios = date(2026, 1, 5), []
        while d < date(2026, 12, 28):
            if calendario.toca_semanal(d, dia):
                envios.append(d)
            d += timedelta(days=1)
        assert all(calendario.es_habil(e) for e in envios)
        for a, b in zip(envios, envios[1:]):
            assert 1 <= (b - a).days <= 11            # nunca pegados dos envios en el mismo dia ni huecos enormes
        assert len(envios) >= 50                      # ~52 semanas, algunas fusionadas por festivos seguidos


def test_proximo_envio():
    bogota = pytz.timezone("America/Bogota")
    # sabado 10/10/2026 -> el lunes 12 es festivo -> sale el martes 13
    assert calendario.proximo_envio_semanal(LUN, bogota.localize(datetime(2026, 10, 10, 9, 0))) == date(2026, 10, 13)
    # martes 13 a las 6:00 (antes de las 7:30) -> sale hoy; a las 8:00 ya paso -> la semana siguiente
    assert calendario.proximo_envio_semanal(MAR, bogota.localize(datetime(2026, 10, 13, 6, 0))) == date(2026, 10, 13)
    assert calendario.proximo_envio_semanal(MAR, bogota.localize(datetime(2026, 10, 13, 8, 0))) == date(2026, 10, 20)
