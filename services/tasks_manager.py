# services/tasks_manager.py
# Gestión de tareas por lenguaje natural para CiplasBot

import os
import json
import uuid
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from openai import OpenAI
from services.whatsapp_service import send_whatsapp_message
from services.session_memory import CONFIG_DIR, sessions
from workflows.daily_report import get_admin_phone  # usa users.json

# =========================
# RUTAS Y ARCHIVOS
# =========================
TASKS_FILE = os.path.join(CONFIG_DIR, "task.json")
os.makedirs(CONFIG_DIR, exist_ok=True)
if not os.path.exists(TASKS_FILE):
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump({"tasks": []}, f, ensure_ascii=False, indent=2)

# =========================
# CLIENTE OPENAI
# =========================
client = OpenAI()
OPENAI_MODEL = "gpt-4o-mini"  # o "o4-mini" si prefieres

# =========================
# UTILIDADES DE TEXTO / NORMALIZACIÓN
# =========================
def _fix_mojibake(s: str) -> str:
    """
    Intenta reparar textos UTF-8 mal decodificados como cp1252/latin1 (mojibake),
    p.ej. 'MiÃ©rcoles' -> 'Miércoles'. Si no aplica, devuelve s.
    """
    if not isinstance(s, str):
        return ""
    try:
        return s.encode("latin1").decode("utf-8")
    except Exception:
        return s

def _strip_accents(s: str) -> str:
    """Elimina acentos/diacríticos sin romper la ñ (ya que es propia) si se desea."""
    if not isinstance(s, str):
        return ""
    # Para fechas permitiremos 'manana' además de 'mañana', así que aquí removemos todo.
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")

def _safe_lower(s: str) -> str:
    """Repara mojibake y pasa a minúscula con espacios colapsados."""
    s2 = _fix_mojibake(s)
    s2 = s2.lower()
    s2 = " ".join(s2.split())
    return s2

# =========================
# UTILIDADES DE FECHA
# =========================
MONTHS_ES = {
    "enero": 1, "ene": 1,
    "febrero": 2, "feb": 2,
    "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4,
    "mayo": 5, "may": 5,
    "junio": 6, "jun": 6,
    "julio": 7, "jul": 7,
    "agosto": 8, "ago": 8,
    "septiembre": 9, "setiembre": 9, "sep": 9, "set": 9,
    "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11,
    "diciembre": 12, "dic": 12,
}

def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def parse_due_date(text: str) -> Optional[str]:
    """
    Interpreta fechas en español:
      - 'hoy', 'mañana'/'manana', 'pasado mañana'/'pasado manana'
      - 'en X días'/'en X dias'
      - YYYY-MM-DD
      - DD/MM/YYYY
      - DD-MM-YYYY
      - '15 de agosto 2025', '15 agosto 2025', '15 de ago 2025'
        (soporta 'septiembre/setiembre', abreviaturas y mojibake)

    Retorna YYYY-MM-DD o None.
    """
    if not text:
        return None

    raw = str(text)
    t = _safe_lower(raw)              # repara mojibake + lower + colapsa espacios
    t_noacc = _strip_accents(t)       # para capturar 'manana' y 'pasado manana'

    # Palabras clave
    if "hoy" in t:
        return datetime.now().strftime("%Y-%m-%d")
    if "pasado mañana" in t or "pasado manana" in t_noacc:
        return (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    if "mañana" in t or "manana" in t_noacc:
        return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    # "en X días" / "en X dias"
    m = re.search(r"en\s+(\d{1,2})\s+d[ií]as", t)
    if not m:
        m = re.search(r"en\s+(\d{1,2})\s+dias", t_noacc)
    if m:
        try:
            add = int(m.group(1))
            return (datetime.now() + timedelta(days=add)).strftime("%Y-%m-%d")
        except Exception:
            return None

    # YYYY-MM-DD
    m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", t)
    if m:
        return m.group(0)

    # DD/MM/YYYY
    m = re.search(r"\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/(20\d{2})\b", t)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except Exception:
            return None

    # DD-MM-YYYY
    m = re.search(r"\b(0?[1-9]|[12]\d|3[01])-(0?[1-9]|1[0-2])-(20\d{2})\b", t)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except Exception:
            return None

    # DD [de] MES [de] YYYY  (con variantes y abreviaturas)
    # Usamos grupos con nombre para evitar confusión de índices
    month_pat = (
        r"(?:ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|"
        r"jul(?:io)?|ago(?:sto)?|sep(?:t|tiembre)?|set(?:iembre)?|oct(?:ubre)?|"
        r"nov(?:iembre)?|dic(?:iembre)?)"
    )
    m = re.search(
        rf"\b(?P<day>0?[1-9]|[12]\d|3[01])\s*(?:de\s+)?(?P<month>{month_pat})\s*(?:de\s+)?(?P<year>20\d{{2}})\b",
        t
    )
    if m:
        d = int(m.group("day"))
        month_raw = m.group("month")
        y = int(m.group("year"))
        # Normalizamos clave de mes: tomamos 3 letras y full
        cand = [_safe_lower(month_raw), _safe_lower(month_raw)[:3]]
        for key in cand:
            if key in MONTHS_ES:
                try:
                    return datetime(y, MONTHS_ES[key], d).strftime("%Y-%m-%d")
                except Exception:
                    return None

    return None

# =========================
# PERSISTENCIA
# =========================
def _read_all() -> Dict[str, Any]:
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_all(data: Dict[str, Any]) -> None:
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =========================
# NLU CON OPENAI (Chat Completions + function calling)
# =========================
_TASK_INTENT_SYSTEM = (
    "Eres un parser NLU en español para gestión de tareas. "
    "Tu salida debe ser una llamada de función con JSON válido (sin texto adicional)."
)

def _task_intent_schema():
    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "view", "delete", "edit", "unknown"]
            },
            "task": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string", "enum": ["alta","media","baja",""]},
                    "due_date_text": {"type": "string"}
                }
            },
            "filters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "enum": ["hoy","mañana","semana","todas","vencidas","pendientes","fecha"]
                    },
                    "date": {"type": "string"},
                    "status": {"type":"string", "enum":["pendiente","completada","todas"]}
                }
            }
        },
        "required": ["action"]
    }

def nlu_intent(user_text: str) -> Dict[str, Any]:
    """
    Usa Chat Completions + function calling.
    Retorna un dict con: action, task{...}, filters{...}
    """
    examples = """
    Instrucciones:
    - Detecta intención: create | view | delete | edit | unknown.
    - Extrae task.title, task.description, task.priority (alta|media|baja), task.due_date_text (texto tal cual).
    - En 'view' llena filters.time si aplica: hoy/mañana/semana/vencidas/pendientes/todas/fecha.
    - En 'delete' o 'edit', si no dan id, usa title aproximado en task.title.
    - SOLO JSON.
    """

    messages = [
        {"role": "system", "content": _TASK_INTENT_SYSTEM},
        {"role": "user", "content": examples},
        {"role": "user", "content": user_text},
    ]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "extract_task_intent",
                "description": "Detecta la intención y extrae campos de tarea/filtros.",
                "parameters": _task_intent_schema(),
            },
        }
    ]

    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=tools,
            tool_choice={"type": "function", "function": {"name": "extract_task_intent"}},
            temperature=0
        )
        choice = resp.choices[0]
        msg = choice.message

        # Preferimos tool_calls (function calling)
        if getattr(msg, "tool_calls", None):
            args = msg.tool_calls[0].function.arguments
            return json.loads(args)

        # Fallback: intentar contenido directo como JSON
        if msg.content:
            try:
                return json.loads(msg.content)
            except Exception:
                pass

        return {"action": "unknown"}

    except Exception as e:
        print(f"⚠️ nlu_intent error: {e}")
        return {"action": "unknown"}

# =========================
# BUSQUEDA / MATCH DE TAREAS
# =========================
def _normalize(s: str) -> str:
    # Normaliza para comparación: repara mojibake, lower y colapsa espacios
    return re.sub(r"\s+", " ", _safe_lower(s)).strip()

def _find_tasks_by_title_like(tasks: List[Dict[str, Any]], title_like: str) -> List[Dict[str, Any]]:
    if not title_like:
        return []
    key = _normalize(title_like)
    out = []
    for t in tasks:
        if key in _normalize(t.get("title", "")):
            out.append(t)
    return out

def _find_task_by_id(tasks: List[Dict[str, Any]], tid: str) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if t.get("id") == tid:
            return t
    return None

# =========================
# CRUD DE TAREAS
# =========================
def _ensure_task_dict(task_like: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza/crea una tarea base."""
    return {
        "id": (task_like.get("id") or uuid.uuid4().hex[:8]),
        "title": (task_like.get("title") or "").strip(),
        "description": (task_like.get("description") or "").strip(),
        "priority": (task_like.get("priority") or "media").strip().lower(),
        "due": None,  # YYYY-MM-DD
        "status": "pendiente",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def create_task_from_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    data = _read_all()
    task_in = intent.get("task", {}) or {}
    t = _ensure_task_dict(task_in)

    # due
    due_text = (task_in.get("due_date_text") or "").strip()
    due = parse_due_date(due_text) if due_text else None
    t["due"] = due

    if not t["title"]:
        raise ValueError("Falta el título de la tarea.")

    data["tasks"].append(t)
    _write_all(data)
    return t

def list_tasks(filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    tasks = _read_all().get("tasks", [])
    if not filters:
        return tasks

    # status
    status = filters.get("status")
    if status and status in ("pendiente", "completada"):
        tasks = [t for t in tasks if t.get("status") == status]

    # time filter
    tf = filters.get("time")
    if tf in ("hoy", "mañana", "semana", "vencidas", "fecha"):
        today = datetime.now().date()

        def to_date(s):
            try:
                return datetime.strptime(s, "%Y-%m-%d").date()
            except Exception:
                return None

        if tf == "hoy":
            tasks = [t for t in tasks if t.get("due") and to_date(t["due"]) == today]
        elif tf == "mañana":
            tomorrow = today + timedelta(days=1)
            tasks = [t for t in tasks if t.get("due") and to_date(t["due"]) == tomorrow]
        elif tf == "semana":
            end = today + timedelta(days=7)
            tasks = [t for t in tasks if t.get("due") and to_date(t["due"]) and today <= to_date(t["due"]) <= end]
        elif tf == "vencidas":
            tasks = [t for t in tasks if t.get("due") and to_date(t["due"]) and to_date(t["due"]) < today and t.get("status") == "pendiente"]
        elif tf == "fecha":
            d = parse_due_date(filters.get("date", "") or "")
            if d:
                dd = datetime.strptime(d, "%Y-%m-%d").date()
                tasks = [t for t in tasks if t.get("due") and to_date(t["due"]) == dd]

    # ordenar por due asc, luego prioridad
    prio_rank = {"alta": 0, "media": 1, "baja": 2}
    tasks.sort(key=lambda x: (
        x.get("due") or "9999-12-31",
        prio_rank.get(x.get("priority", "media"), 1)
    ))
    return tasks

def delete_task(intent: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Elimina por id o por título aproximado. Retorna lista de tareas eliminadas.
    """
    data = _read_all()
    tasks = data.get("tasks", [])
    task_in = intent.get("task", {}) or {}
    target_id = (task_in.get("id") or "").strip()
    title_like = (task_in.get("title") or "").strip()

    removed: List[Dict[str, Any]] = []
    keep: List[Dict[str, Any]] = []

    if target_id:
        for t in tasks:
            if t.get("id") == target_id:
                removed.append(t)
            else:
                keep.append(t)
    elif title_like:
        matches = _find_tasks_by_title_like(tasks, title_like)
        match_ids = {m["id"] for m in matches}
        for t in tasks:
            if t["id"] in match_ids:
                removed.append(t)
            else:
                keep.append(t)
    else:
        raise ValueError("Falta id o título para eliminar.")

    data["tasks"] = keep
    _write_all(data)
    return removed

def edit_task(intent: Dict[str, Any]) -> Dict[str, Any]:
    """
    Edita por id o por título aproximado (si hay 1 match).
    Campos editables: title, description, priority, due (por due_date_text), status.
    """
    data = _read_all()
    tasks = data.get("tasks", [])
    task_in = intent.get("task", {}) or {}
    target_id = (task_in.get("id") or "").strip()
    title_like = (task_in.get("title") or "").strip()

    target = None
    if target_id:
        target = _find_task_by_id(tasks, target_id)
    elif title_like:
        matches = _find_tasks_by_title_like(tasks, title_like)
        if len(matches) == 1:
            target = matches[0]
        elif len(matches) == 0:
            raise ValueError("No se encontró ninguna tarea con ese título.")
        else:
            raise ValueError("Título ambiguo. Especifique el ID o un título más preciso.")
    else:
        raise ValueError("Falta id o título para editar.")

    # Actualizaciones
    if task_in.get("title"):
        target["title"] = task_in["title"].strip()
    if task_in.get("description") is not None:
        target["description"] = (task_in["description"] or "").strip()
    if task_in.get("priority"):
        pr = (task_in["priority"] or "").strip().lower()
        if pr in ("alta", "media", "baja"):
            target["priority"] = pr
    if task_in.get("due_date_text"):
        target["due"] = parse_due_date(task_in["due_date_text"])

    # Permitir status desde filtros (opcional)
    st = (intent.get("filters", {}) or {}).get("status", "")
    if st in ("pendiente", "completada"):
        target["status"] = st

    _write_all(data)
    return target

# =========================
# MENSAJERÍA / UI EN WHATSAPP
# =========================
def _format_task_line(t: Dict[str, Any], idx: int) -> str:
    pr_emoji = {"alta": "🔴", "media": "🟡", "baja": "🟢"}.get(t.get("priority", "media"), "🟡")
    st_emoji = "✅" if t.get("status") == "completada" else "⏳"
    due_txt = t.get("due") or "—"
    return f"{idx}. {st_emoji} {pr_emoji} [{t['id']}] {t['title']} 〰️ vence: {due_txt}"

def send_task_menu(to_phone: str) -> None:
    msg = (
        "🗂 *Gestión de tareas*\n"
        "Dime en lenguaje natural lo que quieres y lo interpreto. Ejemplos:\n"
        "• *Crea* una tarea para mañana comprar Vistamaxx, prioridad alta.\n"
        "• *Ver* tareas pendientes hoy.\n"
        "• *Editar* la tarea comprar Vistamaxx, pásala al 20-08-2025.\n"
        "• *Eliminar* la tarea comprar Vistamaxx.\n\n"
        "Acciones disponibles: *Crear nueva tarea*, *Ver tareas pendientes*, *Eliminar tarea*, *Editar tarea*."
    )
    send_whatsapp_message(to_phone, msg)

def _send_list(to_phone: str, tasks: List[Dict[str, Any]], header: str = "📋 Tareas"):
    if not tasks:
        send_whatsapp_message(to_phone, f"{header}\nNo hay tareas para mostrar.")
        return
    lines = [header]
    for i, t in enumerate(tasks, start=1):
        lines.append(_format_task_line(t, i))
    send_whatsapp_message(to_phone, "\n".join(lines))

# =========================
# FLUJO DE FOLLOW-UP (cuando faltan datos)
# =========================
def _open_flow(user_phone: str, flow: str, payload: Dict[str, Any]):
    sessions[user_phone] = {"flow": flow, "payload": payload}

def _close_flow(user_phone: str):
    if user_phone in sessions:
        del sessions[user_phone]

def handle_followup(user_text: str, user_phone: str) -> bool:
    """
    Si hay un flujo abierto en sessions, intenta procesar el dato faltante.
    Retorna True si fue manejado (consumido), False si no había flujo.
    """
    state = sessions.get(user_phone)
    if not state:
        return False

    flow = state.get("flow")
    if flow == "task_title_missing":
        title = user_text.strip()
        if len(title) < 3:
            send_whatsapp_message(user_phone, "El título es muy corto. Prueba con algo más descriptivo.")
            return True
        payload = state.get("payload", {})
        intent = payload.get("intent", {})
        intent.setdefault("task", {})["title"] = title
        try:
            t = create_task_from_intent(intent)
            send_whatsapp_message(user_phone, f"✅ Tarea creada: [{t['id']}] {t['title']} (vence: {t.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude crear la tarea: {e}")
        _close_flow(user_phone)
        return True

    if flow == "task_disambiguate_edit":
        # Usuario debe responder con un ID
        tid = user_text.strip().strip("[]")
        data = _read_all()
        t = _find_task_by_id(data.get("tasks", []), tid)
        if not t:
            send_whatsapp_message(user_phone, "No encontré ese ID. Por favor responde con un ID válido (texto entre corchetes).")
            return True
        payload = state.get("payload", {})
        intent = payload.get("intent", {})
        intent.setdefault("task", {})["id"] = tid
        try:
            t2 = edit_task(intent)
            send_whatsapp_message(user_phone, f"✏️ Tarea actualizada: [{t2['id']}] {t2['title']} (vence: {t2.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude editar: {e}")
        _close_flow(user_phone)
        return True

    if flow == "task_disambiguate_delete":
        tid = user_text.strip().strip("[]")
        data = _read_all()
        t = _find_task_by_id(data.get("tasks", []), tid)
        if not t:
            send_whatsapp_message(user_phone, "ID inválido. Responde con el ID entre corchetes de la tarea a eliminar.")
            return True
        intent = state.get("payload", {}).get("intent", {})
        intent.setdefault("task", {})["id"] = tid
        try:
            removed = delete_task(intent)
            if removed:
                send_whatsapp_message(user_phone, f"🗑️ Eliminada: [{removed[0]['id']}] {removed[0]['title']}")
            else:
                send_whatsapp_message(user_phone, "No se eliminó ninguna tarea.")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude eliminar: {e}")
        _close_flow(user_phone)
        return True

    return False

# =========================
# ENRUTADOR PRINCIPAL
# =========================
def _is_admin(phone: str) -> bool:
    try:
        admin = get_admin_phone()
        return str(phone).strip() == str(admin).strip()
    except Exception:
        # Si falla, permitir por ahora
        return True

def maybe_handle_task_message(user_text: str, user_name: str, user_phone: str) -> bool:
    """
    Intenta interpretar un mensaje del ADMIN como gestión de tareas.
    Retorna True si manejó el mensaje (ya envió WhatsApp), False si no es de tareas.
    """
    # Solo admin
    if not _is_admin(user_phone):
        return False

    low = _safe_lower(user_text or "")
    # Si hay un flujo abierto, que lo procese handle_followup (se llama antes desde main)
    if sessions.get(user_phone):
        return False

    intent = nlu_intent(user_text)
    action = intent.get("action", "unknown")

    if action == "unknown":
        # Solo atrapamos si parece intención de tareas (palabras clave)
        if any(k in low for k in ["tarea", "tareas", "pendientes", "crear", "eliminar", "editar", "ver"]):
            send_task_menu(user_phone)
            return True
        return False

    # --- CREATE ---
    if action == "create":
        try:
            tk = intent.get("task", {}) or {}
            if not tk.get("title"):
                _open_flow(user_phone, "task_title_missing", {"intent": intent})
                send_whatsapp_message(user_phone, "🆕 ¿Cuál es el *título* de la tarea?")
                return True
            t = create_task_from_intent(intent)
            send_whatsapp_message(user_phone, f"✅ Tarea creada: [{t['id']}] {t['title']} (vence: {t.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude crear la tarea: {e}")
        return True

    # --- VIEW ---
    if action == "view":
        filters = intent.get("filters", {}) or {}
        # valor por defecto: pendientes
        if not filters.get("status"):
            filters["status"] = "pendiente"
        tasks = list_tasks(filters)
        header = "📋 Tareas"
        if filters.get("time") == "hoy":
            header = "📅 Tareas de *hoy*"
        elif filters.get("time") == "mañana":
            header = "📅 Tareas de *mañana*"
        elif filters.get("time") == "semana":
            header = "📅 Tareas de *esta semana*"
        elif filters.get("time") == "vencidas":
            header = "⏰ Tareas *vencidas*"
        elif filters.get("time") == "fecha":
            header = f"📅 Tareas para la *fecha*"
        _send_list(user_phone, tasks, header=header)
        return True

    # --- DELETE ---
    if action == "delete":
        data = _read_all()
        task_in = intent.get("task", {}) or {}
        tid = (task_in.get("id") or "").strip()
        title_like = (task_in.get("title") or "").strip()

        if not tid and title_like:
            matches = _find_tasks_by_title_like(data.get("tasks", []), title_like)
            if len(matches) == 0:
                send_whatsapp_message(user_phone, "No encontré tareas que coincidan con ese título.")
                return True
            if len(matches) > 1:
                _send_list(user_phone, matches, header="Selecciona el *ID* a eliminar (responde con el ID entre corchetes):")
                _open_flow(user_phone, "task_disambiguate_delete", {"intent": intent})
                return True

        try:
            removed = delete_task(intent)
            if removed:
                send_whatsapp_message(user_phone, f"🗑️ Eliminada: [{removed[0]['id']}] {removed[0]['title']}")
            else:
                send_whatsapp_message(user_phone, "No se eliminó ninguna tarea.")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude eliminar: {e}")
        return True

    # --- EDIT ---
    if action == "edit":
        data = _read_all()
        task_in = intent.get("task", {}) or {}
        tid = (task_in.get("id") or "").strip()
        title_like = (task_in.get("title") or "").strip()

        if not tid and title_like:
            matches = _find_tasks_by_title_like(data.get("tasks", []), title_like)
            if len(matches) == 0:
                send_whatsapp_message(user_phone, "No encontré tareas que coincidan con ese título.")
                return True
            if len(matches) > 1:
                _send_list(user_phone, matches, header="Hay varias coincidencias. Responde con el *ID* a editar:")
                _open_flow(user_phone, "task_disambiguate_edit", {"intent": intent})
                return True

        try:
            t = edit_task(intent)
            send_whatsapp_message(user_phone, f"✏️ Tarea actualizada: [{t['id']}] {t['title']} (vence: {t.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude editar: {e}")
        return True

    return False

# =========================
# RECORDATORIO DIARIO PARA EL ADMIN (07:30)
# =========================
def _build_pending_today_message() -> str:
    """Arma el mensaje con solo tareas pendientes de HOY."""
    hoy = datetime.now().strftime("%Y-%m-%d")
    tasks = list_tasks({"time": "hoy", "status": "pendiente"})
    header = f"📅 *Tareas pendientes para HOY* ({hoy})"
    if not tasks:
        return header + "\nNo tienes tareas pendientes para hoy. ✅"

    lines = [header]
    for i, t in enumerate(tasks, start=1):
        pr_emoji = {"alta":"🔴","media":"🟡","baja":"🟢"}.get(t.get("priority","media"),"🟡")
        title = t.get("title","").strip() or "(sin título)"
        tid = t.get("id","")
        due = t.get("due") or "—"
        lines.append(f"{i}. ⏳ {pr_emoji} [{tid}] {title} 〰️ vence: {due}")
    return "\n".join(lines)

def send_daily_pending_tasks_reminder(send_if_empty: bool = True) -> None:
    """
    Envía por WhatsApp al ADMIN el resumen de tareas PENDIENTES de HOY.
    - send_if_empty=True: envía también si no hay pendientes (con mensaje de 'No tienes tareas...')
    """
    try:
        admin_phone = get_admin_phone()
    except Exception as e:
        print(f"⚠️ No se pudo obtener el teléfono del admin: {e}")
        return

    if not admin_phone:
        print("⚠️ Admin phone no configurado.")
        return

    hoy_tasks = list_tasks({"time": "hoy", "status": "pendiente"})
    if not hoy_tasks and not send_if_empty:
        print("ℹ️ No hay tareas pendientes para hoy. No se envía recordatorio.")
        return

    msg = _build_pending_today_message()
    try:
        send_whatsapp_message(admin_phone, msg)
        print(f"✅ Recordatorio de tareas enviado a {admin_phone}")
    except Exception as e:
        print(f"❌ Error enviando recordatorio de tareas al admin: {e}")

# Alias por compatibilidad si el import se hizo en singular:
send_daily_pending_task_reminder = send_daily_pending_tasks_reminder
