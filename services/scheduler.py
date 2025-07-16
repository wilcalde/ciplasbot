# services/scheduler.py

from apscheduler.schedulers.blocking import BlockingScheduler
from workflows.send_daily_tasks import send_daily_tasks
from workflows.daily_report import send_daily_report_request
from workflows.compile_daily_summary import compile_daily_summary
from workflows.supervision_questions import send_supervision_questions  # ✅ Envía preguntas
from workflows.compile_supervision_report import compile_supervision_report  # ✅ Genera resumen y envía email
from workflows.daily_report import check_incomplete_reports_and_notify #envia alertas cuando el informe no se ha enviado
from pytz import timezone

def start_scheduler():
    scheduler = BlockingScheduler(timezone=timezone("America/Bogota"))

    # ✅ 1. Enviar tareas del día
    scheduler.add_job(
        send_daily_tasks,
        'cron',
        hour=18,
        minute=30,
        day_of_week='0-6',
        id='send_tasks'
    )

    # ✅ 2. Enviar reporte de novedades
    scheduler.add_job(
        send_daily_report_request,
        'cron',
        hour=18,
        minute=35,
        day_of_week='0-6',
        id='send_report_request'
    )

    # ✅ 3. Compilar informe novedades y enviar
    scheduler.add_job(
        compile_daily_summary,
        'cron',
        hour=18,
        minute=45,
        day_of_week='0-6',
        id='compile_summary'
    )

    # ✅ 4. Solicitar resumen diario a supervisores
    scheduler.add_job(
        send_supervision_questions,
        'cron',
        hour=18,
        minute=50,
        day_of_week='0-6',
        id='send_supervision_questions'
    )

    # ✅ 5. Compilar respuestas y enviar email de supervisión
    scheduler.add_job(
        compile_supervision_report,
        'cron',
        hour=19,
        minute=00,
        day_of_week='0-6',
        id='compile_supervision_report'
    )

    scheduler.add_job(
    check_incomplete_reports_and_notify,
    'interval',
    minutes=5,                                    #tiempo envio alerta al administrador si el informe no se ha recibido
    id='monitor_supervision'
)

    print(f"🚦 CIPLASBOT Scheduler configurado:\n"
          f"   - 🕕 Tareas motivadoras: 10:01 PM (Lunes a Sábado)\n"
          f"   - 🕕 Solicitar informe: 10:02 PM (Lunes a Sábado)\n"
          f"   - 🕖 Consolidar informe: 10:04 PM (Lunes a Sábado)\n"
          f"   - 🕔 Cuestionario supervisión: 05:07 PM (Todos los días)\n"
          f"   - 🕓 Resumen supervisión (email): 05:14 PM (Todos los días)")

    scheduler.start()
