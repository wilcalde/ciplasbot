from fastapi import FastAPI
from pydantic import BaseModel
from services.prompts import get_prompt, get_flow
from services.whatsapp_service import send_whatsapp_message
from services.session_memory import sessions
import json
import os
import requests
import datetime
from openai import OpenAI

from workflows.supervision_questions import handle_response, load_supervision_session_if_exists
from workflows.daily_report import update_alert_status, check_alert_already_sent, get_admin_phone

app = FastAPI()

# 📂 Directorios base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
HISTORY_DIR = os.path.join(CONFIG_DIR, "history")
TASKS_DIR = os.path.join(BASE_DIR, "tasks")
os.makedirs(HISTORY_DIR, exist_ok=True)

WEBHOOK_URL = "https://hook.make.com/tu_webhook"  # 👉 Reemplaza con tu URL real

# 🧠 Cliente OpenAI
client = OpenAI()

def respuesta_inteligente(texto, nombre, numero):
    if texto.lower() == "tareas" or "tareas del día" in texto.lower():
        archivo_tareas = os.path.join(TASKS_DIR, f"{nombre.lower()}.json")
        if os.path.exists(archivo_tareas):
            with open(archivo_tareas, encoding="utf-8") as f:
                tareas = json.load(f)
                hoy = datetime.datetime.now().strftime("%Y-%m-%d")
                lista = tareas.get(hoy)
                if lista:
                    mensaje = f"📋 *Tareas asignadas para hoy:*\n" + "\n".join([f"✅ {t}" for t in lista])
                else:
                    mensaje = "📭 Hoy no tienes tareas asignadas."
        else:
            mensaje = "⚠️ No encontré un archivo de tareas para ti."
        return mensaje

    prompt_sistema = f"""
Eres un asistente virtual llamado CiplasBot. Tu tarea es responder con amabilidad y precisión al usuario {nombre}, quien trabaja en la planta de producción de Ciplas S.A.S. 
No tienes flujo activo en este momento, pero puedes responder preguntas generales sobre procesos, producción, dudas comunes o instrucciones simples.

Si no sabes la respuesta, responde con:
'Lo siento, por ahora no tengo información sobre ese tema. Puedes escribir "ayuda" para ver opciones disponibles.'
"""
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
    message: str

@app.post("/ciplasbot")
async def handle_ciplasbot(payload: WhatsAppMessage):
    numero = payload.phone
    texto = payload.message.strip()

    # ✅ Cargar sesión de supervisión desde disco si no está en memoria
    if numero not in sessions:
        load_supervision_session_if_exists(numero)

    # 🚩 1️⃣ Si el número tiene sesión activa tipo supervisión
    if numero in sessions and sessions[numero].get("process") == "SUPERVISION":
        handle_response(numero, texto)
        return {"status": "ok", "detail": "handled by supervision_questions"}

    # 🚩 2️⃣ Flujo tradicional basado en archivo JSON por sesión
    session_file = os.path.join(CONFIG_DIR, f"{numero}_session.json")
    session = sessions.get(numero)

    if not session:
        if os.path.exists(session_file):
            with open(session_file, encoding="utf-8") as f:
                session = json.load(f)
            sessions[numero] = session  # 💾 Cargar en memoria
        else:
            # 🧠 RESPUESTA INTELIGENTE SIN FLUJO ACTIVO
            user_file = os.path.join(CONFIG_DIR, "users.json")
            nombre = "usuario"
            if os.path.exists(user_file):
                with open(user_file, encoding="utf-8") as f:
                    users_data = json.load(f)
                    for user in users_data.get("users", []):
                        if user.get("phone") == numero:
                            nombre = user.get("name", "usuario")
                            break

            respuesta = respuesta_inteligente(texto, nombre, numero)
            send_whatsapp_message(numero, respuesta)
            return {"status": "no_flow", "reply": respuesta}

    flow = session.get("flow", [])

    # 🛡 Validar que el índice esté dentro del rango del flujo
    if not flow or session["step_index"] >= len(flow):
        reply = "✅ Ya completaste todas las preguntas. Si deseas empezar de nuevo, escribe /start o espera el próximo cuestionario."
        send_whatsapp_message(numero, reply)
        sessions.pop(numero, None)
        if os.path.exists(session_file):
            os.remove(session_file)
        return {"status": "done", "detail": "flow completed or index out of range"}

    current_step = flow[session["step_index"]]

    # ✅ 3️⃣ Detectar comando EDITAR
    if texto.strip().upper() == "EDITAR":
        lines = []
        for idx, step in enumerate(flow):
            respuesta = session["answers"].get(step, "❌ Sin respuesta")
            lines.append(f"{idx+1}️⃣ {step}: {respuesta}")
        listado = "\n".join(lines)
        reply = f"📋 *Tus respuestas actuales:*\n{listado}\n\n👉 Escribe el número de la pregunta que deseas corregir."
        session["editing"] = True
        sessions[numero] = session
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)
        send_whatsapp_message(numero, reply)
        return {"status": "ok", "mode": "editing", "reply": reply}

    # ✅ 4️⃣ Si está en modo edición
    if session.get("editing"):
        try:
            selected = int(texto.strip()) - 1
            if selected < 0 or selected >= len(flow):
                reply = "⚠️ Número inválido. Escribe un número válido de la lista."
            else:
                session["step_index"] = selected
                step = flow[selected]
                if step in session["answers"]:
                    session["answers"].pop(step)
                reply = f"✏️ Corrige por favor: {get_prompt(step, session['process'])}"
                session.pop("editing", None)
        except ValueError:
            reply = "⚠️ Por favor, escribe solo el número de la pregunta a corregir."

        sessions[numero] = session
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)
        send_whatsapp_message(numero, reply)
        return {"status": "ok", "mode": "editing", "reply": reply}

    # ✅ 5️⃣ Última pregunta o paso normal
    session["answers"][current_step] = texto
    session["step_index"] += 1

    if session["step_index"] < len(flow):
        next_step = flow[session["step_index"]]
        reply = get_prompt(next_step, session["process"])
    else:
        # 📨 Enviar informe final
        report_payload = {"process": session["process"], "answers": session["answers"]}
        try:
            response = requests.post(WEBHOOK_URL, json=report_payload)
            print(f"📤 Informe enviado a Make: {response.status_code}")
        except Exception as e:
            print(f"❌ Error enviando a Make: {e}")

        # 💾 Guardar historial
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        history_file = os.path.join(HISTORY_DIR, f"{numero}_history_{timestamp}.json")
        with open(history_file, "w", encoding="utf-8") as f:
            json.dump(report_payload, f, indent=2, ensure_ascii=False)

        # ✅ Alerta de informe completado
        update_alert_status(numero, "completed_alert_sent")
        admin_phone = get_admin_phone()
        if admin_phone:
            admin_msg = f"✅ *Informe completado:*\nEl supervisor *{numero}* ya completó su informe diario."
            send_whatsapp_message(admin_phone, admin_msg)

        # 🧹 Limpiar sesión
        sessions.pop(numero, None)
        if os.path.exists(session_file):
            os.remove(session_file)

        reply = "✅ Gracias. Toda la información ha sido registrada y enviada a gerencia. 🙌"
        send_whatsapp_message(numero, reply)
        return {"status": "ok", "detail": "done"}

    # 💾 Guardar progreso intermedio
    sessions[numero] = session
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2, ensure_ascii=False)
    send_whatsapp_message(numero, reply)
    return {"status": "ok", "step": session.get("step_index"), "reply": reply}
