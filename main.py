# main.py
from fastapi import FastAPI
from pydantic import BaseModel
from services.prompts import get_prompt, get_flow
from services.whatsapp_service import send_whatsapp_message
from services.session_memory import sessions, CONFIG_DIR, SUPERVISORS_FILE
import json
import os
import re
import requests
import datetime
from openai import OpenAI

# Flujos de supervisión
from workflows.daily_report import update_alert_status, check_alert_already_sent, get_admin_phone
from workflows.supervision_questions import (
    handle_response,
    load_supervision_session_if_exists,
    send_supervision_questions,
    ask_supervision_questions
)

# 👇 Nuevo: Gestor NLU de tareas (sistema por lenguaje natural)
from services.tasks_manager import (
    handle_followup,          # maneja pasos faltantes (pedir título, desambiguar ID, etc.)
    maybe_handle_task_message, # interpreta la intención (crear, ver, editar, eliminar)
    send_task_menu            # permite abrir el menú con /nueva_tarea o /tareas
)

app = FastAPI()

# 📂 Directorio de tareas (opcional; no se usa para almacenamiento principal)
TASKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks")

# Asegurar carpeta CONFIG_DIR existe
os.makedirs(CONFIG_DIR, exist_ok=True)

WEBHOOK_URL = "https://hook.make.com/tu_webhook"  # 👉 Reemplaza con tu URL real

# 🧠 Cliente OpenAI
client = OpenAI()


# =========================
# Utilidades
# =========================

def normalize_phone(p: str) -> str:
    """
    Deja solo dígitos en el número para que coincida con el usado en archivos de sesión.
    Ej: '+57 317-638-0061' -> '573176380061'
    """
    if not p:
        return ""
    return re.sub(r"\D", "", p)

def get_user_name_by_phone(phone_digits: str) -> str:
    """
    Busca el nombre en SUPERVISORS_FILE por 'phone' (normalizado a dígitos).
    """
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users_data = json.load(f)
        for user in users_data.get("users", []):
            u_phone = normalize_phone(user.get("phone", ""))
            if u_phone == phone_digits:
                return user.get("name", "usuario")
    except Exception as e:
        print(f"⚠️ No se pudo leer SUPERVISORS_FILE para nombre: {e}")
    return "usuario"


def respuesta_inteligente(texto: str, nombre: str, numero: str) -> str:
    """
    Respuestas por defecto (IA general). La gestión de tareas se maneja en el endpoint
    antes de llegar aquí, para evitar duplicidades.
    """
    texto_low = (texto or "").strip().lower()

    # Atajo simple “tareas del día” (legacy)
    if texto_low in ("tareas", "tareas del día", "tareas del dia"):
        archivo_tareas = os.path.join(TASKS_DIR, f"{nombre.lower()}.json")
        if os.path.exists(archivo_tareas):
            with open(archivo_tareas, encoding="utf-8") as f:
                tareas = json.load(f)
            hoy = datetime.datetime.now().strftime("%Y-%m-%d")
            lista = tareas.get(hoy)
            if lista:
                lineas = "\n".join([f"✅ {t}" for t in lista])
                return f"📋 *Tareas asignadas para hoy:*\n{lineas}"
        return "📭 Hoy no tienes tareas asignadas."

    # IA general
    prompt_sistema = (
        f"Eres un asistente virtual llamado CiplasBot. Tu tarea es responder con amabilidad y precisión al usuario {nombre}, "
        "quien trabaja en la planta de producción de Ciplas S.A.S. No tienes flujo activo en este momento, "
        "pero puedes responder preguntas generales sobre procesos, producción, dudas comunes o instrucciones simples. "
        "Si no sabes la respuesta, responde con: 'Lo siento, por ahora no tengo información sobre ese tema. "
        "Puedes escribir \"ayuda\" para ver opciones disponibles.'"
    )
    try:
        chat = client.chat.completions.create(
            model="o4-mini",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": texto}
            ]
        )
        return chat.choices[0].message.content.strip()
    except Exception as e:
        print("❌ Error al generar respuesta con OpenAI:", e)
        return "Lo siento, estoy teniendo dificultades para procesar tu mensaje. Intenta más tarde."


class WhatsAppMessage(BaseModel):
    phone: str
    message: str | None = None   # puede venir null si el usuario envía media


@app.post("/ciplasbot")
async def handle_ciplasbot(payload: WhatsAppMessage):
    # 🔢 Normaliza número entrante
    numero = normalize_phone(payload.phone)

    # 🗣️ Extrae texto (puede venir None si es media/archivo)
    texto = (payload.message or "").strip()
    texto_low = texto.lower()

    # Si no hay texto (audio/imagen/documento), pedir texto y salir sin romper el flujo
    if not isinstance(texto, str) or texto == "":
        try:
            send_whatsapp_message(
                numero,
                "🙏 Para registrar tu respuesta necesito un *mensaje de texto*. ¿Puedes escribirlo, por favor?"
            )
        except Exception as e:
            print(f"❌ Error enviando aviso de texto requerido a {numero}: {e}")
        return {"status": "ok", "detail": "no text content; user prompted"}

    # ——————————————————————————————————————————————
    # 0) GESTIÓN DE TAREAS (NLU) — tiene prioridad
    # ——————————————————————————————————————————————
    # Comando para abrir menú (compat con flujo anterior)
    if texto_low.startswith("/nueva_tarea") or texto_low in ("/tareas", "menu tareas", "gestión de tareas", "gestionar tareas"):
        try:
            send_task_menu(numero)
        except Exception as e:
            print(f"❌ Error enviando menú de tareas: {e}")
        return {"status": "ok", "detail": "task_menu_sent"}

    # Si hay un flujo de seguimiento abierto (p.ej. pedir título, desambiguar ID)
    if handle_followup(texto, numero):
        return {"status": "ok", "detail": "task_followup_handled"}

    # Intentar interpretar el mensaje como acción de tareas (solo admin internamente)
    nombre_admin = get_user_name_by_phone(numero)
    if maybe_handle_task_message(texto, nombre_admin, numero):
        return {"status": "ok", "detail": "task_message_handled"}
    # ——————————————————————————————————————————————

    # 1️⃣ Cargar sesión de supervisión si existe en disco
    if numero not in sessions:
        load_supervision_session_if_exists(numero)

    # 2️⃣ Flujo supervisión
    if numero in sessions and sessions[numero].get("process") == "SUPERVISION":
        handle_response(numero, texto)
        return {"status": "ok", "detail": "handled by supervision_questions"}

    # 3️⃣ Flujo tradicional por sesión JSON
    session_file = os.path.join(CONFIG_DIR, f"{numero}_session.json")
    session = sessions.get(numero)

    # Si no hay sesión en memoria, intenta cargar desde archivo
    if not session:
        if os.path.exists(session_file):
            print("🔍 Cargando sesión desde archivo:", session_file)
            try:
                with open(session_file, encoding="utf-8") as f:
                    session = json.load(f)
                sessions[numero] = session
            except Exception as e:
                print(f"❌ Error cargando sesión desde archivo: {e}")
                session = None
        else:
            # Sin flujo activo -> IA general
            nombre = get_user_name_by_phone(numero)
            respuesta = respuesta_inteligente(texto, nombre, numero)
            try:
                # Evita enviar textos vacíos si alguna ruta anterior ya respondió
                if respuesta and respuesta.strip().lower() not in ("ok",):
                    send_whatsapp_message(numero, respuesta)
            except Exception as e:
                print(f"❌ Error enviando respuesta general a {numero}: {e}")
            return {"status": "no_flow", "reply": respuesta}

    # Validación de flujo
    flow = session.get("flow", [])
    step_index = session.get("step_index", 0)

    if not flow or step_index >= len(flow):
        reply = "✅ Ya completaste todas las preguntas. Si deseas empezar de nuevo, escribe /start o espera el próximo cuestionario."
        try:
            send_whatsapp_message(numero, reply)
        except Exception as e:
            print(f"❌ Error enviando mensaje de flujo finalizado a {numero}: {e}")
        # Limpieza suave en memoria; NO borra archivo si quieres mantenerlo
        sessions.pop(numero, None)
        return {"status": "done", "detail": "flow completed or index out of range"}

    current_step = flow[step_index]

    # 4️⃣ Modo edición
    if texto.strip().upper() == "EDITAR":
        # Muestra el listado con lo que hay (si no hay respuesta, marca ❌)
        answers = session.get("answers", {})
        lines = [
            f"{idx+1}️⃣ {step}: {answers.get(step, '❌ Sin respuesta')}"
            for idx, step in enumerate(flow)
        ]
        listado = "\n".join(lines)
        reply = (
            f"📋 *Tus respuestas actuales:*\n{listado}\n\n"
            f"👉 Escribe el número de la pregunta que deseas corregir."
        )
        session["editing"] = True
        sessions[numero] = session
        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Error guardando sesión (modo edición): {e}")
        try:
            send_whatsapp_message(numero, reply)
        except Exception as e:
            print(f"❌ Error enviando listado de edición a {numero}: {e}")
        return {"status": "ok", "mode": "editing", "reply": reply}

    if session.get("editing"):
        try:
            sel = int(texto.strip()) - 1
            if sel < 0 or sel >= len(flow):
                reply = "⚠️ Número inválido. Escribe un número válido de la lista."
            else:
                session["step_index"] = sel
                session["answers"].pop(flow[sel], None)  # elimina respuesta previa de esa pregunta
                reply = f"✏️ Corrige por favor: {get_prompt(flow[sel], session['process'])}"
                session.pop("editing", None)
        except ValueError:
            reply = "⚠️ Por favor, escribe solo el número de la pregunta a corregir."
        sessions[numero] = session
        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Error guardando sesión (selección de edición): {e}")
        try:
            send_whatsapp_message(numero, reply)
        except Exception as e:
            print(f"❌ Error enviando prompt de corrección a {numero}: {e}")
        return {"status": "ok", "mode": "editing", "reply": reply}

    # 5️⃣ Registrar respuesta del paso actual y avanzar
    session_answers = session.get("answers", {})
    if not isinstance(session_answers, dict):
        session_answers = {}
    session["answers"] = session_answers

    # 💾 Guardar ANTES de incrementar (evita perder la última respuesta)
    session['answers'][current_step] = texto
    session['step_index'] = step_index + 1

    # ¿Quedan más preguntas?
    if session['step_index'] < len(flow):
        reply = get_prompt(flow[session['step_index']], session['process'])

        # Guardar progreso intermedio en memoria y disco
        sessions[numero] = session
        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Error guardando progreso de sesión: {e}")

        # Enviar siguiente pregunta
        try:
            send_whatsapp_message(numero, reply)
        except Exception as e:
            print(f"❌ Error enviando siguiente pregunta a {numero}: {e}")
        return {"status": "ok", "step": session.get("step_index"), "reply": reply}

    # 🏁 Última respuesta: enviar a Make y notificar
    report_payload = {"process": session['process'], "answers": session['answers']}
    try:
        response = requests.post(WEBHOOK_URL, json=report_payload)
        print(f"📤 Informe enviado a Make: {response.status_code}")
    except Exception as e:
        print(f"❌ Error enviando a Make: {e}")

    # Notificar completado al admin
    update_alert_status(numero, "completed_alert_sent")
    admin_phone = get_admin_phone()
    if admin_phone:
        try:
            nombre = get_user_name_by_phone(numero)
            send_whatsapp_message(
                admin_phone,
                f"✅ *Informe completado:*\nEl supervisor *{nombre}* ({numero}) completó su informe diario."
            )
        except Exception as e:
            print(f"❌ Error notificando al admin: {e}")

    # Guardar sesión final (no borrar archivo si deseas conservarlo)
    sessions[numero] = session
    try:
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"❌ Error guardando sesión final: {e}")

    # Agradecimiento al supervisor
    reply = "✅ Gracias. Toda la información ha sido registrada y enviada a gerencia. 🙌"
    try:
        send_whatsapp_message(numero, reply)
    except Exception as e:
        print(f"❌ Error enviando confirmación final a {numero}: {e}")

    return {"status": "ok", "detail": "done"}
