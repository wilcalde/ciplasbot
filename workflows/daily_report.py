# workflows/daily_report.py

from datetime import datetime, date, time
import os
import json
import unicodedata
from services.whatsapp_service import send_whatsapp_message
from services.session_memory import CONFIG_DIR, SUPERVISORS_FILE, ALERT_LOG_FILE
from services.prompts import get_flow, get_prompt


def normalize(text):
    """
    🔤 Convierte texto a minúsculas sin acentos para comparación segura.
    """
    return unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode().lower()


def send_daily_report_request():
    """
    🚦 Envía mensaje inicial de apertura de informe diario.
    👉 Incluye saludo + primera pregunta real.
    👉 Crea sesión JSON lista para FastAPI sin verificación redundante.
    """
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users = json.load(f)["users"]
    except Exception as e:
        print(f"❌ Error cargando usuarios: {e}")
        return

    for user in users:
        if normalize(user.get("role", "")) == "supervisor":
            name = user["name"]
            phone = user["phone"]
            process = user["process"].upper()

            flow = get_flow(process)
            if not flow:
                print(f"❌ No se encontró flujo para: {process}")
                continue

            first_question = get_prompt(flow[0], process)
            msg = f"""👋 *Buenos días {name}*
Vamos a registrar la información de *{process}*.

{first_question}"""

            send_whatsapp_message(phone, msg)
            print(f"✅ Mensaje inicial enviado a {name} ({phone})")

            session_file = os.path.join(CONFIG_DIR, f"{phone}_session.json")
            session = {
                "process": process,
                "flow": flow,
                "step_index": 0,
                "answers": {}
            }
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)

            print(f"✅ Sesión creada: {session_file}")


def update_alert_status(phone, key):
    today = str(date.today())
    try:
        if os.path.exists(ALERT_LOG_FILE):
            with open(ALERT_LOG_FILE, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}
        if today not in data:
            data[today] = {}
        if phone not in data[today]:
            data[today][phone] = {}
        if data[today][phone].get(key, False):
            return False
        data[today][phone][key] = True
        with open(ALERT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"❌ Error actualizando alert_log: {e}")
        return False


def check_alert_already_sent(phone, key):
    today = str(date.today())
    try:
        with open(ALERT_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(today, {}).get(phone, {}).get(key, False)
    except:
        return False


def get_admin_phone():
    """
    🔍 Busca el número del administrador según el campo "role": "Administrador"
    """
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users = json.load(f)["users"]
        for u in users:
            if normalize(u.get("role", "")) == "administrador":
                return u.get("phone")
        return None
    except Exception as e:
        print(f"❌ Error buscando administrador: {e}")
        return None


def check_incomplete_reports_and_notify():
    """
    ⏰ Verifica sesiones de supervisores y alerta al administrador si no se han completado.
    También envía una alerta positiva cuando un supervisor completa su informe.
    Esta función debe ejecutarse periódicamente con el scheduler (por ejemplo, cada 5 minutos).
    """

    now = datetime.now()
    if now.time() < time(6, 0) or now.time() > time(22, 0):
        return
    if now.weekday() != 6:  # solo domingo
        return

    admin_phone = get_admin_phone()
    if not admin_phone:
        print("❌ No se encontró número de administrador para enviar alertas.")
        return

    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users = json.load(f)["users"]
    except Exception as e:
        print(f"❌ Error cargando usuarios: {e}")
        return

    for file in os.listdir(CONFIG_DIR):
        if not file.endswith("_session.json"):
            continue

        phone = file.split("_")[0]
        session_path = os.path.join(CONFIG_DIR, file)

        fecha_creacion = date.fromtimestamp(os.path.getctime(session_path))
        if fecha_creacion != date.today():
            continue

        try:
            with open(session_path, encoding="utf-8") as f:
                session = json.load(f)
        except Exception as e:
            print(f"❌ Error leyendo sesión de {phone}: {e}")
            continue

        flow = session.get("flow", [])
        answers = session.get("answers", {})
        total = len(flow)
        respondidas = len(answers)

        supervisor = next((u for u in users if u["phone"] == phone), None)
        name = supervisor["name"] if supervisor else phone

        if respondidas >= total:
            if not check_alert_already_sent(phone, "completed_alert_sent"):
                msg = (
                    f"✅ *Informe completado:*\n"
                    f"El supervisor *{name}* ya completó su informe diario.")
                try:
                    send_whatsapp_message(admin_phone, msg)
                    update_alert_status(phone, "completed_alert_sent")
                    print(f"📩 Alerta de informe completado enviada para {name}")
                except Exception as e:
                    print(f"❌ Error al enviar alerta de completado: {e}")
            continue

        if not check_alert_already_sent(phone, "pending_alert_sent"):
            msg = (
                f"⏰ *Alerta de supervisión pendiente:*\n"
                f"El supervisor *{name}* aún no ha completado su informe diario.\n"
                f"Respuestas: {respondidas} de {total}.")
            try:
                send_whatsapp_message(admin_phone, msg)
                update_alert_status(phone, "pending_alert_sent")
                print(f"🚨 Alerta enviada por sesión incompleta de {name}")
            except Exception as e:
                print(f"❌ Error al enviar alerta por sesión incompleta: {e}")
