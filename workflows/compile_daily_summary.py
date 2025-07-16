import os
import json
from datetime import datetime
from openai import OpenAI
import requests

from services.whatsapp_service import send_whatsapp_message

# 📂 Rutas base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "../config")
HISTORY_DIR = os.path.join(CONFIG_DIR, "history")
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
                return {"name": user["name"], "phone": user["phone"]}
    raise ValueError("⚠️ No se encontró usuario administrador en users.json")

def compile_daily_summary():
    today = datetime.now().strftime("%Y%m%d")
    raw_blocks = []

    # ✅ Busca sesiones vivas en CONFIG_DIR
    for file in os.listdir(CONFIG_DIR):
        if file.endswith("_session.json"):
            path = os.path.join(CONFIG_DIR, file)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                process = data.get("process")
                supervisor = data.get("supervisor", "Desconocido")
                answers = data.get("answers", {})
                fecha = data.get("fecha_hora", today)

                lines = [f"Proceso: {process} | Supervisor: {supervisor} | Fecha: {fecha}"]
                for pregunta, respuesta in answers.items():
                    lines.append(f"- {pregunta}:\n  {respuesta}")

                raw_blocks.append("\n".join(lines))

    admin = get_admin_data()

    if not raw_blocks:
        send_whatsapp_message(
            admin["phone"],
            f"⚠️ {admin['name']}, no se recibió información de ningún supervisor hoy. El informe NO se enviará."
        )
        print("⚠️ Sin bloques ➜ Administrador notificado.")
        return

    draft_body = "\n\n---\n\n".join(raw_blocks)

    system_prompt = f"""
Eres un redactor corporativo experto. Recibes datos crudos de supervisores.
Devuelve un solo EMAIL en HTML profesional:
- Comienza: <p>Cordial saludo,</p>
- Sigue: <p>Estas son las novedades y estado de los procesos del área de conversión del día [FECHA]:</p>
- Para cada proceso: <h3>🔹 Proceso: [Nombre]</h3>
- Dentro de cada bloque: <ul><li>...</li></ul> ➜ personal ausente, máquinas, paros, inventario, novedades importantes.
- Usa emojis y subtítulos claros.
- Corrige forma y ortografía.
- Finaliza: <p>Quedamos atentos a cualquier observación.<br>Atentamente,<br>CIPLASBOT - Asistente I.A proceso conversión</p>
"""

    response = client.chat.completions.create(
        model="o4-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": draft_body}
        ]
    )

    final_body = response.choices[0].message.content.strip()

    print("\n==============================")
    print("📧 📋 EMAIL FINAL PARA GERENCIA")
    print("==============================\n")
    print(final_body)
    print("\n==============================\n")

    payload = {
        "subject": f"Informe de Novedades Conversión – {datetime.now().strftime('%Y-%m-%d')}",
        "body": final_body
    }

    try:
        res = requests.post(WEBHOOK_URL, json=payload)
        print(f"📤 Correo enviado a Make: {res.status_code}")

        if res.status_code == 200:
            send_whatsapp_message(
                admin["phone"],
                f"✅ {admin['name']}, el informe diario de producción fue enviado correctamente como borrador,revisalo antes de enviar. 📧"
            )
            print(f"✅ Confirmación enviada a {admin['name']}.")

            # ✅ Backup y Borrar sesiones vivas de CONFIG
            deleted = 0
            os.makedirs(HISTORY_DIR, exist_ok=True)

            for file in os.listdir(CONFIG_DIR):
                if file.endswith("_session.json"):
                    session_path = os.path.join(CONFIG_DIR, file)

                    # Backup
                    with open(session_path, encoding="utf-8") as f:
                        session_data = json.load(f)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_name = f"{file.replace('_session.json', '')}_history_{timestamp}.json"
                    backup_path = os.path.join(HISTORY_DIR, backup_name)

                    with open(backup_path, "w", encoding="utf-8") as f:
                        json.dump(session_data, f, indent=2, ensure_ascii=False)
                    print(f"📦 Backup guardado: {backup_path}")

                    # Borrar sesión original
                    os.remove(session_path)
                    deleted += 1

            print(f"🗑️ {deleted} sesiones activas respaldadas y eliminadas de CONFIG.")

            # ✅ Eliminar alert_log.json tras envío exitoso
            alert_log_path = os.path.join(CONFIG_DIR, "alert_log.json")
            if os.path.exists(alert_log_path):
                os.remove(alert_log_path)
                print("🧹 Archivo alert_log.json eliminado correctamente.")

    except Exception as e:
        print(f"❌ Error enviando a Make: {e}")

if __name__ == "__main__":
    compile_daily_summary()
