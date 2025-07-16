import json
import os
import unicodedata
from datetime import datetime
from services.openai_service import get_openai_client
from services.whatsapp_service import send_whatsapp_message

# 📂 Definir rutas base
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
TASKS_DIR = os.path.join(CONFIG_DIR, "tasks")
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")

# 🔧 Función para normalizar textos (sin tildes, minúsculas)
def normalizar(texto):
    return unicodedata.normalize('NFD', texto).encode('ascii', 'ignore').decode('utf-8').lower()

def send_daily_tasks():
    print("🚦 Enviando tareas diarias motivadoras...")

    # ✅ Conexión a OpenAI
    client = get_openai_client()

    # 📅 Día de la semana en inglés ➜ traducir a español
    dias_ingles_esp = {
        "Monday": "Lunes",
        "Tuesday": "Martes",
        "Wednesday": "Miércoles",
        "Thursday": "Jueves",
        "Friday": "Viernes",
        "Saturday": "Sábado",
        "Sunday": "Domingo"
    }

    today_en = datetime.today().strftime("%A")  # Ejemplo: 'Saturday'
    today = dias_ingles_esp[today_en]          # Traducido a 'Sábado'
    today_normalizado = normalizar(today)      # 'sabado'
    print(f"📅 Hoy es: {today_en} ➜ {today} ➜ Normalizado: {today_normalizado}")

    # 📂 Leer usuarios
    with open(USERS_FILE) as f:
        data = json.load(f)
        users = data["users"]

    for supervisor in users:
        name = supervisor["name"]
        phone = supervisor["phone"]
        process = supervisor["process"]

        filename = f"{name.lower().replace(' ', '_')}.json"
        task_file = os.path.join(TASKS_DIR, filename)
        print(f"🔍 Buscando archivo: {task_file}")

        if not os.path.exists(task_file):
            print(f"⚠️ Archivo NO encontrado: {task_file}")
            continue

        with open(task_file) as f:
            tasks_data = json.load(f)

        today_tasks = []
        for task in tasks_data["daily_tasks"]:
            dias_normalizados = [normalizar(dia) for dia in task["dias"]]
            print(f"🧪 Tarea: {task['actividad']} ➜ Días normalizados: {dias_normalizados}")
            if today_normalizado in dias_normalizados:
                today_tasks.append(task)

        if not today_tasks:
            print(f"⏭️ No hay tareas para hoy para {name}")
            continue

        # ✏️ Construir prompt para OpenAI
        task_lines = "\n".join([f"- {task['actividad']} (⏰ {task['hora']})" for task in today_tasks])
        prompt = (
            f"Redacta un mensaje breve, motivador y profesional para {name} "
            f"que incluya:\n\n"
            f"👋 Un saludo cordial.\n"
            f"📋 Las tareas del día:\n{task_lines}\n\n"
            f"💪 Un cierre motivador deseándole un excelente día de trabajo.\n"
            f"Usa emojis apropiados de motivación y trabajo."
        )

        # ✅ Generar mensaje con OpenAI
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un asistente de producción motivador, breve y claro."},
                {"role": "user", "content": prompt}
            ]
        )

        message = completion.choices[0].message.content.strip()

        # ✅ Mostrar y enviar
        print(f"\n📋 Mensaje generado para {name}:\n{message}\n")
        send_whatsapp_message(phone, message)

if __name__ == "__main__":
    send_daily_tasks()
