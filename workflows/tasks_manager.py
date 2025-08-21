# workflows/tasks_manager.py
import os
import json
import re
from datetime import datetime, date
from typing import Optional, Dict, Any, List, Tuple

from services.whatsapp_service import send_whatsapp_message

# Rutas base
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.normpath(os.path.join(BASE_DIR, "../config"))
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")
TASKS_FILE = os.path.join(CONFIG_DIR, "tasks.json")

# Estado en memoria del flujo de creación de tareas
# clave: telefono_admin_normalizado -> {"step": int, "draft": {...}, "choices": {...}}
TASK_SESSIONS: Dict[str, Dict[str, Any]] = {}

# ─────────────────────────────────────────────────────────────────────────────
# Normalización de teléfonos (E.164 CO) y helpers
# ─────────────────────────────────────────────────────────────────────────────

def _digits_only(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())

def _last10(d: str) -> str:
    d2 = _digits_only(d)
    return d2[-10:] if len(d2) >= 10 else d2

def _canon_e164_co(phone: str) -> str:
    """
    E.164 Colombia: 57 + últimos 10 dígitos. Si ya viene 57XXXXXXXXXX, se respeta.
    """
    d = _digits_only(phone)
    if d.startswith("57") and len(d) == 12:
        return d
    tail = _last10(d)
    return ("57" + tail) if tail else d

def _normalize_phone(phone: str) -> str:
    """Compat alias: normaliza a E.164 CO."""
    return _canon_e164_co(phone)

# ─────────────────────────────────────────────────────────────────────────────
# Persistencia
# ─────────────────────────────────────────────────────────────────────────────

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

def _load_users() -> List[dict]:
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("users", [])

# ─────────────────────────────────────────────────────────────────────────────
# Seguridad / Roles
# ─────────────────────────────────────────────────────────────────────────────

def _is_admin(phone: str) -> bool:
    phone_norm = _normalize_phone(phone)
    for u in _load_users():
        up = _normalize_phone(u.get("phone", ""))
        role = (u.get("role") or "").strip().lower()
        if up == phone_norm and role in {"administrador", "admin"}:
            return True
    return False

def _is_supervisor(phone: str) -> bool:
    phone_norm = _normalize_phone(phone)
    for u in _load_users():
        up = _normalize_phone(u.get("phone", ""))
        role = (u.get("role") or "").strip().lower()
        if up == phone_norm and role == "supervisor":
            return True
    return False

# ─────────────────────────────────────────────────────────────────────────────
# Utilidades de tareas / fechas
# ─────────────────────────────────────────────────────────────────────────────

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

def _today_iso() -> str:
    return date.today().isoformat()

# ─────────────────────────────────────────────────────────────────────────────
# Selección de supervisor (para asignación)
# ─────────────────────────────────────────────────────────────────────────────

def _list_supervisors() -> List[dict]:
    sup = []
    for u in _load_users():
        if (u.get("role") or "").strip().lower() == "supervisor":
            sup.append(u)
    return sup

def _format_supervisors_menu() -> str:
    sups = _list_supervisors()
    if not sups:
        return "⚠️ No hay supervisores configurados en el sistema."
    lines = ["👤 *Elige el supervisor* (responde con *número*, *nombre* o *teléfono*):"]
    for i, u in enumerate(sups, start=1):
        lines.append(f"{i}. {u.get('name','(sin nombre)')} — {u.get('phone','')}")
    return "\n".join(lines)

def _match_supervisor(answer: str) -> Optional[dict]:
    """Permite elegir por índice (1..n), por nombre (contiene) o por teléfono."""
    answer = (answer or "").strip()
    sups = _list_supervisors()
    if not sups:
        return None

    # 1) Índice
    if answer.isdigit():
        idx = int(answer)
        if 1 <= idx <= len(sups):
            return sups[idx - 1]

    # 2) Teléfono
    ans_phone = _normalize_phone(answer)
    for u in sups:
        if _normalize_phone(u.get("phone", "")) == ans_phone and ans_phone:
            return u

    # 3) Nombre contiene (case-insensitive)
    ans_low = answer.lower()
    candidates = [u for u in sups if ans_low in (u.get("name","").lower())]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # ambigüo: devuelve None para que el flujo pida ser más específico
        return None

    return None

# ─────────────────────────────────────────────────────────────────────────────
# Notificaciones
# ─────────────────────────────────────────────────────────────────────────────

def _notify_supervisor_new_task(task: dict):
    """
    Envia WhatsApp al supervisor asignado cuando se crea una tarea.
    """
    phone = task.get("assignee_phone_raw") or task.get("assignee_phone")  # preferimos el raw original por pasarela
    if not phone:
        return
    msg = (
        "🆕 *Nueva tarea asignada*\n\n"
        f"• *Nombre:* {task.get('name','')}\n"
        f"• *Vence:* {task.get('due_date','—')}\n"
        f"• *Prioridad:* {task.get('priority','')}\n"
        f"• *Proceso:* {task.get('process','')}\n"
        f"• *ID:* {task.get('id','')}\n\n"
        "Por favor, revísala y ejecútala según prioridad. ✅"
    )
    try:
        send_whatsapp_message(phone, msg)
    except Exception as e:
        print(f"⚠️ Error notificando al supervisor ({phone}): {e}")

def send_daily_pending_tasks_for_supervisors(today_only: bool = True):
    """
    Envía a *cada supervisor* su resumen de *tareas pendientes*.
    - today_only=True → solo las que *vencen hoy* o *vencidas*.
    - today_only=False → todas las pendientes.
    """
    tasks = _load_tasks()
    sups = _list_supervisors()
    if not sups:
        print("ℹ️ No hay supervisores configurados para notificar tareas pendientes.")
        return

    today = _today_iso()

    def to_date(s: Optional[str]) -> Optional[date]:
        try:
            return datetime.strptime(s or "", "%Y-%m-%d").date()
        except Exception:
            return None

    for u in sups:
        sup_name = u.get("name","(sin nombre)")
        sup_phone_raw = u.get("phone","")
        sup_phone_key = _normalize_phone(sup_phone_raw)

        # Filtrar tareas del supervisor
        my_tasks = [t for t in tasks
                    if _normalize_phone(t.get("assignee_phone","")) == sup_phone_key
                    and (t.get("status","pendiente").lower() == "pendiente")]

        if today_only:
            # Solo hoy (vencen hoy) o vencidas
            my_tasks = [t for t in my_tasks
                        if (to_date(t.get("due_date")) is None) or (to_date(t.get("due_date")) <= to_date(today))]
        if not my_tasks:
            # Puedes silenciar envíos vacíos si prefieres
            continue

        # Orden: vencidas primero, luego por due_date asc
        def sort_key(t: dict):
            d = to_date(t.get("due_date"))
            return (d or date(2099,12,31))
        my_tasks.sort(key=sort_key)

        lines = [
            f"📋 *Tareas pendientes* — {sup_name}",
        ]
        for i, t in enumerate(my_tasks, start=1):
            due = t.get("due_date","—")
            pr = t.get("priority","")
            nm = t.get("name","")
            pid = t.get("id","")
            proc = t.get("process","")
            lines.append(f"{i}. {nm} 〰️ vence: {due} • {pr} • {proc} • ID: {pid}")

        try:
            send_whatsapp_message(sup_phone_raw, "\n".join(lines))
            print(f"✅ Notificado pendientes a {sup_name} ({sup_phone_raw})")
        except Exception as e:
            print(f"❌ Error enviando pendientes a {sup_name}: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Flujo guiado de creación (SOLO ADMIN)
# ─────────────────────────────────────────────────────────────────────────────

def start_new_task_flow(phone: str):
    """
    Inicia el flujo de creación de tarea (solo administrador).
    Pasos:
      1) Nombre
      2) Fecha límite
      3) Prioridad
      4) Supervisor asignado  ← NUEVO
      5) Proceso
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
            "status": "pendiente",
            "assignee_phone": "",
            "assignee_phone_raw": "",
            "assignee_name": "",
        },
        "choices": {}
    }
    send_whatsapp_message(
        phone,
        "📝 *Nueva tarea* — Paso 1/5\n\n1) *Nombre de la tarea*:\nEscribe un título corto y claro."
    )

def handle_task_flow_response(phone: str, message: str):
    """
    Maneja cada respuesta del flujo de creación de tarea.
    (Solo procesa si el flujo está activo y lo inició un admin).
    """
    phone_norm = _normalize_phone(phone)
    session = TASK_SESSIONS.get(phone_norm)
    if not session:
        # No hay flujo activo; ignorar en este manejador.
        return

    # Re-validar que quien responde siga siendo admin
    if not _is_admin(phone):
        send_whatsapp_message(phone, "⛔ Solo el administrador puede continuar con la creación de tareas.")
        TASK_SESSIONS.pop(phone_norm, None)
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
            "📅 *Nueva tarea* — Paso 2/5\n\n2) *Fecha límite (vencimiento)*:\n"
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
            "⏫ *Nueva tarea* — Paso 3/5\n\n3) *Prioridad* (Alta, Media, Baja):"
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
        # Mostrar menú de supervisores
        menu = _format_supervisors_menu()
        send_whatsapp_message(phone, f"👥 *Nueva tarea* — Paso 4/5\n\n{menu}")
        return

    # Paso 4: Supervisor
    if step == 4:
        chosen = _match_supervisor(txt)
        if not chosen:
            send_whatsapp_message(phone, "⚠️ No pude identificar al supervisor. Responde con *número*, *nombre* o *teléfono* tal como aparece en la lista.\n\n" + _format_supervisors_menu())
            return

        session["draft"]["assignee_name"] = chosen.get("name","")
        session["draft"]["assignee_phone_raw"] = chosen.get("phone","")
        session["draft"]["assignee_phone"] = _normalize_phone(chosen.get("phone",""))

        session["step"] = 5
        send_whatsapp_message(
            phone,
            "🏭 *Nueva tarea* — Paso 5/5\n\n5) *Proceso* (ej.: Costura, Fileteado, Cuerdas, RTR, etc.):"
        )
        return

    # Paso 5: Proceso → guardar
    if step == 5:
        if len(txt) < 2:
            send_whatsapp_message(phone, "⚠️ Proceso demasiado corto. Intenta nuevamente.")
            return
        session["draft"]["process"] = txt

        # Guardar tarea
        tasks = _load_tasks()
        new_task = {
            "id": _new_task_id(),
            "name": session["draft"]["name"],
            "due_date": session["draft"]["due_date"],  # ISO YYYY-MM-DD
            "priority": session["draft"]["priority"],
            "process": session["draft"]["process"],
            "status": "pendiente",
            "assignee_phone": session["draft"]["assignee_phone"],
            "assignee_phone_raw": session["draft"]["assignee_phone_raw"],  # se usa para enviar por WhatsApp
            "assignee_name": session["draft"]["assignee_name"],
            "created_at": datetime.now().isoformat(),
            "created_by_phone": _normalize_phone(phone),
            "created_by": "",  # intentaremos completar abajo
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

        # Confirmación al admin
        send_whatsapp_message(
            phone,
            "✅ *Tarea creada correctamente*\n\n"
            f"• *Nombre:* {new_task['name']}\n"
            f"• *Vence:* {new_task['due_date']}\n"
            f"• *Prioridad:* {new_task['priority']}\n"
            f"• *Proceso:* {new_task['process']}\n"
            f"• *Asignado a:* {new_task['assignee_name']} ({new_task['assignee_phone_raw']})\n"
            f"• *ID:* {new_task['id']}"
        )

        # Notificar al supervisor
        _notify_supervisor_new_task(new_task)
        return

# ─────────────────────────────────────────────────────────────────────────────
# Operaciones de administración: editar / eliminar (SOLO ADMIN)
# ─────────────────────────────────────────────────────────────────────────────

def admin_delete_task(requester_phone: str, task_id: str) -> bool:
    """
    Elimina una tarea por ID. Solo admin.
    Retorna True si eliminó algo.
    """
    if not _is_admin(requester_phone):
        send_whatsapp_message(requester_phone, "⛔ Solo el administrador puede eliminar tareas.")
        return False

    tasks = _load_tasks()
    keep = [t for t in tasks if t.get("id") != task_id]
    if len(keep) == len(tasks):
        send_whatsapp_message(requester_phone, f"⚠️ No encontré una tarea con ID {task_id}.")
        return False

    _save_tasks(keep)
    send_whatsapp_message(requester_phone, f"🗑️ Tarea *{task_id}* eliminada.")
    return True

def admin_edit_task(requester_phone: str, task_id: str, **updates) -> Optional[dict]:
    """
    Edita campos de una tarea (solo admin).
    Campos permitidos: name, due_date, priority (Alta/Media/Baja), process, status (pendiente/completada),
                       assignee_phone (o assignee por nombre/teléfono usando assignee_hint)
    """
    if not _is_admin(requester_phone):
        send_whatsapp_message(requester_phone, "⛔ Solo el administrador puede modificar tareas.")
        return None

    tasks = _load_tasks()
    target = None
    for t in tasks:
        if t.get("id") == task_id:
            target = t
            break

    if not target:
        send_whatsapp_message(requester_phone, f"⚠️ No encontré una tarea con ID {task_id}.")
        return None

    # Actualizaciones validadas
    if "name" in updates and isinstance(updates["name"], str) and updates["name"].strip():
        target["name"] = updates["name"].strip()

    if "due_date" in updates and updates["due_date"]:
        iso = _parse_date_any(str(updates["due_date"]))
        if iso:
            target["due_date"] = iso

    if "priority" in updates and updates["priority"]:
        p = str(updates["priority"]).strip().lower()
        mapping = {"alta": "Alta", "media": "Media", "baja": "Baja"}
        if p in mapping:
            target["priority"] = mapping[p]

    if "process" in updates and isinstance(updates["process"], str):
        target["process"] = updates["process"].strip()

    if "status" in updates and updates["status"]:
        st = str(updates["status"]).strip().lower()
        if st in {"pendiente", "completada"}:
            target["status"] = st

    # Reasignación opcional por hint: nombre / teléfono
    assignee_hint = updates.get("assignee_hint")
    if assignee_hint:
        sup = _match_supervisor(str(assignee_hint))
        if sup:
            target["assignee_name"] = sup.get("name","")
            target["assignee_phone_raw"] = sup.get("phone","")
            target["assignee_phone"] = _normalize_phone(sup.get("phone",""))

    _save_tasks(tasks)

    send_whatsapp_message(
        requester_phone,
        "✏️ *Tarea actualizada*\n\n"
        f"• *Nombre:* {target.get('name')}\n"
        f"• *Vence:* {target.get('due_date')}\n"
        f"• *Prioridad:* {target.get('priority')}\n"
        f"• *Proceso:* {target.get('process')}\n"
        f"• *Estado:* {target.get('status')}\n"
        f"• *Asignado a:* {target.get('assignee_name')} ({target.get('assignee_phone_raw')})\n"
        f"• *ID:* {target.get('id')}"
    )

    return target

# ─────────────────────────────────────────────────────────────────────────────
# Consulta de tareas (para admin o supervisor)
# ─────────────────────────────────────────────────────────────────────────────

def list_my_tasks(phone: str, pending_only: bool = True) -> List[dict]:
    """
    Devuelve tareas asignadas al remitente (si es supervisor).
    Si es admin, devuelve todas (filtrables por pending_only).
    """
    tasks = _load_tasks()
    phone_key = _normalize_phone(phone)
    if _is_admin(phone):
        out = tasks
    else:
        out = [t for t in tasks if _normalize_phone(t.get("assignee_phone","")) == phone_key]

    if pending_only:
        out = [t for t in out if (t.get("status","pendiente").lower() == "pendiente")]
    # Orden por due_date asc
    def to_date(s):
        try:
            return datetime.strptime(s or "", "%Y-%m-%d").date()
        except Exception:
            return date(2099,12,31)
    out.sort(key=lambda x: to_date(x.get("due_date")))
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Utilidad para saber si hay flujo activo (UI externa)
# ─────────────────────────────────────────────────────────────────────────────

def has_active_task_flow(phone: str) -> bool:
    return _normalize_phone(phone) in TASK_SESSIONS
