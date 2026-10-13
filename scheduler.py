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
import config
from sheets import sincronizar_todas

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ZONA_HORARIA = "America/Bogota"


def registrar_jobs(scheduler) -> None:
    """Registra la sincronizacion con Google Sheets y las 4 alertas (Blocking para el script, Background para la web)."""

    # Sincroniza los Google Sheets de los clientes ANTES de las alertas, todos los dias a las 06:30.
    # Solo si la funcion esta activada (SHEETS_ENABLED=1); por defecto esta apagada.
    if config.SHEETS_ENABLED:
        scheduler.add_job(
            sincronizar_todas,
            CronTrigger(hour=6, minute=30, timezone=ZONA_HORARIA),
            id="sync_sheets",
            name="Sincronizar Google Sheets",
            misfire_grace_time=3600,
        )

    # Alerta diaria: lunes a viernes, 07:00
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=0, timezone=ZONA_HORARIA),
        args=["diaria"],
        kwargs={"respetar_dia": True},
        id="alerta_diaria",
        name="Alerta diaria — vencidos y 7 dias",
        misfire_grace_time=3600,  # tolera hasta 1h de retraso si el PC estaba apagado
    )

    # Alerta semanal: se revisa de lunes a viernes a las 07:30; cada empresa la recibe el dia que elija (por defecto, lunes)
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=ZONA_HORARIA),
        args=["semanal"],
        kwargs={"respetar_dia": True},
        id="alerta_semanal",
        name="Alerta semanal — 7 dias + anticipacion",
        misfire_grace_time=3600,
    )

    # Alerta quincenal: dias 1 y 15 (si cae en fin de semana o festivo, el siguiente dia habil). Se revisa lun-vie a las 08:00
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=ZONA_HORARIA),
        args=["quincenal"],
        kwargs={"respetar_dia": True},
        id="alerta_quincenal",
        name="Alerta quincenal — 15 dias + anticipacion",
        misfire_grace_time=3600,
    )

    # Reporte mensual: dia 1 (si cae en fin de semana o festivo, el siguiente dia habil). Se revisa lun-vie a las 08:30
    scheduler.add_job(
        ejecutar_alerta,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=ZONA_HORARIA),
        args=["mensual"],
        kwargs={"respetar_dia": True},
        id="reporte_mensual",
        name="Reporte mensual — 30 dias + anticipacion",
        misfire_grace_time=3600,
    )


def main() -> None:
    scheduler = BlockingScheduler(timezone=ZONA_HORARIA)
    registrar_jobs(scheduler)

    log.info("=== Vencimientos: programador iniciado ===")
    log.info(f"  Zona horaria : {ZONA_HORARIA}")
    log.info("  Google Sheets    : " + ("todos los dias a las 06:30" if config.SHEETS_ENABLED else "desactivado (SHEETS_ENABLED=0)"))
    log.info("  Alerta diaria    : dias habiles a las 07:00")
    log.info("  Alerta semanal   : el dia que elija cada empresa, 07:30 (festivo -> siguiente dia habil)")
    log.info("  Alerta quincenal : dias 1 y 15, 08:00 (fin de semana o festivo -> siguiente dia habil)")
    log.info("  Reporte mensual  : dia 1, 08:30 (fin de semana o festivo -> siguiente dia habil)")
    log.info("Presiona Ctrl+C para detener.")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        log.info("Scheduler detenido por el usuario.")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
