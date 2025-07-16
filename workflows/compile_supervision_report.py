import os
import json
import requests
from datetime import datetime
from openai import OpenAI

# 📍 Ruta al directorio correcto de respuestas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
#RESPONSES_DIR = os.path.join(BASE_DIR, "../supervision_responses")  # Carpeta raíz
RESPONSES_DIR = os.path.join(BASE_DIR, "../config/supervision_responses")


# 🌐 Webhook de Make
MAKE_WEBHOOK_URL = "https://hook.us2.make.com/hmlnjg64d7360ly5b90d337a6cv678kn"

# 🧠 Cliente OpenAI
client = OpenAI()

# 🔧 Prompt para análisis
system_prompt = """
Eres un redactor corporativo experto. Recibes respuestas de supervisores de planta y debes redactar un informe profesional en HTML dirigido al Ing. Wilson Calderón, jefe de planta.

✅ Instrucciones:
1. Redacta en formato HTML bien estructurado. Usa <h2>, <ul>, <li>, <p> y negritas <strong>.
2. Usa un tono profesional pero ameno, con algunos emojis como 📌, ⚠️, 🔧, 📦 para hacerlo más visual.
3. Incluye tres secciones claras:
   <h2>📌 Resumen ejecutivo de novedades</h2>
   <h2>⚠️ Temas críticos identificados</h2>
   <h2>🚀 Líneas de acción propuestas</h2>
4. En cada sección, usa <ul><li> para listar cada punto claramente.
5. Incluye nombres de operarios si se mencionan (ej. “el operario Juan Nova…”).
6. Cierra con un párrafo: 
   <p>Quedo atento a sus comentarios y decisiones respecto a las acciones propuestas.</p>
7. Firma con: 
   <p>Atentamente,<br><strong>Agente IA CiplasBot</strong></p>
"""

def compile_supervision_report():
    print("📋 Compilando informe de supervisión...")

    if not os.path.exists(RESPONSES_DIR):
        print(f"⚠️ Carpeta de respuestas no encontrada: {RESPONSES_DIR}")
        return

    print(f"📁 Archivos encontrados en {RESPONSES_DIR}:")
    bloques = []

    for filename in os.listdir(RESPONSES_DIR):
        if filename.endswith(".json"):
            filepath = os.path.join(RESPONSES_DIR, filename)
            print(" -", filename)

            with open(filepath, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    print("   🔑 Claves del JSON:", list(data.keys()))
                except Exception as e:
                    print(f"   ❌ Error leyendo {filename}: {e}")
                    continue

                respuestas = data.get("respuestas") or data.get("answers") or {}
                if not respuestas:
                    print(f"   ⚠️ No se encontraron respuestas válidas en {filename}")
                    continue

                bloque = "\n".join([f"{k}: {v}" for k, v in respuestas.items()])
                bloques.append(bloque)

    if not bloques:
        print("⚠️ No se encontraron respuestas de supervisores.")
        return

    draft = "\n\n---\n\n".join(bloques)

    print("🧠 Enviando respuestas al modelo OpenAI para análisis...")
    try:
        response = client.chat.completions.create(
            model="o4-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": draft}
            ]
        )
        final_body = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"❌ No se pudo generar el análisis del informe: {e}")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"📋 Informe Diario Supervisión - {today}"

    payload = {
        "subject": subject,
        "body": final_body
    }

    try:
        res = requests.post(MAKE_WEBHOOK_URL, json=payload)
        if res.status_code == 200:
            print("📤 Informe enviado correctamente al administrador.")
        else:
            print(f"❌ Error al enviar email (status {res.status_code}): {res.text}")
    except Exception as e:
        print(f"❌ Error al enviar al webhook de Make: {e}")
        return

    # ✅ Eliminar archivos procesados
    deleted = 0
    for filename in os.listdir(RESPONSES_DIR):
        if filename.endswith(".json"):
            os.remove(os.path.join(RESPONSES_DIR, filename))
            deleted += 1
    print(f"🧹 {deleted} archivos temporales de supervisión eliminados.")

if __name__ == "__main__":
    compile_supervision_report()
