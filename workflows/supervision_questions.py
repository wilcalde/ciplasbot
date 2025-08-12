# workflows/supervision_questions.py
import os
import json
from datetime import datetime
from services.whatsapp_service import send_whatsapp_message
import services.session_memory as memory  # 🧠 Diccionario compartido

# 📁 Rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "../config")
RESPONSES_DIR = os.path.join(CONFIG_DIR, "supervision_responses")
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")

# 📝 Preguntas del formulario de supervisión (10 en total)
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

# 🔧 Crear carpetas si no existen
os.makedirs(RESPONSES_DIR, exist_ok=True)

# ——————————————————————————————————————————————
# Helpers
# ——————————————————————————————————————————————
def _normalize_phone(phone: str) -> str:
    """Deja solo dígitos (ej: '+57 317...' -> '57317...')."""
    return "".join(ch for ch in str(phone) if ch.isdigit())

def _session_filename(phone: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return os.path.join(
        RESPONSES_DIR, f"{_normalize_phone(phone)}_{when.strftime('%Y%m%d')}.json"
    )

def _write_session_to_disk(phone: str, session: dict) -> None:
    fname = _session_filename(phone)
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)

def _load_session_from_disk(phone: str) -> dict | None:
    """
    Intenta cargar usando el teléfono normalizado y, si no, tal cual viene.
    Devuelve el dict de sesión o None.
    """
    today = datetime.now().strftime("%Y%m%d")
    candidates = [
        os.path.join(RESPONSES_DIR, f"{_normalize_phone(phone)}_{today}.json"),
        os.path.join(RESPONSES_DIR, f"{phone}_{today}.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    print(f"📥 Sesión cargada: {path}")
                    return data
                except Exception as e:
                    print(f"❌ Error leyendo sesión {path}: {e}")
    return None

def _sanitize_loaded_session(session: dict) -> dict:
    """
    Arregla incoherencias: flow vacío o índice fuera de rango.
    """
    flow = session.get("flow") or []
    idx = session.get("step_index", 0)

    # Si el flow está vacío o no es lista, re-inicializamos
    if not isinstance(flow, list) or len(flow) == 0:
        print("⚠️ Flow vacío o inválido. Reinicializando flujo y step_index.")
        session["flow"] = QUESTIONS[:]
        session["step_index"] = 0
        session.setdefault("answers", {})
        session.setdefault("process", "SUPERVISION")
        return session

    # Si el índice se fue de rango, reiniciamos a 0 (primera pregunta)
    if not isinstance(idx, int) or idx < 0 or idx >= len(flow):
        print("⚠️ step_index incoherente. Reinicializando a 0.")
        session["step_index"] = 0

    session.setdefault("answers", {})
    session.setdefault("process", "SUPERVISION")
    return session

# ——————————————————————————————————————————————
# API principal
# ——————————————————————————————————————————————
def ask_supervision_questions(phone: str, name: str):
    """
    Inicia en memoria y en disco la sesión de supervisión,
    y envía la primera pregunta al supervisor.
    """
    phone_norm = _normalize_phone(phone)
    session_data = {
        "flow": QUESTIONS[:],
        "step_index": 0,
        "answers": {},
        "process": "SUPERVISION",
        "phone": phone_norm,
        "created_at": datetime.now().isoformat()
    }

    # Guardar en memoria y disco
    memory.sessions[phone_norm] = session_data
    _write_session_to_disk(phone_norm, session_data)

    # Primera pregunta
    send_whatsapp_message(
        phone,
        (
            f"📝 Hola *{name}*, vamos a diligenciar el informe de rutina de supervisión del día.\n\n"
            f"{QUESTIONS[0]}"
        )
    )
    print(f"🚀 Sesión iniciada para {phone_norm}")

def send_supervision_questions():
    """
    Envía la invitación al cuestionario a todos los usuarios con rol 'supervisor'.
    """
    if not os.path.exists(USERS_FILE):
        print(f"❌ Archivo users.json no encontrado en: {USERS_FILE}")
        return

    with open(USERS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    for user in data.get("users", []):
        if user.get("role", "").lower() == "supervisor":
            phone = user.get("phone")
            name = user.get("name")
            if phone and name:
                ask_supervision_questions(phone, name)

def load_supervision_session_if_exists(phone: str) -> bool:
    """
    Si existe un archivo de sesión para hoy, lo carga en memoria y devuelve True.
    En caso contrario, False.
    """
    phone_norm = _normalize_phone(phone)
    ses = _load_session_from_disk(phone_norm)
    if ses is None:
        # Probar con el no normalizado por si el archivo previo se creó así
        ses = _load_session_from_disk(phone)

    if ses is not None:
        ses = _sanitize_loaded_session(ses)
        # Mantener clave de memoria con número normalizado
        memory.sessions[phone_norm] = ses
        print(f"📦 Sesión activa en memoria para {phone_norm} con step_index={ses['step_index']}")
        return True

    return False

def handle_response(phone: str, message: str):
    """
    Procesa cada respuesta del supervisor, avanza en el flujo
    y guarda progreso en disco y memoria.
    """
    phone_norm = _normalize_phone(phone)

    # 1) Si no está en memoria, intenta cargar desde disco
    if phone_norm not in memory.sessions:
        if not load_supervision_session_if_exists(phone):
            send_whatsapp_message(
                phone,
                "⚠️ No encuentro una sesión activa. Por favor escribe */start* para iniciar el informe de supervisión."
            )
            print(f"ℹ️ No había sesión en memoria ni en disco para {phone_norm}.")
            return

    # 2) Sesión en memoria (y sanitizada)
    session = _sanitize_loaded_session(memory.sessions.get(phone_norm, {}))
    flow = session["flow"]
    idx = session["step_index"]

    # 3) Caso especial: si el usuario responde con algo tipo /start, ok, listo… y estamos en idx 0,
    #    volvemos a pedir la primera pregunta en vez de registrar eso como respuesta.
    first_like = {"/start", "start", "ok", "listo", "hola", "buenas", "buen día", "buen dia"}
    if idx == 0 and message.strip().lower() in first_like:
        send_whatsapp_message(phone, flow[0])
        print(f"🔁 Repetida primera pregunta a {phone_norm} (mensaje inicial no se guardó como respuesta).")
        return

    # 4) Si aún quedan preguntas por responder
    if idx < len(flow):
        current_q = flow[idx]
        session["answers"][current_q] = message.strip()
        session["step_index"] = idx + 1

        # Persistir
        memory.sessions[phone_norm] = session
        _write_session_to_disk(phone_norm, session)

        # Siguiente pregunta o finalización
        if session["step_index"] < len(flow):
            next_q = flow[session["step_index"]]
            send_whatsapp_message(phone, next_q)
            print(f"➡️ Enviada pregunta {session['step_index']+1}/{len(flow)} a {phone_norm}")
        else:
            send_whatsapp_message(
                phone,
                "✅ ¡Gracias! El informe fue registrado correctamente. 📨"
            )
            print(f"🏁 Cuestionario completado por {phone_norm}")
            # Limpiar memoria (el archivo queda en disco para el compilador)
            if phone_norm in memory.sessions:
                del memory.sessions[phone_norm]
        return

    # 5) Si por alguna razón llegamos aquí (idx >= len(flow)), forzamos reinicio seguro
    print(f"⚠️ idx={idx} >= len(flow)={len(flow)} para {phone_norm}. Reinicializando sesión.")
    session["flow"] = QUESTIONS[:]
    session["step_index"] = 0
    memory.sessions[phone_norm] = session
    _write_session_to_disk(phone_norm, session)
    send_whatsapp_message(
        phone,
        "🔄 Reinicié tu cuestionario por una inconsistencia. Empecemos de nuevo:\n\n" + QUESTIONS[0]
    )
