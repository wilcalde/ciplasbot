# services/scheduler.py

from apscheduler.schedulers.blocking import BlockingScheduler
from pytz import timezone

# Workflows existentes
from workflows.send_daily_tasks import send_daily_tasks
from workflows.daily_report import send_daily_report_request, check_incomplete_reports_and_notify
from workflows.compile_daily_summary import compile_daily_summary
from workflows.supervision_questions import send_supervision_questions  # ✅ Envía preguntas
from workflows.compile_supervision_report import compile_supervision_report  # ✅ Genera resumen y envía email
from workflows.dashboard_notifications import send_dashboard_links  # ✅ Enviar link de dashboards a supervisores

# 🔔 NUEVO: recordatorio de tareas pendientes (solo ADMIN)
from services.tasks_manager import send_daily_pending_tasks_reminder


def start_scheduler():
    scheduler = BlockingScheduler(timezone=timezone("America/Bogota"))

    # ✅ 1. Enviar tareas del día (motivación/equipo)
    scheduler.add_job(
        send_daily_tasks,
        'cron',
        hour=6,
        minute=30,
        day_of_week='0-4',
        id='send_tasks'
    )

    # ✅ 2. Enviar solicitud de reporte de novedades
    scheduler.add_job(
        send_daily_report_request,
        'cron',
        hour=20,
        minute=42,
        day_of_week='0-5',
        id='send_report_request'
    )

    # ✅ 3. Compilar informe de novedades y enviar
    scheduler.add_job(
        compile_daily_summary,
        'cron',
        hour=8,
        minute=0,
        day_of_week='0-5',
        id='compile_summary'
    )

    # ✅ 3.1 NUEVO — Recordatorio al ADMIN: tareas PENDIENTES de HOY (07:30)
    scheduler.add_job(
        send_daily_pending_tasks_reminder,
        'cron',
        hour=6,
        minute=45,
        day_of_week='0-4',
        id='send_admin_pending_tasks',
        kwargs={'send_if_empty': True},   # envía aunque no haya pendientes (cambia a False si prefieres silencio)
        misfire_grace_time=300,           # 5 min de tolerancia
        coalesce=True
    )

    # ✅ 4. Solicitar cuestionario de supervisión
    scheduler.add_job(
        send_supervision_questions,
        'cron',
        hour=20,
        minute=39,
        day_of_week='0-4',
        id='send_supervision_questions'
    )

    # ✅ 5. Compilar respuestas y enviar email de supervisión
    scheduler.add_job(
        compile_supervision_report,
        'cron',
        hour=17,
        minute=0,
        day_of_week='0-4',
        id='compile_supervision_report'
    )

    # ✅ 6. Monitorear informes incompletos y notificar cada 5 minutos
    scheduler.add_job(
        check_incomplete_reports_and_notify,
        'interval',
        minutes=5,
        id='monitor_supervision'
    )

    # ✅ 7. Enviar link del dashboard a cada supervisor (resultados día anterior)
    scheduler.add_job(
        send_dashboard_links,
        'cron',
        hour=11,
        minute=0,
        day_of_week='0-5',
        id='send_dashboard_links'
    )

    print(
        "🚦 CIPLASBOT Scheduler configurado:\n"
        "   - 🕡 Tareas motivadoras (equipo): 06:30 AM (Lun–Sáb)\n"
        "   - 📣 Solicitud reporte novedades: 07:00 AM (Lun–Sáb)\n"
        "   - 🧠 ADMIN: tareas PENDIENTES de HOY: 07:30 AM (Lun–Sáb)\n"
        "   - 📝 Compilar resumen diario: 08:00 AM (Lun–Sáb)\n"
        "   - ❓ Cuestionario supervisión: 09:02 PM (Lun–Vie)\n"
        "   - 📧 Resumen supervisión (email): 05:00 PM (Lun–Vie)\n"
        "   - ⏱ Monitor supervisión: cada 5 minutos\n"
        "   - 📊 Envío dashboards: 11:00 AM (Lun–Sáb)\n"
    )

    scheduler.start()
