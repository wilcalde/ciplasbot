import os
import json
from datetime import datetime
from services.whatsapp_service import send_whatsapp_message
import services.session_memory as memory  # 🧠 Diccionario compartido

# 📁 Rutas actualizadas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESPONSES_DIR = os.path.join(BASE_DIR, "../config/supervision_responses")
USERS_FILE = os.path.join(BASE_DIR, "../config/users.json")

# 📝 Preguntas del formulario de supervisión
QUESTIONS = [
    "1. Novedades con programación (dificultades, temas por mejorar o reportar)",
    "2. Producto no conforme (materias primas o productos internos)",
    "3. Atención y novedades con mantenimiento",
    "4. Inventario de suministros y materias primas",
    "5. Estado del inventario de etiquetas sin leer en su ubicación",
    "6. Novedades en puntos de control y autorizaciones",
    "7. Retroalimentación al personal (desempeño, disciplina, reconocimientos)",
    "8. Verificación de registros de máquinas (control de proceso, calidad, listas de chequeo)",
    "9. Orden, aseo y cumplimiento de BPF",
    "10. Métodos de trabajo o documentos por actualizar"
]

# 🔧 Crear la carpeta si no existe
os.makedirs(RESPONSES_DIR, exist_ok=True)

# ✅ Iniciar cuestionario para un supervisor
def ask_supervision_questions(phone, name):
    session_data = {
        "flow": QUESTIONS,
        "step_index": 0,
        "answers": {},
        "process": "SUPERVISION"
    }

    # Guardar en memoria
    memory.sessions[phone] = session_data

    # Guardar como archivo temporal de trabajo
    filename = os.path.join(RESPONSES_DIR, f"{phone}_{datetime.now().strftime('%Y%m%d')}.json")
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)

    # Enviar primera pregunta
    send_whatsapp_message(
        phone,
        f"📝 Hola *{name}*, vamos a diligenciar el informe de rutina de supervisión del día.\n\n{QUESTIONS[0]}"
    )

# ✅ Enviar preguntas a todos los supervisores registrados
def send_supervision_questions():
    if not os.path.exists(USERS_FILE):
        print("❌ Archivo users.json no encontrado.")
        return

    with open(USERS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    for user in data.get("users", []):
        if user.get("role", "").lower() == "supervisor":
            phone = user.get("phone")
            name = user.get("name")
            if phone and name:
                ask_supervision_questions(phone, name)

# ✅ Manejar respuesta de cada pregunta
def handle_response(phone, message):
    if phone not in memory.sessions:
        print(f"⚠️ No hay sesión activa para {phone}")
        return

    session = memory.sessions[phone]
    current_index = session["step_index"]
    flow = session["flow"]

    if current_index < len(flow):
        current_question = flow[current_index]
        session["answers"][current_question] = message.strip()
        session["step_index"] += 1

        # Actualizar archivo
        filename = os.path.join(RESPONSES_DIR, f"{phone}_{datetime.now().strftime('%Y%m%d')}.json")
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

        # Si hay más preguntas
        if session["step_index"] < len(flow):
            next_question = flow[session["step_index"]]
            send_whatsapp_message(phone, next_question)
        else:
            # Finaliza el cuestionario
            send_whatsapp_message(phone, "✅ ¡Gracias! El informe fue registrado correctamente. 📨")
            del memory.sessions[phone]

def load_supervision_session_if_exists(phone: str):
    """
    Carga una sesión previa guardada (si existe) y la carga en memoria.
    """
    today = datetime.now().strftime("%Y%m%d")
    folder = os.path.join("config", "supervision_responses")
    file_path = os.path.join(folder, f"{phone}_{today}.json")

    if os.path.exists(file_path):
        with open(file_path, encoding="utf-8") as f:
            memory.sessions[phone] = json.load(f)  # ✅ Carga en memoria
            print(f"📥 Sesión de supervisión cargada desde disco para {phone}")
            return True
    return False
