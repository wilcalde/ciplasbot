#whatsapp_service.py
import os
import requests
from dotenv import load_dotenv

# Carga variables del .env
load_dotenv()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_ID = os.getenv("PHONE_ID")

def send_whatsapp_message(to, message):
    """
    Envía un mensaje de texto simple usando WhatsApp Business Cloud API.
    """
    url = f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": message
        }
    }

    print(f"📤 [WA] Enviando a: {to} | Mensaje: {message}")

    try:
        response = requests.post(url, headers=headers, json=payload)

        # 📌 Imprime TODO el resultado real de la API
        print(f"✅ [WA] Response status: {response.status_code}")
        print(f"✅ [WA] Response body: {response.text}")

        # ⚠️ Forzar error si falla
        response.raise_for_status()

    except requests.RequestException as e:
        print(f"❌ [WA] Error enviando WhatsApp: {e}")
