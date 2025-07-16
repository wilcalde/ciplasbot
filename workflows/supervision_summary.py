import os
import json
from datetime import datetime
from openai import OpenAI
import requests
from services.whatsapp_service import send_whatsapp_message

# 📁 Rutas base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESPONSES_DIR = os.path.join(BASE_DIR, "../config/supervision_responses")
CONFIG_DIR = os.path.join(BASE_DIR, "../config")
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")

# 🌐 Webhook Make
WEBHOOK_URL = "https://hook.us2.make.com/k2vr3eevsu1sc1l60lkqtnmdegdemfpa"

# 🧠 Cliente OpenAI
client = OpenAI()

def get_admin_data():
    with open(USERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
        for user in data.get("users", []):
            if user.get("role", "").lower() == "administrador":
                return {
                    "name": user.get("name"),
                    "phone": user.get("phone")
                }
    raise ValueError("⚠️ No se encontró usuario administrador en users.json")

def supervision_summary():
    """
    📋 Consolida respuestas de supervisores y envía resumen diario al administrador.
    """
    today = datetime.now().strftime("%Y%m%d")
    raw_blocks = []

    if not os.path.exists(RESPONSES_DIR):
        print(f"⚠️ Carpeta de respuestas no encontrada: {RESPONSES_DIR}")
        return

    for file in os.listdir(RESPONSES_DIR):
        if file.endswith(".json") and today in file:
            path = os.path.join(RESPONSES_DIR, file)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                supervisor = data.get("supervisor")
                answers = data.get("answers", {})
                fecha = data.get("fecha", "")

                lines = [f"Supervisor: {supervisor} | Fecha: {fecha}"]
                for pregunta, respuesta in answers.items():
                    lines.append(f"- {pregunta}:\n  {respuesta}")
                raw_blocks.append("\n".join(lines))

    admin = get_admin_data()

    if not raw_blocks:
        send_whatsapp_message(
            admin["phone"],
            f"⚠️ {admin['name']}, no se recibió ningún informe de supervisión hoy a las 2:30 p. m."
        )
        print("⚠️ No se encontraron informes ➜ Admin notificado.")
        return

    # ✨ Redactar email con OpenAI
    draft_body = "\n\n---\n\n".join(raw_blocks)
    system_prompt = """
Eres un redactor profesional. Recibes informes diarios de supervisión.
Redacta un resumen ejecutivo en HTML incluyendo lo siguiente:
- Inicia con <p>Cordial saludo,</p>
- Explica que se consolidan las observaciones realizadas por los supervisores en su rutina de supervisión diaria.
- Para cada supervisor: <h3>🔹 Supervisor: [Nombre]</h3>
  - Listar puntos destacados con <ul><li>...</li></ul>
- Cierra con: <p>Quedamos atentos a cualquier observación.<br>Atentamente,<br>CIPLASBOT - Asistente I.A</p>
- Mejora redacción y ortografía.
- Usa emojis y títulos claros.
"""

    response = client.chat.completions.create(
        model="o4-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": draft_body}
        ]
    )

    final_body = response.choices[0].message.content.strip()

    # 📧 Mostrar resumen
    print("\n====== EMAIL RESUMEN SUPERVISIÓN ======\n")
    print(final_body)
    print("\n========================================\n")

    payload = {
        "subject": f"Informe Supervisión Diaria – {datetime.now().strftime('%Y-%m-%d')}",
        "body": final_body
    }

    try:
        res = requests.post(WEBHOOK_URL, json=payload)
        print(f"📤 Email enviado: {res.status_code}")

        if res.status_code == 200:
            send_whatsapp_message(
                admin["phone"],
                f"✅ {admin['name']}, el informe de supervisión fue enviado correctamente. 📧"
            )
            print("✅ Confirmación enviada al administrador.")

            # 🧹 Eliminar archivos procesados
            deleted = 0
            for file in os.listdir(RESPONSES_DIR):
                if file.endswith(".json") and today in file:
                    os.remove(os.path.join(RESPONSES_DIR, file))
                    deleted += 1
            print(f"🗑️ {deleted} archivos de respuestas eliminados.")

    except Exception as e:
        print(f"❌ Error al enviar resumen: {e}")
