import os
from openai import OpenAI
from dotenv import load_dotenv

# 📌 Cargar variables de entorno desde .env
load_dotenv()

# 📌 Inicializar cliente OpenAI
def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("❌ ERROR: No se encontró OPENAI_API_KEY en el entorno.")
    return OpenAI(api_key=api_key)


# ✔️ Función de ejemplo para redactar un texto usando o4-mini
def create_completion_o4(prompt: str) -> str:
    client = get_openai_client()
    response = client.chat.completions.create(
        model="o4-mini",
        messages=[
            {"role": "system", "content": "Eres un asistente profesional y redactas mensajes claros y motivadores."},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()

