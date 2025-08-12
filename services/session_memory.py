# services/session_memory.py

import os
import json
import unicodedata
from datetime import datetime, date, time

# 📁 Rutas y carpetas necesarias para la gestión de sesiones y alertas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "../config")
SUPERVISORS_FILE = os.path.join(CONFIG_DIR, "users.json")
ALERT_LOG_FILE = os.path.join(CONFIG_DIR, "alert_log.json")

# 🗂️ Asegura que la carpeta config exista
os.makedirs(CONFIG_DIR, exist_ok=True)

# 📦 Diccionario global para mantener los flujos activos por número de teléfono
sessions = {}


def normalize(text: str) -> str:
    """
    🔤 Convierte texto a minúsculas sin acentos para comparación segura.
    """
    return unicodedata.normalize('NFKD', text).encode('ASCII', 'ignore').decode().lower()


def register_answer(phone: str, pregunta_key: str, respuesta: str) -> bool:
    """
    📥 Carga la sesión JSON, añade la respuesta, avanza el índice de paso y actualiza la marca de tiempo.

    Args:
        phone: Número de teléfono del supervisor.
        pregunta_key: Clave de la pregunta (identificador en el flujo).
        respuesta: Texto de la respuesta enviada.

    Returns:
        True si la sesión existía y se actualizó correctamente, False si no se encontró sesión.
    """
    session_file = os.path.join(CONFIG_DIR, f"{phone}_session.json")
    if not os.path.exists(session_file):
        return False

    try:
        with open(session_file, "r+", encoding="utf-8") as f:
            session = json.load(f)
            # Añadir respuesta y avanzar paso
            session.setdefault("answers", {})[pregunta_key] = respuesta
            session["step_index"] = session.get("step_index", 0) + 1
            # Actualizar timestamp para compilación de informe
            session["fecha_hora"] = datetime.now().isoformat()

            # Reescribir JSON completo
            f.seek(0)
            json.dump(session, f, indent=2, ensure_ascii=False)
            f.truncate()
        return True
    except Exception as e:
        print(f"❌ Error registrando respuesta en sesión {phone}: {e}")
        return False


def load_session(phone: str) -> dict | None:
    """
    📂 Lee y devuelve el contenido de la sesión JSON para un supervisor.
    """
    path = os.path.join(CONFIG_DIR, f"{phone}_session.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error leyendo sesión {phone}: {e}")
        return None


def save_session(phone: str, session: dict) -> bool:
    """
    💾 Guarda el diccionario de sesión actualizado en el archivo JSON.
    """
    path = os.path.join(CONFIG_DIR, f"{phone}_session.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"❌ Error guardando sesión {phone}: {e}")
        return False
