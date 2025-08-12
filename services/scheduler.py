# services/scheduler.py

from apscheduler.schedulers.blocking import BlockingScheduler
from workflows.send_daily_tasks import send_daily_tasks
from workflows.daily_report import send_daily_report_request, check_incomplete_reports_and_notify
from workflows.compile_daily_summary import compile_daily_summary
from workflows.supervision_questions import send_supervision_questions  # ✅ Envía preguntas
from workflows.compile_supervision_report import compile_supervision_report  # ✅ Genera resumen y envía email
from workflows.dashboard_notifications import send_dashboard_links  # ✅ Enviar link de dashboards a supervisores
from pytz import timezone

def start_scheduler():
    scheduler = BlockingScheduler(timezone=timezone("America/Bogota"))

    # ✅ 1. Enviar tareas del día
    scheduler.add_job(
        send_daily_tasks,
        'cron',
        hour=6,
        minute=40,
        day_of_week='0-5',
        id='send_tasks'
    )

    # ✅ 2. Enviar reporte de novedades
    scheduler.add_job(
        send_daily_report_request,
        'cron',
        hour=7,
        minute=00,
        day_of_week='0-5',
        id='send_report_request'
    )

    # ✅ 3. Compilar informe novedades y enviar
    scheduler.add_job(
        compile_daily_summary,
        'cron',
        hour=8,
        minute=00,
        day_of_week='0-5',
        id='compile_summary'
    )

    # ✅ 4. Solicitar resumen diario a supervisores
    scheduler.add_job(
        send_supervision_questions,
        'cron',
        hour=20,
        minute=31,
        day_of_week='0-4',
        id='send_supervision_questions'
    )

    # ✅ 5. Compilar respuestas y enviar email de supervisión
    scheduler.add_job(
        compile_supervision_report,
        'cron',
        hour=20,
        minute=37,
        day_of_week='0-4',
        id='compile_supervision_report'
    )

    # ✅ 6. Monitorear informes incompletos y notificar cada 5 minutos
    scheduler.add_job(
        check_incomplete_reports_and_notify,
        'interval',
        minutes=5,
        #day_of_week='0-5',
        id='monitor_supervision'
    )

    # ✅ 7. Enviar link del dashboard a cada supervisor para resultados del día anterior
    scheduler.add_job(
        send_dashboard_links,
        'cron',
        hour=11,
        minute=30,
        day_of_week='0-5',
        id='send_dashboard_links'
    )

    print(
        "🚦 CIPLASBOT Scheduler configurado:\n"
        "   - 🕡 Tareas motivadoras: 06:30 AM (Lunes–Sábado)\n"
        "   - 📣 Envío reporte novedades: 08:00 AM (Lunes–Sábado)\n"
        "   - 📝 Compilar resumen diario: 09:00 AM (Lunes–Sábado)\n"
        "   - ❓ Cuestionario supervisión: 11:00 AM (Lunes–Sábado)\n"
        "   - 📧 Resumen supervisión (email): 01:00 PM (Lunes–Sábado)\n"
        "   - ⏱ Monitor supervisión: cada 5 minutos\n"
        "   - 📊 Envío dashboards: 07:00 AM (Lunes–Sábado)\n"
    )

    scheduler.start()
