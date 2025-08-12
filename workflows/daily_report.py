# workflows/daily_report.py
from datetime import datetime, date, time
import os
import json
import unicodedata
from typing import Dict, Tuple, Optional

from services.whatsapp_service import send_whatsapp_message
from services.session_memory import CONFIG_DIR, SUPERVISORS_FILE, ALERT_LOG_FILE
from services.prompts import get_flow, get_prompt


# =========================
# Utilidades de normalización / IO
# =========================

def normalize(text: str) -> str:
    """
    🔤 Convierte texto a minúsculas sin acentos para comparaciones (roles, etc.)
    """
    return unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode().lower()

def normalize_key(label: str) -> str:
    """
    🔤 Normaliza etiquetas de temas a UPPER_SNAKE (ej: 'General notes' -> 'GENERAL_NOTES')
    """
    if not isinstance(label, str):
        label = str(label)
    return (
        label.strip()
             .replace("-", " ")
             .replace("/", " ")
             .replace(".", " ")
             .replace(",", " ")
             .upper()
             .replace("  ", " ")
             .replace(" ", "_")
    )

def get_session_path(phone: str) -> str:
    """
    📁 Construye la ruta del archivo de sesión por número de teléfono.
    """
    return os.path.join(CONFIG_DIR, f"{phone}_session.json")

def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# =========================
# Flujo: envío inicial
# =========================

def send_daily_report_request():
    """
    🚦 Envía el mensaje inicial para cada supervisor y crea la sesión JSON.
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

            # Primera pregunta
            first_question = get_prompt(flow[0], process)
            msg = (
                f"👋 *Buenos días {name}*\n"
                f"Vamos a registrar la información de *{process}*.\n\n"
                f"{first_question}"
            )

            # Enviar WhatsApp
            try:
                send_whatsapp_message(phone, msg)
                print(f"✅ Mensaje inicial enviado a {name} ({phone})")
            except Exception as e:
                print(f"❌ Error enviando mensaje a {phone}: {e}")
                continue

            # Crear sesión
            session = {
                "process": process,
                "supervisor": name,
                "flow": flow,          # Lista de etiquetas legibles (p. ej. "General notes")
                "step_index": 0,       # Índice del tema actual a responder
                "answers": {},         # Respuestas por tema normalizado (UPPER_SNAKE)
                "fecha_hora": datetime.now().isoformat()
            }
            session_file = get_session_path(phone)
            print("🏷️ Creando sesión en:", session_file)
            try:
                save_json(session_file, session)
                print(f"✅ Sesión creada: {session_file}")
            except Exception as e:
                print(f"❌ Error creando sesión para {phone}: {e}")


# =========================
# Flujo: manejo de respuestas
# =========================

def _save_current_answer(session: Dict, user_reply: str) -> Tuple[Dict, Optional[str]]:
    """
    Guarda la respuesta del paso actual ANTES de avanzar el step_index.
    Devuelve (session_modificada, topic_key_guardado).
    """
    flow = session.get("flow", [])
    step_index = session.get("step_index", 0)

    # Asegurar contenedor de respuestas
    answers = session.get("answers", {})
    if not isinstance(answers, dict):
        answers = {}
    session["answers"] = answers  # re-asignación por seguridad

    if 0 <= step_index < len(flow):
        current_label = flow[step_index]           # ej: "General notes"
        topic_key = normalize_key(current_label)   # ej: "GENERAL_NOTES"
        answers[topic_key] = (user_reply or "").strip()
        return session, topic_key

    return session, None

def _send_next_question(phone: str, process: str, next_label: str) -> None:
    """
    Envía la siguiente pregunta según la etiqueta del siguiente tema.
    """
    try:
        prompt = get_prompt(next_label, process)
    except Exception:
        prompt = f"Por favor, compárteme la información de: *{next_label}*"

    try:
        send_whatsapp_message(phone, prompt)
    except Exception as e:
        print(f"❌ Error enviando siguiente pregunta a {phone}: {e}")

def handle_supervisor_reply(phone: str, user_reply: str) -> Dict:
    """
    Maneja la respuesta del supervisor:
      1) Abre la sesión por teléfono.
      2) GUARDA la respuesta del tema actual.
      3) Persiste en disco inmediatamente.
      4) Incrementa step_index y determina si hay siguiente pregunta o si finaliza.

    Retorna un dict con estado y metadatos útiles.
    """
    session_path = get_session_path(phone)
    if not os.path.exists(session_path):
        return {"status": "ERROR", "message": "No existe una sesión activa para este número."}

    try:
        session = load_json(session_path)
    except Exception as e:
        return {"status": "ERROR", "message": f"Error leyendo la sesión: {e}"}

    process = session.get("process", "")
    flow = session.get("flow", [])
    step_index = session.get("step_index", 0)

    # 1) Guardar respuesta del paso actual (clave normalizada) ANTES de avanzar
    session, saved_key = _save_current_answer(session, user_reply)

    # 2) Persistir inmediatamente
    try:
        save_json(session_path, session)
    except Exception as e:
        return {"status": "ERROR", "message": f"Error guardando la respuesta: {e}"}

    # 3) Avanzar paso
    step_index += 1
    session["step_index"] = step_index
    session["updated_at"] = datetime.now().isoformat()

    # 4) ¿Quedan más preguntas?
    if step_index < len(flow):
        next_label = flow[step_index]  # etiqueta legible
        # Guardar avance y enviar siguiente pregunta
        try:
            save_json(session_path, session)
        except Exception as e:
            return {"status": "ERROR", "message": f"Error actualizando la sesión: {e}"}

        _send_next_question(phone, process, next_label)
        return {
            "status": "CONTINUE",
            "saved_key": saved_key,
            "next_topic_key": normalize_key(next_label),
            "next_label": next_label,
            "step_index": step_index,
            "total_steps": len(flow)
        }

    # 5) Si era la última, marcar como completado y guardar definitivamente
    session["completed_at"] = datetime.now().isoformat()
    try:
        save_json(session_path, session)
    except Exception as e:
        return {"status": "ERROR", "message": f"Error cerrando la sesión: {e}"}

    # Opcional: mensaje de cierre al supervisor
    try:
        send_whatsapp_message(phone, "✅ Gracias. Tu informe diario ha sido registrado completo.")
    except Exception as e:
        print(f"❌ Error enviando mensaje de cierre a {phone}: {e}")

    return {
        "status": "DONE",
        "saved_key": saved_key,
        "message": "Formulario completado y guardado.",
        "total_steps": len(flow)
    }


# =========================
# Alertas de avance / pendientes
# =========================

def update_alert_status(phone: str, key: str) -> bool:
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

def check_alert_already_sent(phone: str, key: str) -> bool:
    today = str(date.today())
    try:
        with open(ALERT_LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(today, {}).get(phone, {}).get(key, False)
    except:
        return False

def get_admin_phone() -> Optional[str]:
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
    ⏰ Verifica sesiones y alerta al administrador si no se han completado.
    Ejecutar con el scheduler (p. ej. cada 5–15 minutos, en ventana horaria definida).
    """
    now = datetime.now()
    if now.time() < time(6, 0) or now.time() > time(22, 0):
        return
    if now.weekday() != 6:  # solo domingo (ajusta si necesitas otros días)
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

        # Filtrar sesiones del día actual
        try:
            fecha_creacion = date.fromtimestamp(os.path.getctime(session_path))
        except Exception:
            continue
        if fecha_creacion != date.today():
            continue

        try:
            session = load_json(session_path)
        except Exception as e:
            print(f"❌ Error leyendo sesión de {phone}: {e}")
            continue

        flow = session.get("flow", [])
        answers = session.get("answers", {})
        total = len(flow)
        respondidas = len(answers)

        supervisor = next((u for u in users if u.get("phone") == phone), None)
        name = supervisor["name"] if supervisor else phone

        # Completado
        if respondidas >= total and total > 0:
            if not check_alert_already_sent(phone, "completed_alert_sent"):
                msg = (
                    f"✅ *Informe completado:*\n"
                    f"El supervisor *{name}* ya completó su informe diario."
                )
                try:
                    send_whatsapp_message(admin_phone, msg)
                    update_alert_status(phone, "completed_alert_sent")
                    print(f"📩 Alerta de informe completado enviada para {name}")
                except Exception as e:
                    print(f"❌ Error al enviar alerta de completado: {e}")
            continue

        # Pendiente
        if not check_alert_already_sent(phone, "pending_alert_sent"):
            msg = (
                f"⏰ *Alerta de supervisión pendiente:*\n"
                f"El supervisor *{name}* aún no ha completado su informe diario.\n"
                f"Respuestas: {respondidas} de {total}."
            )
            try:
                send_whatsapp_message(admin_phone, msg)
                update_alert_status(phone, "pending_alert_sent")
                print(f"🚨 Alerta enviada por sesión incompleta de {name}")
            except Exception as e:
                print(f"❌ Error al enviar alerta por sesión incompleta: {e}")
