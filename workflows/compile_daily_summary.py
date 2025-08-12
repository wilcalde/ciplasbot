import os
import json
from datetime import datetime
from openai import OpenAI
import requests

from services.whatsapp_service import send_whatsapp_message
from services.session_memory import CONFIG_DIR, SUPERVISORS_FILE, ALERT_LOG_FILE

# 🌐 Webhook Make
WEBHOOK_URL = "https://hook.us2.make.com/k2vr3eevsu1sc1l60lkqtnmdegdemfpa"

# 🧠 Cliente OpenAI
client = OpenAI()


def get_admin_data():
    """
    Lee users.json y retorna nombre y teléfono del administrador.
    """
    with open(SUPERVISORS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for user in data.get("users", []):
        if user.get("role", "").lower() == "administrador":
            return {"name": user["name"], "phone": user["phone"]}
    raise ValueError("⚠️ No se encontró usuario administrador en users.json")


def compile_daily_summary():
    """
    Compila los reportes JSON de varios supervisores, genera un email resumen con OpenAI,
    envía a Make y notifica por WhatsApp.
    """
    # 1) Listar archivos de sesión
    try:
        session_files = [f for f in os.listdir(CONFIG_DIR) if f.endswith("_session.json")]
    except FileNotFoundError:
        session_files = []
    print("🗂️ Sesiones encontradas:", session_files)

    reports = []
    for filename in session_files:
        path = os.path.join(CONFIG_DIR, filename)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            reports.append(data)
        except Exception as e:
            print(f"❌ Error leyendo {filename}: {e}")

    admin = get_admin_data()

    # Si no hay reportes, avisar y salir
    if not reports:
        send_whatsapp_message(
            admin['phone'],
            f"⚠️ {admin['name']}, no se recibió información de ningún supervisor hoy. El informe NO se enviará."
        )
        return

    # 2) Preparar prompt para OpenAI
    system_prompt = (
        "Eres un asistente que genera un email en HTML para el equipo de producción. "
        "Debes detallar los reportes de cada supervisor, agrupados por proceso. "
        "Para cada proceso, incluye: supervisor, hora de registro, y listas con las novedades: personal ausente, operando, inventario, paradas, y notas generales. "
        "Utiliza etiquetas HTML (<h3>, <h4>, <ul>, <li>, <p>) y emojis para cada sección. "
        "Al final, agrega un pie de página con agradecimiento y la firma 'Agente IA CiplasBot 🤖'."
    )
    user_prompt = (
        "Genera el cuerpo en HTML de un email de resumen diario de novedades basado en la siguiente lista de reportes (formato JSON):\n"
        f"{json.dumps(reports, ensure_ascii=False, indent=2)}"
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        final_body = response.choices[0].message.content
    except Exception as e:
        print(f"❌ Error generando el resumen con OpenAI: {e}")
        # Fallback: cuerpo simple
        date_str = datetime.now().strftime('%d/%m/%Y')
        final_body = (
            f"<p>👋 <strong>¡Buen día, equipo!</strong></p>"
            f"<p>📅 Actualización del área de conversión al <b>{date_str}</b>:</p>"
            "<p>No fue posible generar el resumen automáticamente.</p>"
            "<p>Atentamente,<br>Agente IA CiplasBot 🤖</p>"
        )

    # 3) Subject y envío
    subject = f"Informe de Novedades Conversión – {datetime.now().strftime('%d/%m/%Y')}"

    try:
        # Enviar a Make
        requests.post(WEBHOOK_URL, json={'subject': subject, 'body': final_body})
        # Notificar por WhatsApp
        send_whatsapp_message(
            admin['phone'],
            f"✅ {admin['name']}, el informe diario de novedades fue enviado correctamente."
        )
    except Exception as e:
        print(f"❌ Error enviando informe: {e}")

    # 4) Limpieza de archivos de sesión y alertas
    for fn in session_files:
        try:
            os.remove(os.path.join(CONFIG_DIR, fn))
        except:
            pass
    if os.path.exists(ALERT_LOG_FILE):
        os.remove(ALERT_LOG_FILE)


if __name__ == "__main__":
    compile_daily_summary()
