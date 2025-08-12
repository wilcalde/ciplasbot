# workflows/tasks_manager.py
import os
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from services.whatsapp_service import send_whatsapp_message

# Rutas base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "../config")
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")
TASKS_FILE = os.path.join(CONFIG_DIR, "tasks.json")

# Estado en memoria del flujo de creación de tareas
# clave: telefono_normalizado -> {"step": int, "draft": {...}}
TASK_SESSIONS: Dict[str, Dict[str, Any]] = {}

# ─────────────────────────────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in str(phone) if ch.isdigit())

def _load_users() -> List[dict]:
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("users", [])

def _is_admin(phone: str) -> bool:
    phone_norm = _normalize_phone(phone)
    for u in _load_users():
        up = _normalize_phone(u.get("phone", ""))
        role = (u.get("role") or "").strip().lower()
        if up == phone_norm and role == "administrador":
            return True
    return False

def _ensure_tasks_file():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump({"tasks": []}, f, ensure_ascii=False, indent=2)

def _load_tasks() -> List[dict]:
    _ensure_tasks_file()
    with open(TASKS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("tasks", [])

def _save_tasks(tasks: List[dict]):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump({"tasks": tasks}, f, ensure_ascii=False, indent=2)

def _parse_date_any(s: str) -> Optional[str]:
    """
    Acepta formatos comunes y devuelve ISO (YYYY-MM-DD) si es válida.
    Soporta: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, YYYY/MM/DD.
    """
    s = (s or "").strip()
    if not s:
        return None

    # YYYY-MM-DD o YYYY/MM/DD
    m = re.match(r"^\s*(\d{4})[/-](\d{2})[/-](\d{2})\s*$", s)
    if m:
        y, M, d = map(int, m.groups())
        try:
            return datetime(y, M, d).date().isoformat()
        except Exception:
            return None

    # DD/MM/YYYY o DD-MM-YYYY
    m = re.match(r"^\s*(\d{2})[/-](\d{2})[/-](\d{4})\s*$", s)
    if m:
        d, M, y = map(int, m.groups())
        try:
            return datetime(y, M, d).date().isoformat()
        except Exception:
            return None

    return None

def _new_task_id() -> str:
    # id simple basado en timestamp
    return datetime.now().strftime("T%Y%m%d%H%M%S%f")

def start_new_task_flow(phone: str):
    """
    Inicia el flujo de creación de tarea (solo administrador).
    """
    if not _is_admin(phone):
        send_whatsapp_message(phone, "⛔ Este comando es solo para el administrador.")
        return

    phone_norm = _normalize_phone(phone)
    TASK_SESSIONS[phone_norm] = {
        "step": 1,
        "draft": {
            "name": "",
            "due_date": "",
            "priority": "",
            "process": "",
            "status": "pendiente"
        }
    }
    send_whatsapp_message(
        phone,
        "📝 *Nueva tarea* — Paso 1/4\n\n1) *Nombre de la tarea*:\nEscribe un título corto y claro."
    )

def handle_task_flow_response(phone: str, message: str):
    """
    Maneja cada respuesta del flujo de creación de tarea.
    """
    phone_norm = _normalize_phone(phone)
    session = TASK_SESSIONS.get(phone_norm)
    if not session:
        # No hay flujo activo; ignorar en este manejador.
        return

    step = session["step"]
    txt = (message or "").strip()

    # Cancelación opcional
    if txt.lower() in {"cancelar", "/cancelar"}:
        TASK_SESSIONS.pop(phone_norm, None)
        send_whatsapp_message(phone, "❎ Creación de tarea cancelada.")
        return

    # Paso 1: Nombre
    if step == 1:
        if len(txt) < 3:
            send_whatsapp_message(phone, "⚠️ El nombre es muy corto. Intenta con algo más descriptivo.")
            return
        session["draft"]["name"] = txt
        session["step"] = 2
        send_whatsapp_message(
            phone,
            "📅 *Nueva tarea* — Paso 2/4\n\n2) *Fecha límite (vencimiento)*:\n"
            "Formato recomendado: YYYY-MM-DD (también acepto DD/MM/YYYY)."
        )
        return

    # Paso 2: Fecha
    if step == 2:
        iso = _parse_date_any(txt)
        if not iso:
            send_whatsapp_message(phone, "⚠️ Fecha no válida. Usa formatos como *2025-08-15* o *15/08/2025*.")
            return
        session["draft"]["due_date"] = iso
        session["step"] = 3
        send_whatsapp_message(
            phone,
            "⏫ *Nueva tarea* — Paso 3/4\n\n3) *Prioridad* (Alta, Media, Baja):"
        )
        return

    # Paso 3: Prioridad
    if step == 3:
        p = txt.lower()
        mapping = {"alta": "Alta", "media": "Media", "baja": "Baja"}
        if p not in mapping:
            send_whatsapp_message(phone, "⚠️ Prioridad no válida. Escribe *Alta*, *Media* o *Baja*.")
            return
        session["draft"]["priority"] = mapping[p]
        session["step"] = 4
        send_whatsapp_message(
            phone,
            "🏭 *Nueva tarea* — Paso 4/4\n\n4) *Proceso* (ej.: Costura, Fileteado, Cuerdas, RTR, etc.):"
        )
        return

    # Paso 4: Proceso
    if step == 4:
        if len(txt) < 2:
            send_whatsapp_message(phone, "⚠️ Proceso demasiado corto. Intenta nuevamente.")
            return
        session["draft"]["process"] = txt

        # Guardar tarea
        _ensure_tasks_file()
        tasks = _load_tasks()
        new_task = {
            "id": _new_task_id(),
            "name": session["draft"]["name"],
            "due_date": session["draft"]["due_date"],  # ISO YYYY-MM-DD
            "priority": session["draft"]["priority"],
            "process": session["draft"]["process"],
            "status": "pendiente",
            "created_at": datetime.now().isoformat(),
            "created_by_phone": _normalize_phone(phone),
        }

        # Enriquecer con nombre del admin
        for u in _load_users():
            if _normalize_phone(u.get("phone", "")) == _normalize_phone(phone):
                new_task["created_by"] = u.get("name", "").strip()
                break

        tasks.append(new_task)
        _save_tasks(tasks)

        # Cerrar flujo
        TASK_SESSIONS.pop(phone_norm, None)

        # Confirmación
        send_whatsapp_message(
            phone,
            "✅ *Tarea creada correctamente*\n\n"
            f"• *Nombre:* {new_task['name']}\n"
            f"• *Vence:* {new_task['due_date']}\n"
            f"• *Prioridad:* {new_task['priority']}\n"
            f"• *Proceso:* {new_task['process']}\n"
            f"• *ID:* {new_task['id']}"
        )
        return

# ─────────────────────────────────────────────────────────────────────────────
# Funciones auxiliares (opcional)
# ─────────────────────────────────────────────────────────────────────────────

def has_active_task_flow(phone: str) -> bool:
    return _normalize_phone(phone) in TASK_SESSIONS
