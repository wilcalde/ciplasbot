# workflows/supervision_questions.py
import os
import json
from datetime import datetime
from typing import Optional, List

from services.whatsapp_service import send_whatsapp_message
import services.session_memory as memory  # 🧠 Diccionario compartido

# 🔗 Tareas (para pregunta dinámica de vencidas)
from services.tasks_manager import (
    get_overdue_tasks_for_assignee,
    complete_tasks_by_ids,
    parse_task_ids_from_text,
)

# 🔔 Admin (teléfono desde users.json por daily_report)
from workflows.daily_report import get_admin_phone

# 📁 Rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.normpath(os.path.join(BASE_DIR, "../config"))
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
MIN_QUESTIONS = len(QUESTIONS)

# 🔧 Crear carpetas si no existen
os.makedirs(RESPONSES_DIR, exist_ok=True)

# ——————————————————————————————————————————————
# Helpers de teléfono / archivos / usuarios
# ——————————————————————————————————————————————
def _digits_only(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())

def _last10(digits: str) -> str:
    d = _digits_only(digits)
    return d[-10:] if len(d) >= 10 else d

def _canon_e164_co(phone: str) -> str:
    """
    Canoniza a formato E.164 Colombia: '57' + últimos 10 dígitos.
    """
    d = _digits_only(phone)
    if d.startswith("57") and len(d) == 12:
        return d
    tail10 = _last10(d)
    return "57" + tail10 if tail10 else d

def _same_person_phone(a: str, b: str) -> bool:
    return _last10(a) == _last10(b) and _last10(a) != ""

def _session_filename_from_key(phone_key: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    return os.path.join(RESPONSES_DIR, f"{phone_key}_{when.strftime('%Y%m%d')}.json")

def _candidate_phone_keys(phone: str) -> List[str]:
    d = _digits_only(phone)
    return list(dict.fromkeys([
        _canon_e164_co(phone),
        _last10(d),
        d
    ]))

def _find_session_path_by_variants(phone: str) -> Optional[str]:
    today = datetime.now().strftime("%Y%m%d")
    variants = _candidate_phone_keys(phone)
    for key in variants:
        path = os.path.join(RESPONSES_DIR, f"{key}_{today}.json")
        if os.path.exists(path):
            print(f"📥 Sesión encontrada por variante directa: {os.path.basename(path)}")
            return path

    tail10 = _last10(phone)
    if not tail10:
        return None

    try:
        for fname in os.listdir(RESPONSES_DIR):
            if not fname.endswith(f"_{today}.json"):
                continue
            base = fname.split("_")[0]
            if _last10(base) == tail10:
                path = os.path.join(RESPONSES_DIR, fname)
                print(f"📥 Sesión encontrada por coincidencia de últimos 10 dígitos: {fname}")
                return path
    except FileNotFoundError:
        pass

    return None

def _write_session_to_disk(phone_key: str, session: dict) -> None:
    fname = _session_filename_from_key(phone_key)
    with open(fname, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    print(f"💾 Sesión guardada: {os.path.basename(fname)}")

def _load_session_from_disk(phone: str) -> Optional[dict]:
    path = _find_session_path_by_variants(phone)
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            print(f"📦 Sesión cargada desde: {os.path.basename(path)}")
            return data
        except Exception as e:
            print(f"❌ Error leyendo sesión {path}: {e}")
    else:
        print("ℹ️ No se encontró sesión en disco para hoy con ninguna variante de teléfono.")
    return None

def _lookup_user_by_phone(phone_key: str) -> Optional[dict]:
    """
    Busca en users.json por teléfono (acepta e164 o últimos 10 dígitos).
    Retorna dict con 'name' y 'process' (o 'area' si existe).
    """
    try:
        if not os.path.exists(USERS_FILE):
            print(f"⚠️ USERS_FILE no existe: {USERS_FILE}")
            return None
        with open(USERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for u in data.get("users", []):
            uphone = str(u.get("phone", ""))
            if _same_person_phone(uphone, phone_key):
                return {
                    "name": u.get("name", "").strip() or None,
                    "process": (u.get("process") or u.get("area") or "SUPERVISION")
                }
    except Exception as e:
        print(f"⚠️ Error leyendo users.json: {e}")
    return None

# ——————————————————————————————————————————————
# Construcción dinámica de la pregunta de tareas vencidas
# ——————————————————————————————————————————————
def _build_tasks_question(phone: str) -> Optional[dict]:
    """
    Si el supervisor tiene tareas vencidas (pendientes), construye:
      - texto de la pregunta
      - lista de IDs candidatos
    """
    overdue = get_overdue_tasks_for_assignee(phone)
    if not overdue:
        return None

    lines = [
        "11. *Tareas asignadas vencidas*",
        "A continuación están tus tareas *pendientes y vencidas*:",
    ]
    for t in overdue:
        lines.append(f"• [{t.get('id')}] {t.get('title')} (vence: {t.get('due')})")

    lines.append(
        "\n✍️ *Indica cuáles completaste hoy*: responde con los *IDs* entre corchetes "
        "(o separados por coma). Escribe *ninguna* si no completaste ninguna."
    )

    return {
        "text": "\n".join(lines),
        "ids": [t.get("id") for t in overdue if t.get("id")]
    }

def _ensure_valid_flow(session: dict, phone_key: str) -> dict:
    """
    Garantiza que el flujo sea una lista con al menos las 10 preguntas base.
    Si no, lo reconstruye y (si aplica) agrega la pregunta dinámica de tareas vencidas.
    """
    flow = session.get("flow")
    needs_rebuild = (
        not isinstance(flow, list) or
        len(flow) < MIN_QUESTIONS or
        any(not isinstance(x, str) for x in (flow or []))
    )

    if needs_rebuild:
        print(f"🛠️ Reconstruyendo flow inválido. Antes: type={type(flow)}, len={len(flow) if isinstance(flow, list) else 'N/A'}")
        flow = QUESTIONS[:]
        tq = _build_tasks_question(phone_key)
        if tq:
            flow.append(tq["text"])
            session["tasks_question_index"] = len(flow) - 1
            session["tasks_completion_candidates"] = tq["ids"]
        session["flow"] = flow
        # Ajustar step_index a límites válidos
        idx = session.get("step_index", 0)
        if not isinstance(idx, int) or idx < 0:
            idx = 0
        if idx > len(flow):
            idx = len(flow)
        session["step_index"] = idx

    # Para depuración
    print(f"🔎 Flow verificado: len={len(session['flow'])}, step_index={session.get('step_index', 0)}")
    return session

# ——————————————————————————————————————————————
# Notificación al administrador al completar
# ——————————————————————————————————————————————
def _notify_admin_on_complete(session: dict) -> None:
    """
    Envía una notificación al administrador cuando el supervisor termina.
    """
    try:
        admin_phone = get_admin_phone()  # lee de users.json (rol=administrador)
    except Exception as e:
        print(f"⚠️ No fue posible obtener el teléfono del admin: {e}")
        return

    phone_key = session.get("phone") or ""
    # Nombre / proceso del supervisor
    sup_name = (session.get("name") or "").strip()
    sup_process = session.get("process") or "SUPERVISION"

    if not sup_name:
        info = _lookup_user_by_phone(phone_key)
        if info:
            sup_name = info.get("name") or sup_name
            sup_process = info.get("process") or sup_process

    answered = len(session.get("answers", {}))
    total = len(session.get("flow", []))
    completed_at = session.get("completed_at") or datetime.now().isoformat()

    # Adjunta el archivo de sesión (nombre), útil para el compilador
    ses_path = _find_session_path_by_variants(phone_key)
    ses_file = os.path.basename(ses_path) if ses_path else "N/A"

    msg = (
        "📣 *Informe de supervisión completado*\n"
        f"• Supervisor: *{sup_name or 'N/D'}*\n"
        f"• Proceso: *{sup_process}*\n"
        f"• Teléfono: +{phone_key}\n"
        f"• Respuestas: *{answered}/{total}*\n"
        f"• Hora fin: {completed_at}\n"
        f"• Archivo sesión: `{ses_file}`\n\n"
        "Puedes proceder a *compilar el resumen* a la hora establecida. ✅"
    )
    try:
        send_whatsapp_message(admin_phone, msg)
        print(f"📢 Notificación enviada al administrador ({admin_phone}) por {phone_key}")
    except Exception as e:
        print(f"❌ Error enviando notificación al administrador: {e}")

# ——————————————————————————————————————————————
# API principal
# ——————————————————————————————————————————————
def ask_supervision_questions(phone: str, name: str):
    """
    Inicia en memoria y en disco la sesión de supervisión,
    y envía la primera pregunta al supervisor.
    """
    phone_key = _canon_e164_co(phone)  # 🔑 clave única y estable

    # Flujo base + posible pregunta de tareas vencidas
    flow = QUESTIONS[:]
    tq = _build_tasks_question(phone_key)
    tasks_q_index = None
    if tq:
        flow.append(tq["text"])
        tasks_q_index = len(flow) - 1  # índice de la pregunta dinámica

    session_data = {
        "flow": flow,
        "step_index": 0,
        "answers": {},
        "process": "SUPERVISION",
        "phone": phone_key,
        "name": name,  # ⬅️ Guardamos nombre del supervisor
        "created_at": datetime.now().isoformat()
    }

    if tq:
        session_data["tasks_question_index"] = tasks_q_index
        session_data["tasks_completion_candidates"] = tq["ids"]

    # Guardar en memoria y disco
    memory.sessions[phone_key] = session_data
    _write_session_to_disk(phone_key, session_data)

    # Primera pregunta
    send_whatsapp_message(
        phone,
        (
            f"📝 Hola *{name}*, vamos a diligenciar el informe de rutina de supervisión del día.\n\n"
            f"{flow[0]}"
        )
    )
    print(f"🚀 Sesión iniciada para {phone_key} (tareas vencidas incluidas: {'sí' if tq else 'no'})")

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
    phone_key = _canon_e164_co(phone)
    ses = _load_session_from_disk(phone)
    if ses is not None:
        # Saneado + reconstrucción si hace falta
        ses = _ensure_valid_flow(ses, phone_key)

        # Asegurar que tenga nombre/proceso en sesión (útil para la notificación final)
        if not ses.get("name"):
            info = _lookup_user_by_phone(phone_key)
            if info and info.get("name"):
                ses["name"] = info["name"]
        if not ses.get("process"):
            info = _lookup_user_by_phone(phone_key)
            if info and info.get("process"):
                ses["process"] = info["process"]

        memory.sessions[phone_key] = ses  # siempre en memoria por clave canónica
        print(f"📦 Sesión activa en memoria para {phone_key} con step_index={ses['step_index']}")
        return True
    return False

def handle_response(phone: str, message: str):
    """
    Procesa cada respuesta del supervisor, avanza en el flujo
    y guarda progreso en disco y memoria. Al finalizar, notifica al admin.
    """
    text = (message or "").strip()
    phone_key = _canon_e164_co(phone)

    # 🔁 Reinicio global con /start (en cualquier paso)
    if text.lower().startswith("/start"):
        # Intentar recuperar nombre desde users.json
        info = _lookup_user_by_phone(phone_key) or {}
        session = {
            "flow": QUESTIONS[:],
            "step_index": 0,
            "answers": {},
            "process": "SUPERVISION",
            "phone": phone_key,
            "name": info.get("name") or "",  # ⬅️ mantener nombre si podemos
            "created_at": datetime.now().isoformat()
        }
        # Si hay tareas vencidas, añadir pregunta dinámica
        tq = _build_tasks_question(phone_key)
        if tq:
            session["flow"].append(tq["text"])
            session["tasks_question_index"] = len(session["flow"]) - 1
            session["tasks_completion_candidates"] = tq["ids"]

        memory.sessions[phone_key] = session
        _write_session_to_disk(phone_key, session)
        send_whatsapp_message(phone, session["flow"][0])
        print(f"🔄 Reinicio manual de sesión para {phone_key}")
        return

    # 1) Si no está en memoria, intenta cargar desde disco (robusto a formatos)
    if phone_key not in memory.sessions:
        if not load_supervision_session_if_exists(phone):
            send_whatsapp_message(
                phone,
                "⚠️ No encuentro una sesión activa. Escribe */start* para iniciar el informe de supervisión."
            )
            print(f"ℹ️ No había sesión en memoria ni en disco para {phone_key}.")
            return

    # 2) Sesión en memoria + garantizar flujo válido
    session = memory.sessions.get(phone_key, {})
    session = _ensure_valid_flow(session, phone_key)  # 🔒 asegura 10+ preguntas válidas
    flow = session["flow"]
    idx = session.get("step_index", 0)

    # 3) Mensajes tipo saludo al inicio de idx=0 → repetir la primera pregunta
    first_like = {"/start", "start", "ok", "listo", "hola", "buenas", "buen día", "buen dia", "buenos días", "buenos dias"}
    if idx == 0 and text.lower() in first_like:
        send_whatsapp_message(phone, flow[0])
        print(f"🔁 Repetida primera pregunta a {phone_key} (mensaje inicial no se guardó como respuesta).")
        return

    total = len(flow)

    # 4) Si aún quedan preguntas por responder
    if 0 <= idx < total:
        current_q = flow[idx]

        # ⛔ Idempotencia: si ya guardamos exactamente este texto para current_q, no avances
        prev = session.get("answers", {}).get(current_q)
        if prev is not None and prev.strip() == text:
            print(f"⏭️ Duplicado detectado para {phone_key} en Q{idx+1}; no se avanza.")
            return

        session["answers"][current_q] = text
        session["step_index"] = idx + 1

        # Persistir
        memory.sessions[phone_key] = session
        _write_session_to_disk(phone_key, session)

        # 🔔 Si esta pregunta fue la de tareas vencidas → procesar completadas
        tasks_q_idx = session.get("tasks_question_index", None)
        if tasks_q_idx is not None and idx == tasks_q_idx:
            allowed = set(session.get("tasks_completion_candidates", []) or [])
            answer = text.strip().lower()

            if answer not in {"ninguna", "ninguno", "no", "nada"}:
                ids = parse_task_ids_from_text(text)
                # filtrar a los que estaban en la lista
                ids = [i for i in ids if i in allowed]
                if ids:
                    updated = complete_tasks_by_ids(ids, phone=phone_key)
                    if updated:
                        listado = ", ".join(f"[{t.get('id')}] {t.get('title')}" for t in updated)
                        send_whatsapp_message(
                            phone,
                            f"✅ *Tareas marcadas como completadas*: {listado}"
                        )
                    else:
                        send_whatsapp_message(
                            phone,
                            "ℹ️ No pude marcar tareas con los IDs indicados (verifica que correspondan a tus tareas)."
                        )
                else:
                    send_whatsapp_message(
                        phone,
                        "ℹ️ No detecté IDs válidos de la lista. Si deseas, puedes responder con los IDs entre corchetes (por ejemplo: [abc12345], [def67890])."
                    )

        # Siguiente pregunta o finalización
        if session["step_index"] < total:
            next_q = flow[session["step_index"]]
            send_whatsapp_message(phone, next_q)
            print(f"➡️ Enviada pregunta {session['step_index']+1}/{total} a {phone_key}")
        else:
            # Marcar cierre y persistir
            session["completed_at"] = datetime.now().isoformat()
            memory.sessions[phone_key] = session
            _write_session_to_disk(phone_key, session)

            # Notificar al supervisor y al administrador
            send_whatsapp_message(
                phone,
                "✅ ¡Gracias! El informe fue registrado correctamente. 📨"
            )
            print(f"🏁 Cuestionario completado por {phone_key}")

            _notify_admin_on_complete(session)

            # Limpiar memoria (el archivo queda en disco para el compilador)
            if phone_key in memory.sessions:
                del memory.sessions[phone_key]
        return

    # 5) Si idx >= total (usuario escribe algo tras terminar)
    if idx >= total:
        send_whatsapp_message(
            phone,
            "✅ Ya completaste todas las preguntas. Si deseas empezar de nuevo, escribe */start*."
        )
        print(f"ℹ️ Usuario {phone_key} escribió tras completar el cuestionario.")
        return
