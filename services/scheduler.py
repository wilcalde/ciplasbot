# services/scheduler.py

import os
from apscheduler.schedulers.blocking import BlockingScheduler
from pytz import timezone

# Workflows existentes
from workflows.send_daily_tasks import send_daily_tasks
from workflows.daily_report import send_daily_report_request, check_incomplete_reports_and_notify
from workflows.compile_daily_summary import compile_daily_summary
from workflows.supervision_questions import send_supervision_questions
from workflows.compile_supervision_report import compile_supervision_report
from workflows.dashboard_notifications import send_dashboard_links

# 🔔 Recordatorios de tareas (ADMIN + SUPERVISORES)
from services.tasks_manager import run_pending_tasks_reminders

# ⏰ Ventana WhatsApp (24h) + nudges
from services.wa_window_manager import schedule_window_jobs, load_config, run_nudges

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except Exception:
        return default

def _env_time(env_hour: str, env_min: str, default_hour: int, default_min: int):
    return _env_int(env_hour, default_hour), _env_int(env_min, default_min)

def start_scheduler():
    scheduler = BlockingScheduler(timezone=timezone("America/Bogota"))

    # ✅ 1. Enviar tareas del día (motivación/equipo)
    scheduler.add_job(
        send_daily_tasks,
        'cron',
        hour=_env_int("SEND_DAILY_TASKS_HOUR", 6),
        minute=_env_int("SEND_DAILY_TASKS_MINUTE", 30),
        day_of_week=os.getenv("SEND_DAILY_TASKS_DOW", "0-5"),
        id='send_tasks',
        misfire_grace_time=300,
        coalesce=True
    )

    # ✅ 2. Enviar solicitud de reporte de novedades
    scheduler.add_job(
        send_daily_report_request,
        'cron',
        hour=_env_int("REPORT_REQUEST_HOUR", 7),
        minute=_env_int("REPORT_REQUEST_MINUTE", 00),
        day_of_week=os.getenv("REPORT_REQUEST_DOW", "0-5"),
        id='send_report_request',
        misfire_grace_time=300,
        coalesce=True
    )

    # ✅ 3. Compilar informe de novedades y enviar
    scheduler.add_job(
        compile_daily_summary,
        'cron',
        hour=_env_int("COMPILE_SUMMARY_HOUR", 8),
        minute=_env_int("COMPILE_SUMMARY_MINUTE", 00),
        day_of_week=os.getenv("COMPILE_SUMMARY_DOW", "0-5"),
        id='compile_summary',
        misfire_grace_time=300,
        coalesce=True
    )

    # ✅ 3.1 Recordatorio de tareas PENDIENTES (ADMIN + SUPERVISORES)
    rem_hour, rem_min = _env_time(
        "PENDING_TASKS_REMINDER_HOUR",
        "PENDING_TASKS_REMINDER_MINUTE",
        6, 20
    )
    rem_dow = os.getenv("PENDING_TASKS_REMINDER_DOW", "0-5")

    scheduler.add_job(
        run_pending_tasks_reminders,
        'cron',
        hour=rem_hour,
        minute=rem_min,
        day_of_week=rem_dow,
        id='send_all_pending_tasks',
        kwargs={
            'send_if_empty': True,
            'today_only': False
        },
        misfire_grace_time=300,
        coalesce=True
    )

    # ✅ 4. Solicitar cuestionario de supervisión
    scheduler.add_job(
        send_supervision_questions,
        'cron',
        hour=_env_int("SUP_QUESTIONS_HOUR", 14),
        minute=_env_int("SUP_QUESTIONS_MINUTE", 30),
        day_of_week=os.getenv("SUP_QUESTIONS_DOW", "0-4"),
        id='send_supervision_questions',
        misfire_grace_time=300,
        coalesce=True
    )

    # ✅ 5. Compilar respuestas y enviar email de supervisión
    scheduler.add_job(
        compile_supervision_report,
        'cron',
        hour=_env_int("SUP_REPORT_HOUR", 17),
        minute=_env_int("SUP_REPORT_MINUTE", 00),
        day_of_week=os.getenv("SUP_REPORT_DOW", "0-4"),
        id='compile_supervision_report',
        misfire_grace_time=300,
        coalesce=True
    )

    # ✅ 6. Monitorear informes incompletos y notificar cada N minutos
    scheduler.add_job(
        check_incomplete_reports_and_notify,
        'interval',
        minutes=_env_int("SUP_MONITOR_MINUTES", 5),
        id='monitor_supervision',
        misfire_grace_time=60,
        coalesce=True
    )

    # ✅ 7. Enviar link del dashboard a cada supervisor (resultados día anterior)
    scheduler.add_job(
        send_dashboard_links,
        'cron',
        hour=_env_int("DASHBOARD_LINKS_HOUR", 11),
        minute=_env_int("DASHBOARD_LINKS_MINUTE", 0),
        day_of_week=os.getenv("DASHBOARD_LINKS_DOW", "0-5"),
        id='send_dashboard_links',
        misfire_grace_time=300,
        coalesce=True
    )

    # ✅ 8. (Nuevo) Nudges de ventana WhatsApp 24h
    schedule_window_jobs(scheduler)  # registra el job periódico
    run_nudges()  # crea config/wa_conversations.json al arranque si no existe

    # —— Resumen consola ——
    wa_cfg = load_config()
    print(
        "🚦 CIPLASBOT Scheduler configurado:\n"
        "   - 🕡 Tareas motivadoras (equipo): "
        f"{_env_int('SEND_DAILY_TASKS_HOUR', 6):02d}:{_env_int('SEND_DAILY_TASKS_MINUTE', 30):02d} "
        f"(DOW='{os.getenv('SEND_DAILY_TASKS_DOW', '0-4')}')\n"
        "   - 📣 Solicitud reporte novedades: "
        f"{_env_int('REPORT_REQUEST_HOUR', 7):02d}:{_env_int('REPORT_REQUEST_MINUTE', 0):02d} "
        f"(DOW='{os.getenv('REPORT_REQUEST_DOW', '0-5')}')\n"
        "   - 🧠 Admin + Supervisores: pendientes: "
        f"{rem_hour:02d}:{rem_min:02d} (según DOW='{rem_dow}')\n"
        "   - 📝 Compilar resumen diario: "
        f"{_env_int('COMPILE_SUMMARY_HOUR', 8):02d}:{_env_int('COMPILE_SUMMARY_MINUTE', 0):02d} "
        f"(DOW='{os.getenv('COMPILE_SUMMARY_DOW', '0-5')}')\n"
        "   - ❓ Cuestionario supervisión: "
        f"{_env_int('SUP_QUESTIONS_HOUR', 14):02d}:{_env_int('SUP_QUESTIONS_MINUTE', 30):02d} "
        f"(DOW='{os.getenv('SUP_QUESTIONS_DOW', '0-4')}')\n"
        "   - 📧 Resumen supervisión (email): "
        f"{_env_int('SUP_REPORT_HOUR', 17):02d}:{_env_int('SUP_REPORT_MINUTE', 0):02d} "
        f"(DOW='{os.getenv('SUP_REPORT_DOW', '0-4')}')\n"
        "   - ⏱ Monitor supervisión: cada "
        f"{_env_int('SUP_MONITOR_MINUTES', 5)} min\n"
        "   - 📊 Envío dashboards: "
        f"{_env_int('DASHBOARD_LINKS_HOUR', 11):02d}:{_env_int('DASHBOARD_LINKS_MINUTE', 0):02d} "
        f"(DOW='{os.getenv('DASHBOARD_LINKS_DOW', '0-5')}')\n"
        "   - 🔔 WA ventana 24h (nudges): "
        f"ventana={wa_cfg.get('window_hours', 24)}h, "
        f"nudge a {wa_cfg.get('nudge_before_minutes', 60)} min, "
        f"chequeo cada {wa_cfg.get('check_every_minutes', 5)} min\n"
    )

    scheduler.start()
