"""
Scheduler principal — ejecuta las alertas de vencimiento de forma automatica.

Horarios configurados (zona horaria: America/Bogota):
  - Alerta diaria:    Lunes a viernes a las 07:00
  - Alerta semanal:   Todos los lunes a las 07:30
  - Alerta quincenal: Dias 1 y 15 de cada mes a las 08:00
  - Reporte mensual:  Dia 1 de cada mes a las 08:30

Uso:
  python scheduler.py                  <- corre en primer plano (Ctrl+C para detener)
  pythonw scheduler.py                 <- corre en segundo plano sin ventana (Windows)

Para produccion en Windows se recomienda registrarlo como Tarea del Programador de tareas
o como servicio con NSSM (https://nssm.cc/).
"""
import sys
import logging
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from scripts.generar_alertas import ejecutar_alerta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ZONA_HORARIA = "America/Bogota"


def main() -> None:
    scheduler = BlockingScheduler(timezone=ZONA_HORARIA)

    # Alerta diaria: lunes a viernes, 07:00
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0, timezone=ZONA_HORARIA),
        args=["diaria"],
        id="alerta_diaria",
        name="Alerta diaria — vencen en 7 dias",
        misfire_grace_time=3600,  # tolera hasta 1h de retraso si el PC estaba apagado
    )

    # Alerta semanal: todos los lunes, 07:30
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day_of_week="mon", hour=7, minute=30, timezone=ZONA_HORARIA),
        args=["semanal"],
        id="alerta_semanal",
        name="Alerta semanal — vencen en 30 dias",
        misfire_grace_time=3600,
    )

    # Alerta quincenal: dias 1 y 15 de cada mes, 08:00
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day="1,15", hour=8, minute=0, timezone=ZONA_HORARIA),
        args=["quincenal"],
        id="alerta_quincenal",
        name="Alerta quincenal — vencen en 15 dias",
        misfire_grace_time=3600,
    )

    # Reporte mensual: dia 1 de cada mes, 08:30
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day=1, hour=8, minute=30, timezone=ZONA_HORARIA),
        args=["mensual"],
        id="reporte_mensual",
        name="Reporte mensual — vencen en 60 dias",
        misfire_grace_time=3600,
    )

    log.info("=== autCursos Scheduler iniciado ===")
    log.info(f"  Zona horaria : {ZONA_HORARIA}")
    log.info("  Alerta diaria    : Lun-Vie a las 07:00")
    log.info("  Alerta semanal   : Lunes  a las 07:30")
    log.info("  Alerta quincenal : Dias 1 y 15 a las 08:00")
    log.info("  Reporte mensual  : Dia 1  a las 08:30")
    log.info("Presiona Ctrl+C para detener.")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scheduler detenido por el usuario.")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
