# services/tasks_manager.py
# Gestión de tareas por lenguaje natural para CiplasBot
# - Asignación a supervisores (tolerante a acentos, _, ., - y @)
# - Notificación al supervisor al crear
# - Migración de task.json para añadir assignee_*
# - Recordatorios: admin + supervisores

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

VERSION = "services.tasks_manager v2.3"
TASKS_DEBUG = True
print(f"🔧 {VERSION} importado")

# =========================
# RUTAS Y ARCHIVOS
# =========================
TASKS_FILE = os.path.join(CONFIG_DIR, "task.json")
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")
os.makedirs(CONFIG_DIR, exist_ok=True)

def _ensure_tasks_file():
    if not os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump({"tasks": []}, f, ensure_ascii=False, indent=2)

_ensure_tasks_file()

# =========================
# CLIENTE OPENAI
# =========================
client = OpenAI()
OPENAI_MODEL = "gpt-4o-mini"  # o "o4-mini"

# =========================
# HELPERS TEXTO / TELÉFONOS / USUARIOS
# =========================
def _fix_mojibake(s: str) -> str:
    if not isinstance(s, str):
        return ""
    try:
        return s.encode("latin1").decode("utf-8")
    except Exception:
        return s

def _strip_accents(s: str) -> str:
    if not isinstance(s, str):
        return ""
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")

def _safe_lower(s: str) -> str:
    s2 = _fix_mojibake(s or "")
    s2 = " ".join(s2.split())
    return s2.lower()

def _normalize_name(s: str) -> str:
    """
    Normaliza nombres para comparación:
    - minúsculas
    - sin tildes
    - convierte _, ., - y @ en espacios
    - colapsa espacios
    """
    if not isinstance(s, str):
        return ""
    s = _strip_accents(_safe_lower(s))
    s = s.replace("@", " ")
    s = re.sub(r"[_\.\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _pretty_display_name(s: str) -> str:
    """Convierte 'orlando_diaz' o 'orlando.diaz' a 'Orlando Diaz' para mostrar."""
    s = re.sub(r"[_\.\-]+", " ", (s or "").strip())
    return " ".join(w.capitalize() for w in s.split())

def _digits_only(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())

def _last10(d: str) -> str:
    d2 = _digits_only(d)
    return d2[-10:] if len(d2) >= 10 else d2

def _canon_e164_co(phone: str) -> str:
    d = _digits_only(phone)
    if d.startswith("57") and len(d) == 12:
        return d
    tail = _last10(d)
    return ("57" + tail) if tail else d

def _normalize_phone(phone: str) -> str:
    return _canon_e164_co(phone)

def _strip_quotes(s: str) -> str:
    s = (s or "").strip()
    quote_chars = '\"\'“”«»'
    if len(s) >= 2 and s[0] in quote_chars and s[-1] in quote_chars:
        return s[1:-1].strip()
    return s.strip("“”«»\"'").strip()

def _load_users() -> List[dict]:
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("users", [])

def _is_admin(phone: str) -> bool:
    """Compara con admin de users.json; *fallback* por rol."""
    try:
        admin = get_admin_phone()
        return str(_normalize_phone(phone)).strip() == str(_normalize_phone(admin)).strip()
    except Exception:
        p = _normalize_phone(phone)
        for u in _load_users():
            if _normalize_phone(u.get("phone", "")) == p and (u.get("role", "").lower() in {"administrador", "admin"}):
                return True
        return False

def _list_supervisors() -> List[dict]:
    sups = [u for u in _load_users() if (u.get("role", "").strip().lower() == "supervisor")]
    if TASKS_DEBUG:
        print(f"👥 SUPERVISORES: {[u.get('name') for u in sups]}")
    return sups

def _find_supervisor_by_hint(hint: str) -> Optional[dict]:
    """
    Busca supervisor por teléfono o nombre (tolerante a acentos/espacios/_,.-,@).
    """
    if not hint:
        return None
    sups = _list_supervisors()
    if not sups:
        return None

    # Teléfono
    h_phone = _normalize_phone(hint)
    if _digits_only(h_phone):
        for u in sups:
            if _normalize_phone(u.get("phone", "")) == h_phone:
                return u

    # Nombre (normalizado en ambos lados)
    h_norm = _normalize_name(hint)
    exact = [u for u in sups if _normalize_name(u.get("name", "")) == h_norm]
    if len(exact) == 1:
        return exact[0]

    contain = [u for u in sups if h_norm in _normalize_name(u.get("name", ""))]
    if len(contain) == 1:
        return contain[0]
    if len(contain) > 1:
        contain.sort(key=lambda u: len(u.get("name", "")), reverse=True)
        return contain[0]
    return None

def _fallback_match_supervisor_in_text(text: str) -> Optional[dict]:
    """
    Si el NLU no trae assignee, intenta detectar por:
    - @menciones
    - frases: 'a/para/asignar a <nombre>'
    - substring del nombre del supervisor
    """
    if not text:
        return None

    # 1) @menciones (acepta '_' '.' '-')
    m = re.findall(r"@([A-Za-zÁÉÍÓÚÑáéíóúñ0-9_.\-]+)", text)
    for tag in m or []:
        sup = _find_supervisor_by_hint(tag)
        if sup:
            if TASKS_DEBUG:
                print(f"🕵️ ASSIGNEE DEBUG: @mención → {sup.get('name')}")
            return sup

    # 2) Frases: a|para|asignar a + Nombre (1 a 4 tokens). Corta en signos.
    name_pat = r"[A-Za-zÁÉÍÓÚÑáéíóúñ_.\-]+(?:\s+[A-Za-zÁÉÍÓÚÑáéíóúñ_.\-]+){0,3}"
    m2 = re.findall(rf"(?:\bpara\b|\ba\b|\basignar\s+a)\s+({name_pat})(?=[\s,:;\"“”'»)]|$)", text, flags=re.IGNORECASE)
    for chunk in m2 or []:
        sup = _find_supervisor_by_hint(chunk)
        if sup:
            if TASKS_DEBUG:
                print(f"🕵️ ASSIGNEE DEBUG: frase → {sup.get('name')}")
            return sup

    # 3) Substring directo del nombre completo (normalizado)
    txt_norm = _normalize_name(text)
    best = None
    best_len = 0
    for u in _list_supervisors():
        nm = _normalize_name(u.get("name", ""))
        if nm and nm in txt_norm and len(nm) > best_len:
            best = u
            best_len = len(nm)
    if best and TASKS_DEBUG:
        print(f"🕵️ ASSIGNEE DEBUG: substring → {best.get('name')}")
    return best

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
    Entiende: hoy, mañana/manana, pasado mañana/manana, en X días,
    YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, '15 de agosto 2025'
    """
    if not text:
        return None
    raw = str(text)
    t = _safe_lower(raw)
    t_noacc = _strip_accents(t)

    if "hoy" in t:
        return datetime.now().strftime("%Y-%m-%d")
    if "pasado mañana" in t or "pasado manana" in t_noacc:
        return (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
    if "mañana" in t or "manana" in t_noacc:
        return (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    m = re.search(r"en\s+(\d{1,2})\s+d[ií]as", t) or re.search(r"en\s+(\d{1,2})\s+dias", t_noacc)
    if m:
        try:
            add = int(m.group(1))
            return (datetime.now() + timedelta(days=add)).strftime("%Y-%m-%d")
        except Exception:
            return None

    m = re.search(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", t)
    if m:
        return m.group(0)

    m = re.search(r"\b(0?[1-9]|[12]\d|3[01])/(0?[1-9]|1[0-2])/(20\d{2})\b", t)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except Exception:
            return None

    m = re.search(r"\b(0?[1-9]|[12]\d|3[01])-(0?[1-9]|1[0-2])-(20\d{2})\b", t)
    if m:
        d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mth, d).strftime("%Y-%m-%d")
        except Exception:
            return None

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
        for key in (_safe_lower(month_raw), _safe_lower(month_raw)[:3]):
            if key in MONTHS_ES:
                try:
                    return datetime(y, MONTHS_ES[key], d).strftime("%Y-%m-%d")
                except Exception:
                    return None
    return None

# =========================
# PERSISTENCIA + MIGRACIÓN
# =========================
def _read_all() -> Dict[str, Any]:
    with open(TASKS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _write_all(data: Dict[str, Any]) -> None:
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _migrate_tasks_schema_add_assignee_fields() -> None:
    """
    Asegura que todas las tareas tengan assignee_* y priority en minúsculas.
    """
    try:
        data = _read_all()
    except FileNotFoundError:
        return

    tasks = data.get("tasks", [])
    changed = False

    for t in tasks:
        if "assignee_name" not in t:
            t["assignee_name"] = ""
            changed = True
        if "assignee_phone" not in t:
            t["assignee_phone"] = ""
            changed = True
        if "assignee_phone_raw" not in t:
            t["assignee_phone_raw"] = ""
            changed = True
        if "priority" in t and isinstance(t["priority"], str):
            newp = t["priority"].strip().lower()
            if newp != t["priority"]:
                t["priority"] = newp
                changed = True

    if changed:
        _write_all({"tasks": tasks})
        print("🛠️ task.json migrado: campos assignee_* + priority normalizada.")

_migrate_tasks_schema_add_assignee_fields()

# =========================
# NLU (OpenAI function calling)
# =========================
_TASK_INTENT_SYSTEM = (
    "Eres un parser NLU en español para gestión de tareas. "
    "Tu salida debe ser una llamada de función con JSON válido (sin texto adicional)."
)

def _task_intent_schema():
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "view", "delete", "edit", "unknown"]},
            "task": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "priority": {"type": "string", "enum": ["alta", "media", "baja", ""]},
                    "due_date_text": {"type": "string"},
                    "assignee": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "phone": {"type": "string"}
                        }
                    }
                }
            },
            "filters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "enum": ["hoy", "mañana", "semana", "todas", "vencidas", "pendientes", "fecha"]
                    },
                    "date": {"type": "string"},
                    "status": {"type": "string", "enum": ["pendiente", "completada", "todas"]}
                }
            }
        },
        "required": ["action"]
    }

def nlu_intent(user_text: str) -> Dict[str, Any]:
    examples = """
    Instrucciones:
    - Detecta intención: create|view|delete|edit|unknown.
    - Extrae:
        task.title  → sin comillas si el usuario las puso.
        task.description → opcional.
        task.priority → alta|media|baja.
        task.due_date_text → tal cual el usuario lo dijo.
        task.assignee.name/phone → si dice "a X", "para X", "asignar a X", "@X".
    - En 'view' llena filters.time si aplica.
    - En 'delete' o 'edit', si no dan id, usa task.title aproximado.
    - Solo JSON.
    """

    messages = [
        {"role": "system", "content": _TASK_INTENT_SYSTEM},
        {"role": "user", "content": examples},
        {"role": "user", "content": user_text},
    ]

    tools = [{
        "type": "function",
        "function": {
            "name": "extract_task_intent",
            "description": "Detecta la intención y extrae campos de tarea/filtros.",
            "parameters": _task_intent_schema(),
        },
    }]

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

        if getattr(msg, "tool_calls", None):
            args = msg.tool_calls[0].function.arguments
            if TASKS_DEBUG:
                print(f"🧭 NLU DEBUG: {args}")
            return json.loads(args)

        if msg.content:
            try:
                if TASKS_DEBUG:
                    print(f"🧭 NLU DEBUG (content): {msg.content}")
                return json.loads(msg.content)
            except Exception:
                pass

        return {"action": "unknown"}

    except Exception as e:
        print(f"⚠️ nlu_intent error: {e}")
        return {"action": "unknown"}

# =========================
# BÚSQUEDA / MATCH
# =========================
def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", _safe_lower(s)).strip()

def _find_tasks_by_title_like(tasks: List[Dict[str, Any]], title_like: str) -> List[Dict[str, Any]]:
    if not title_like:
        return []
    key = _normalize(title_like)
    return [t for t in tasks if key in _normalize(t.get("title", ""))]

def _find_task_by_id(tasks: List[Dict[str, Any]], tid: str) -> Optional[Dict[str, Any]]:
    for t in tasks:
        if t.get("id") == tid:
            return t
    return None

# =========================
# CRUD
# =========================
def _ensure_task_dict(task_like: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": (task_like.get("id") or uuid.uuid4().hex[:8]),
        "title": _strip_quotes((task_like.get("title") or "")),
        "description": (task_like.get("description") or "").strip(),
        "priority": (task_like.get("priority") or "media").strip().lower(),
        "due": None,
        "status": "pendiente",
        "assignee_name": "",
        "assignee_phone": "",
        "assignee_phone_raw": "",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

def _notify_supervisor_new_task(t: Dict[str, Any]) -> None:
    phone = t.get("assignee_phone_raw") or t.get("assignee_phone")
    if not phone:
        return
    msg = (
        "🆕 *Nueva tarea asignada*\n\n"
        f"• *Nombre:* {t.get('title','')}\n"
        f"• *Vence:* {t.get('due','—')}\n"
        f"• *Prioridad:* {t.get('priority','')}\n"
        f"• *ID:* {t.get('id','')}\n\n"
        "Por favor, revísala y ejecútala según prioridad. ✅"
    )
    try:
        send_whatsapp_message(phone, msg)
    except Exception as e:
        print(f"⚠️ Error notificando al supervisor ({phone}): {e}")

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

    # Asignación: (1) NLU
    assignee = task_in.get("assignee") or {}
    hint_name = (assignee.get("name") or "").strip()
    hint_phone = (assignee.get("phone") or "").strip()
    chosen = None
    if hint_phone:
        chosen = _find_supervisor_by_hint(hint_phone)
    if not chosen and hint_name:
        chosen = _find_supervisor_by_hint(hint_name)

    # (2) Fallback con texto crudo
    raw_txt = intent.get("__raw_text") or ""
    if not chosen and raw_txt:
        chosen = _fallback_match_supervisor_in_text(raw_txt)

    if chosen:
        raw_name = chosen.get("name", "").strip()
        t["assignee_name"] = _pretty_display_name(raw_name) or raw_name
        t["assignee_phone_raw"] = chosen.get("phone", "").strip()
        t["assignee_phone"] = _normalize_phone(chosen.get("phone", ""))

    data.setdefault("tasks", []).append(t)
    _write_all(data)

    # Notificar supervisor si aplica
    if t.get("assignee_phone") or t.get("assignee_phone_raw"):
        _notify_supervisor_new_task(t)
        if TASKS_DEBUG:
            print(f"📣 Notificado supervisor: {t.get('assignee_name')} / {t.get('assignee_phone_raw')}")

    if TASKS_DEBUG:
        print(f"📝 TAREA CREADA → assignee: {t.get('assignee_name') or '(ninguno)'}")

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
    data = _read_all()
    tasks = data.get("tasks", [])
    task_in = intent.get("task", {}) or {}
    target_id = (task_in.get("id") or "").strip()
    title_like = (task_in.get("title") or "").strip()

    removed: List[Dict[str, Any]] = []
    keep: List[Dict[str, Any]] = []

    if target_id:
        if not re.fullmatch(r"(?:[0-9a-f]{8}|T\d{17,})", target_id):
            raise ValueError("ID de tarea con formato inválido.")
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
        target["title"] = _strip_quotes(task_in["title"])
    if task_in.get("description") is not None:
        target["description"] = (task_in["description"] or "").strip()
    if task_in.get("priority"):
        pr = (task_in["priority"] or "").strip().lower()
        if pr in ("alta", "media", "baja"):
            target["priority"] = pr
    if task_in.get("due_date_text"):
        target["due"] = parse_due_date(task_in["due_date_text"])

    # Reasignación (NLU + fallback)
    assignee = task_in.get("assignee") or {}
    raw_txt = intent.get("__raw_text") or ""
    chosen = None
    if assignee.get("phone"):
        chosen = _find_supervisor_by_hint(assignee.get("phone"))
    if not chosen and assignee.get("name"):
        chosen = _find_supervisor_by_hint(assignee.get("name"))
    if not chosen and raw_txt:
        chosen = _fallback_match_supervisor_in_text(raw_txt)
    if chosen:
        raw_name = chosen.get("name", "").strip()
        target["assignee_name"] = _pretty_display_name(raw_name) or raw_name
        target["assignee_phone_raw"] = chosen.get("phone", "").strip()
        target["assignee_phone"] = _normalize_phone(chosen.get("phone", ""))

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
    who = f" • 👤 {t['assignee_name']}" if t.get("assignee_name") else ""
    return f"{idx}. {st_emoji} {pr_emoji} [{t['id']}] {t['title']} 〰️ vence: {due_txt}{who}"

def send_task_menu(to_phone: str) -> None:
    msg = (
        "🗂 *Gestión de tareas*\n"
        "Ejemplos:\n"
        "• Crea tarea *a Orlando Diaz*: \"Enviar informe PNC\", vence *mañana*, prioridad *alta*.\n"
        "• Ver tareas pendientes hoy.\n"
        "• Editar tarea 'Enviar informe PNC', pásala al 20-08-2025.\n"
        "• Eliminar tarea 'Enviar informe PNC'."
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
# FOLLOW-UPS
# =========================
def _open_flow(user_phone: str, flow: str, payload: Dict[str, Any]):
    sessions[user_phone] = {"flow": flow, "payload": payload}

def _close_flow(user_phone: str):
    if user_phone in sessions:
        del sessions[user_phone]

def handle_followup(user_text: str, user_phone: str) -> bool:
    state = sessions.get(user_phone)
    if not state:
        return False

    flow = state.get("flow")
    if flow == "task_title_missing":
        title = _strip_quotes(user_text.strip())
        if len(title) < 3:
            send_whatsapp_message(user_phone, "El título es muy corto. Prueba con algo más descriptivo.")
            return True
        payload = state.get("payload", {})
        intent = payload.get("intent", {})
        intent.setdefault("task", {})["title"] = title
        try:
            t = create_task_from_intent(intent)
            who = f" → 👤 {t.get('assignee_name')}" if t.get("assignee_name") else ""
            send_whatsapp_message(user_phone, f"✅ Tarea creada: [{t['id']}] {t['title']}{who} (vence: {t.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude crear la tarea: {e}")
        _close_flow(user_phone)
        return True

    if flow == "task_disambiguate_edit":
        tid = user_text.strip().strip("[]")
        if not re.fullmatch(r"(?:[0-9a-f]{8}|T\d{17,})", tid):
            send_whatsapp_message(user_phone, "ID inválido. Responde con el ID exacto entre corchetes.")
            return True
        data = _read_all()
        t = _find_task_by_id(data.get("tasks", []), tid)
        if not t:
            send_whatsapp_message(user_phone, "No encontré ese ID. Intenta nuevamente.")
            return True
        payload = state.get("payload", {})
        intent = payload.get("intent", {})
        intent.setdefault("task", {})["id"] = tid
        try:
            t2 = edit_task(intent)
            who = f" → 👤 {t2.get('assignee_name')}" if t2.get("assignee_name") else ""
            send_whatsapp_message(user_phone, f"✏️ Tarea actualizada: [{t2['id']}] {t2['title']}{who} (vence: {t2.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude editar: {e}")
        _close_flow(user_phone)
        return True

    if flow == "task_disambiguate_delete":
        tid = user_text.strip().strip("[]")
        if not re.fullmatch(r"(?:[0-9a-f]{8}|T\d{17,})", tid):
            send_whatsapp_message(user_phone, "ID inválido. Responde con el ID exacto entre corchetes.")
            return True
        data = _read_all()
        t = _find_task_by_id(data.get("tasks", []), tid)
        if not t:
            send_whatsapp_message(user_phone, "No encontré ese ID. Intenta nuevamente.")
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
# ENRUTADOR (SOLO ADMIN)
# =========================
def _is_admin_strict(phone: str) -> bool:
    return _is_admin(phone)

def maybe_handle_task_message(user_text: str, user_name: str, user_phone: str) -> bool:
    if not _is_admin_strict(user_phone):
        return False

    low = _safe_lower(user_text or "")
    if sessions.get(user_phone):
        return False

    intent = nlu_intent(user_text)
    action = intent.get("action", "unknown")
    intent["__raw_text"] = user_text  # para fallbacks

    # Fallback de asignatario si NLU no lo trajo
    if action in {"create", "edit"}:
        tk = intent.get("task", {}) or {}
        ass = tk.get("assignee") or {}
        if not ass.get("name") and not ass.get("phone"):
            sup = _fallback_match_supervisor_in_text(user_text)
            if sup:
                intent.setdefault("task", {}).setdefault("assignee", {})
                intent["task"]["assignee"]["name"] = sup.get("name", "")
                intent["task"]["assignee"]["phone"] = sup.get("phone", "")

    if action == "unknown":
        if any(k in low for k in ["tarea", "tareas", "pendientes", "crear", "eliminar", "editar", "ver"]):
            send_task_menu(user_phone)
            return True
        return False

    if action == "create":
        try:
            tk = intent.get("task", {}) or {}
            if not tk.get("title"):
                _open_flow(user_phone, "task_title_missing", {"intent": intent})
                send_whatsapp_message(user_phone, "🆕 ¿Cuál es el *título* de la tarea?")
                return True
            t = create_task_from_intent(intent)
            who = f" → 👤 {t.get('assignee_name')}" if t.get("assignee_name") else ""
            send_whatsapp_message(user_phone, f"✅ Tarea creada: [{t['id']}] {t['title']}{who} (vence: {t.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude crear la tarea: {e}")
        return True

    if action == "view":
        filters = intent.get("filters", {}) or {}
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
            header = "📅 Tareas para la *fecha*"
        _send_list(user_phone, tasks, header=header)
        return True

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
            who = f" → 👤 {t.get('assignee_name')}" if t.get("assignee_name") else ""
            send_whatsapp_message(user_phone, f"✏️ Tarea actualizada: [{t['id']}] {t['title']}{who} (vence: {t.get('due') or '—'})")
        except Exception as e:
            send_whatsapp_message(user_phone, f"⚠️ No pude editar: {e}")
        return True

    return False

# =========================
# RECORDATORIOS
# =========================
def _to_date(s: Optional[str]):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else None
    except Exception:
        return None

def _build_admin_message_for_today_and_overdue() -> str:
    """Admin: HOY + VENCIDAS (pendientes)."""
    today = datetime.now().date()
    all_tasks = _read_all().get("tasks", [])
    pending = []
    for t in all_tasks:
        if t.get("status") != "pendiente":
            continue
        d = _to_date(t.get("due"))
        if d and d <= today:
            pending.append(t)

    header = f"🧠 *Pendientes HOY + vencidas* ({today.isoformat()})"
    if not pending:
        return header + "\nNo tienes tareas pendientes para hoy. ✅"

    prio_rank = {"alta": 0, "media": 1, "baja": 2}
    pending.sort(key=lambda x: (x.get("due") or "9999-12-31", prio_rank.get(x.get("priority", "media"), 1)))

    lines = [header]
    for i, t in enumerate(pending, start=1):
        pr_emoji = {"alta":"🔴","media":"🟡","baja":"🟢"}.get(t.get("priority","media"),"🟡")
        due = t.get("due") or "—"
        who = f" • 👤 {t.get('assignee_name')}" if t.get("assignee_name") else ""
        lines.append(f"{i}. ⏳ {pr_emoji} [{t.get('id')}] {t.get('title')} 〰️ vence: {due}{who}")
    return "\n".join(lines)

def _build_supervisor_message(name: str, tasks: List[Dict[str, Any]], today_only: bool) -> Optional[str]:
    today = datetime.now().date()
    def is_relevant(t):
        if t.get("status") != "pendiente":
            return False
        if not today_only:
            return True
        d = _to_date(t.get("due"))
        return d is not None and d <= today  # hoy + vencidas

    relevant = [t for t in tasks if is_relevant(t)]
    header = f"📋 *Tareas pendientes* — {name}"
    sub = "(hoy + vencidas)" if today_only else "(todas)"
    header = f"{header} {sub}"

    if not relevant:
        return None

    prio_rank = {"alta": 0, "media": 1, "baja": 2}
    relevant.sort(key=lambda x: (x.get("due") or "9999-12-31", prio_rank.get(x.get("priority", "media"), 1)))

    lines = [header]
    for i, t in enumerate(relevant, start=1):
        pr_emoji = {"alta":"🔴","media":"🟡","baja":"🟢"}.get(t.get("priority","media"),"🟡")
        due = t.get("due") or "—"
        lines.append(f"{i}. ⏳ {pr_emoji} [{t.get('id')}] {t.get('title')} 〰️ vence: {due}")
    lines.append("\n✅ Responde cuando completes cada tarea. ¡Gracias!")
    return "\n".join(lines)

def run_pending_tasks_reminders(send_if_empty: bool = True, today_only: bool = True) -> None:
    """
    Envía recordatorios:
      - ADMIN: HOY + VENCIDAS de todas las tareas.
      - SUPERVISORES: sus tareas pendientes; si today_only=True → solo hoy + vencidas, si False → todas.
    """
    # --- Admin ---
    try:
        admin_phone = get_admin_phone()
    except Exception as e:
        print(f"⚠️ No se pudo obtener el teléfono del admin: {e}")
        admin_phone = None

    admin_msg = _build_admin_message_for_today_and_overdue()
    if admin_phone:
        if "No tienes tareas pendientes para hoy." in admin_msg and not send_if_empty:
            print("ℹ️ Admin sin pendientes; no se envía por configuración.")
        else:
            try:
                send_whatsapp_message(admin_phone, admin_msg)
                print(f"✅ Recordatorio ADMIN enviado a {admin_phone}")
            except Exception as e:
                print(f"❌ Error enviando recordatorio al admin: {e}")

    # --- Supervisores ---
    data = _read_all()
    all_tasks = data.get("tasks", [])

    # agrupar por teléfono del asignado
    buckets: Dict[str, Dict[str, Any]] = {}
    for t in all_tasks:
        phone = (t.get("assignee_phone_raw") or t.get("assignee_phone") or "").strip()
        if not phone or t.get("status") != "pendiente":
            continue
        if phone not in buckets:
            buckets[phone] = {
                "name": _pretty_display_name(t.get("assignee_name") or ""),
                "tasks": []
            }
        buckets[phone]["tasks"].append(t)

    if TASKS_DEBUG:
        print(f"📦 Supervisores con pendientes: {len(buckets)}")

    for phone, info in buckets.items():
        sup_name = info["name"] or "Supervisor"
        msg = _build_supervisor_message(sup_name, info["tasks"], today_only=today_only)
        if not msg:
            if TASKS_DEBUG:
                print(f"ℹ️ {phone} ({sup_name}) sin tareas relevantes para enviar (today_only={today_only}).")
            continue
        try:
            send_whatsapp_message(phone, msg)
            print(f"✅ Recordatorio SUPERVISOR enviado a {phone} ({sup_name})")
        except Exception as e:
            print(f"❌ Error enviando recordatorio a supervisor {phone}: {e}")

# =========================
# RECORDATORIO DIARIO SOLO ADMIN (legacy)
# =========================
def _build_pending_today_message() -> str:
    """Arma el mensaje con solo tareas pendientes de HOY para el admin (modo legacy)."""
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
        who = f" • 👤 {t.get('assignee_name')}" if t.get("assignee_name") else ""
        lines.append(f"{i}. ⏳ {pr_emoji} [{tid}] {title} 〰️ vence: {due}{who}")
    return "\n".join(lines)

def send_daily_pending_tasks_reminder(send_if_empty: bool = True) -> None:
    """
    (Legacy) Envía por WhatsApp al ADMIN el resumen de tareas PENDIENTES de HOY.
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

# Alias por compatibilidad
send_daily_pending_task_reminder = send_daily_pending_tasks_reminder

# =========================
# API PARA SUPERVISORES: vencidas y completadas
# =========================
def _to_date(s: Optional[str]):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date() if s else None
    except Exception:
        return None

def _is_task_assigned_to_phone(t: Dict[str, Any], phone: str) -> bool:
    """Verifica si la tarea t está asignada al teléfono (normalizado E.164 CO)."""
    if not phone:
        return False
    p = _normalize_phone(phone)
    p_raw = _normalize_phone(t.get("assignee_phone_raw", ""))
    p_norm = _normalize_phone(t.get("assignee_phone", ""))
    return p and (p == p_raw or p == p_norm)

def get_overdue_tasks_for_assignee(phone: str) -> List[Dict[str, Any]]:
    """
    Devuelve tareas PENDIENTES y VENCIDAS (due <= hoy) asignadas a phone.
    """
    data = _read_all()
    all_tasks = data.get("tasks", [])
    today = datetime.now().date()
    out = []
    for t in all_tasks:
        if t.get("status") != "pendiente":
            continue
        if not _is_task_assigned_to_phone(t, phone):
            continue
        d = _to_date(t.get("due"))
        if d and d <= today:
            out.append(t)
    # orden útil
    prio_rank = {"alta": 0, "media": 1, "baja": 2}
    out.sort(key=lambda x: (x.get("due") or "9999-12-31", prio_rank.get(x.get("priority", "media"), 1)))
    return out

def parse_task_ids_from_text(text: str) -> List[str]:
    """
    Extrae IDs desde texto. Soporta:
      - IDs entre corchetes: [abcd1234], [T20250814123456789]
      - IDs sueltos separados por coma/espacio
    """
    if not text:
        return []
    txt = (text or "").strip()
    pat = r"(?:[0-9a-f]{8}|T\d{17,})"
    ids = re.findall(r"\[(" + pat + r")\]", txt, flags=re.IGNORECASE)
    if not ids:
        ids = re.findall(pat, txt, flags=re.IGNORECASE)
    # normalizar y deduplicar preservando orden
    seen = set()
    out = []
    for i in ids:
        i2 = i.strip()
        if i2 not in seen:
            seen.add(i2)
            out.append(i2)
    return out

def complete_tasks_by_ids(ids: List[str], phone: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Marca como 'completada' las tareas con IDs dados.
    Si phone está definido, solo completa tareas asignadas a ese phone.
    Retorna la lista de tareas actualizadas.
    """
    if not ids:
        return []
    data = _read_all()
    tasks = data.get("tasks", [])
    idset = set(ids)
    updated = []
    for t in tasks:
        if t.get("id") in idset and t.get("status") == "pendiente":
            if phone and not _is_task_assigned_to_phone(t, phone):
                continue
            t["status"] = "completada"
            t["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated.append(t)
    if updated:
        _write_all({"tasks": tasks})
    return updated

