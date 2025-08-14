# workflows/supervision_questions.py
import os
import json
from datetime import datetime
from typing import Optional, List, Tuple

from services.whatsapp_service import send_whatsapp_message
import services.session_memory as memory  # 🧠 Diccionario compartido
from workflows.daily_report import get_admin_phone  # ✅ para notificación al admin

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

# 🔧 Crear carpetas si no existen
os.makedirs(RESPONSES_DIR, exist_ok=True)

# ——————————————————————————————————————————————
# Helpers de teléfono / archivos
# ——————————————————————————————————————————————
def _digits_only(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())

def _last10(digits: str) -> str:
    d = _digits_only(digits)
    return d[-10:] if len(d) >= 10 else d

def _canon_e164_co(phone: str) -> str:
    """
    Canoniza a formato E.164 Colombia: '57' + últimos 10 dígitos.
    Ejemplos:
      '+57 317 123 4567' -> '573171234567'
      '3171234567'       -> '573171234567'
      '573171234567'     -> '573171234567'
    """
    d = _digits_only(phone)
    tail10 = _last10(d)
    if not tail10:
        return d  # vacío o raro
    if d.startswith("57") and len(d) == 12:
        return d
    return "57" + tail10

def _session_filename_from_key(phone_key: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    return os.path.join(RESPONSES_DIR, f"{phone_key}_{when.strftime('%Y%m%d')}.json")

def _candidate_phone_keys(phone: str) -> List[str]:
    """
    Posibles claves de archivo para búsqueda directa.
    Primero la canónica (preferida), luego variantes.
    """
    d = _digits_only(phone)
    return list(dict.fromkeys([
        _canon_e164_co(phone),   # preferida
        d,                       # dígitos tal cual lleguen
        _last10(d),              # últimos 10
    ]))

def _load_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Error leyendo JSON {path}: {e}")
        return None

def _find_session_path_by_variants(phone: str) -> Tuple[Optional[str], Optional[dict]]:
    """
    Busca el archivo de sesión de HOY probando variantes y valida que
    session['phone'] == phone_key canónico del remitente.
    """
    today = datetime.now().strftime("%Y%m%d")
    phone_key = _canon_e164_co(phone)

    # 1) Intentos directos
    for key in _candidate_phone_keys(phone):
        path = os.path.join(RESPONSES_DIR, f"{key}_{today}.json")
        if os.path.exists(path):
            data = _load_json(path)
            if data and data.get("phone") == phone_key:
                print(f"📥 Sesión encontrada (match exacto): {os.path.basename(path)}")
                return path, data
            else:
                print(f"⚠️ Ignorada sesión por phone mismatch en {os.path.basename(path)}")

    # 2) Escaneo del día: aceptar solo si 'phone' coincide exactamente
    try:
        for fname in os.listdir(RESPONSES_DIR):
            if not fname.endswith(f"_{today}.json"):
                continue
            path = os.path.join(RESPONSES_DIR, fname)
            data = _load_json(path)
            if data and data.get("phone") == phone_key:
                print(f"📥 Sesión encontrada (escaneo validado): {fname}")
                return path, data
    except FileNotFoundError:
        pass

    return None, None

def _write_session_to_disk(phone_key: str, session: dict) -> None:
    fname = _session_filename_from_key(phone_key)
    try:
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
        print(f"💾 Sesión guardada: {os.path.basename(fname)} (step_index={session.get('step_index')})")
    except Exception as e:
        print(f"❌ Error guardando sesión {fname}: {e}")

def _realign_index_if_needed(session: dict) -> dict:
    """
    Si step_index quedó fuera de rango o igual a len(flow) pero aún faltan respuestas,
    lo realineamos a len(answers).
    """
    flow = session.get("flow") or []
    answers = session.get("answers") or {}
    idx = session.get("step_index", 0)

    # Restaurar flujo mínimo
    if not isinstance(flow, list) or len(flow) == 0:
        print("⚠️ Flow vacío/ inválido. Reseteando flujo e índice.")
        session["flow"] = QUESTIONS[:]
        session["step_index"] = 0
        session.setdefault("answers", {})
        session.setdefault("process", "SUPERVISION")
        return session

    n = len(flow)
    # idx inválido → corregir
    if not isinstance(idx, int) or idx < 0:
        idx = 0

    # Si aún faltan respuestas, alinear al conteo real de respuestas válidas
    answered_count = sum(1 for q in flow if q in answers and isinstance(answers[q], str) and answers[q].strip() != "")

    if idx > n:
        print(f"⚠️ step_index={idx} > len(flow)={n}. Alineando a {answered_count}.")
        idx = answered_count
    elif idx == n and answered_count < n:
        # Estaba “al final” pero faltaban respuestas → continuar
        print(f"ℹ️ step_index==len(flow) pero faltan respuestas ({answered_count}/{n}). Alineando índice.")
        idx = answered_count

    session["step_index"] = idx
    session.setdefault("answers", {})
    session.setdefault("process", "SUPERVISION")
    return session

def _sanitize_loaded_session(session: dict) -> dict:
    """Wrapper que aplica la realineación y asegura campos base."""
    session = _realign_index_if_needed(session or {})
    # Guardamos timestamp de última carga/ajuste para depurar
    session["last_loaded_at"] = datetime.now().isoformat()
    return session

# ——————————————————————————————————————————————
# Helpers de identificación y notificación
# ——————————————————————————————————————————————
def _get_supervisor_name_by_phone(phone_key: str) -> str:
    """
    Busca el nombre del usuario por teléfono canónico en users.json.
    Si no lo encuentra, retorna el phone_key como fallback.
    """
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for u in data.get("users", []):
            uphone = _canon_e164_co(u.get("phone", ""))
            if uphone == phone_key:
                return u.get("name", phone_key)
    except Exception as e:
        print(f"⚠️ No se pudo leer users.json para obtener nombre: {e}")
    return phone_key

def _notify_admin_completion(phone_key: str, name: Optional[str] = None) -> None:
    """
    Envía un WhatsApp al administrador cuando un supervisor termina el cuestionario.
    Incluye nombre y teléfono canónico.
    """
    try:
        admin_phone = get_admin_phone()
    except Exception as e:
        print(f"⚠️ No se pudo obtener el teléfono del admin: {e}")
        admin_phone = None

    if not admin_phone:
        print("ℹ️ Admin phone no configurado. Se omite notificación.")
        return

    sup_name = name or _get_supervisor_name_by_phone(phone_key)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = (
        f"✅ *Informe de supervisión completado*\n"
        f"👤 Supervisor: *{sup_name}*\n"
        f"📱 Teléfono: `{phone_key}`\n"
        f"🕒 {ts}"
    )
    try:
        send_whatsapp_message(admin_phone, msg)
        print(f"📨 Notificación enviada al admin por finalización de {sup_name}.")
    except Exception as e:
        print(f"❌ Error enviando notificación al admin: {e}")

# ——————————————————————————————————————————————
# API principal
# ——————————————————————————————————————————————
def ask_supervision_questions(phone: str, name: str):
    """
    Inicia en memoria y en disco la sesión de supervisión,
    y envía la primera pregunta al supervisor.
    """
    phone_key = _canon_e164_co(phone)  # 🔑 clave única y estable
    session_data = {
        "flow": QUESTIONS[:],
        "step_index": 0,
        "answers": {},
        "process": "SUPERVISION",
        "phone": phone_key,
        "name": name,  # ✅ guardamos el nombre para notificar al admin después
        "created_at": datetime.now().isoformat()
    }

    # Guardar en memoria y disco
    memory.sessions[phone_key] = session_data
    _write_session_to_disk(phone_key, session_data)

    # Primera pregunta
    send_whatsapp_message(
        phone,
        (
            f"📝 Hola *{name}*, vamos a diligenciar el informe de rutina de supervisión del día.\n\n"
            f"{QUESTIONS[0]}"
        )
    )
    print(f"🚀 Sesión iniciada para {phone_key} (flow={len(QUESTIONS)} preguntas)")

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
        if (user.get("role") or "").lower() == "supervisor":
            phone = user.get("phone")
            name = user.get("name")
            if phone and name:
                ask_supervision_questions(phone, name)

def load_supervision_session_if_exists(phone: str) -> bool:
    """
    Si existe un archivo de sesión para hoy del REMITENTE, lo carga en memoria y devuelve True.
    En caso contrario, False.
    """
    phone_key = _canon_e164_co(phone)
    path, ses = _find_session_path_by_variants(phone)
    if ses is not None:
        ses = _sanitize_loaded_session(ses)
        # Si la sesión (antigua) no trae nombre, lo buscamos por users.json
        ses.setdefault("name", _get_supervisor_name_by_phone(phone_key))
        memory.sessions[phone_key] = ses  # en memoria por clave canónica
        print(f"📦 Sesión activa en memoria para {phone_key} con step_index={ses['step_index']}")
        return True
    print(f"ℹ️ No se encontró sesión en disco para hoy con coincidencia exacta de phone={phone_key}.")
    return False

def handle_response(phone: str, message: str):
    """
    Procesa cada respuesta del supervisor, avanza en el flujo
    y guarda progreso en disco y memoria.
    """
    text = (message or "").strip()
    phone_key = _canon_e164_co(phone)

    # 🔁 Reinicio global con /start (en cualquier paso)
    if text.lower().startswith("/start"):
        # Intentamos mantener el nombre si ya existe
        existing_name = memory.sessions.get(phone_key, {}).get("name") if phone_key in memory.sessions else _get_supervisor_name_by_phone(phone_key)
        session = {
            "flow": QUESTIONS[:],
            "step_index": 0,
            "answers": {},
            "process": "SUPERVISION",
            "phone": phone_key,
            "name": existing_name,
            "created_at": datetime.now().isoformat()
        }
        memory.sessions[phone_key] = session
        _write_session_to_disk(phone_key, session)
        send_whatsapp_message(phone, QUESTIONS[0])
        print(f"🔄 Reinicio manual de sesión para {phone_key}")
        return

    # 1) Si no está en memoria, intenta cargar desde disco del REMITENTE
    if phone_key not in memory.sessions:
        if not load_supervision_session_if_exists(phone):
            send_whatsapp_message(
                phone,
                "⚠️ No encuentro una sesión activa. Escribe */start* para iniciar el informe de supervisión."
            )
            print(f"ℹ️ No había sesión en memoria ni en disco para {phone_key}.")
            return

    # 2) Sesión en memoria (sanitizada y realineada si hace falta)
    session = _sanitize_loaded_session(memory.sessions.get(phone_key, {}))
    flow = session["flow"]
    idx = session["step_index"]
    answers = session["answers"]
    sup_name = session.get("name") or _get_supervisor_name_by_phone(phone_key)

    print(f"🧭 [{phone_key}] step_index={idx} / {len(flow)} | respuestas={sum(1 for v in answers.values() if str(v).strip())}")

    # 3) Mensajes tipo saludo al inicio de idx=0 → repetir la primera pregunta
    first_like = {"/start", "start", "ok", "listo", "hola", "buenas", "buen día", "buen dia"}
    if idx == 0 and text.lower() in first_like:
        send_whatsapp_message(phone, flow[0])
        print(f"🔁 Repetida primera pregunta a {phone_key} (mensaje inicial no se guardó como respuesta).")
        return

    # 4) Si aún quedan preguntas por responder
    if idx < len(flow):
        current_q = flow[idx]
        session["answers"][current_q] = text
        session["step_index"] = idx + 1

        # Persistir
        memory.sessions[phone_key] = session
        _write_session_to_disk(phone_key, session)

        # Siguiente pregunta o finalización
        if session["step_index"] < len(flow):
            next_q = flow[session["step_index"]]
            send_whatsapp_message(phone, next_q)
            print(f"➡️ Enviada pregunta {session['step_index']}/{len(flow)} a {phone_key}")
        else:
            send_whatsapp_message(
                phone,
                "✅ ¡Gracias! El informe fue registrado correctamente. 📨"
            )
            print(f"🏁 Cuestionario completado por {phone_key}")
            # 🔔 Notificar al administrador
            _notify_admin_completion(phone_key, sup_name)
            # Limpiar memoria (el archivo queda en disco para el compilador)
            if phone_key in memory.sessions:
                del memory.sessions[phone_key]
        return

    # 5) Si idx >= len(flow): verificamos si hay inconsistencia y recuperamos
    if idx >= len(flow):
        remaining = [q for q in flow if q not in answers or not str(answers[q]).strip()]
        if remaining:
            # Recuperación: asignar respuesta a la primera pendiente y continuar
            next_q = remaining[0]
            session["answers"][next_q] = text
            # Recalcular índice al número de respondidas
            session["step_index"] = sum(1 for q in flow if q in session["answers"] and str(session["answers"][q]).strip())
            # Persistir y enviar la siguiente si aún queda
            memory.sessions[phone_key] = session
            _write_session_to_disk(phone_key, session)

            if session["step_index"] < len(flow):
                send_whatsapp_message(phone, flow[session["step_index"]])
                print(f"🔧 Recuperado índice y continuando con pregunta {session['step_index']}/{len(flow)} para {phone_key}")
            else:
                send_whatsapp_message(phone, "✅ ¡Gracias! El informe fue registrado correctamente. 📨")
                print(f"🏁 Cuestionario completado por {phone_key} (tras recuperación).")
                # 🔔 Notificar al administrador también en cierre por recuperación
                _notify_admin_completion(phone_key, sup_name)
                if phone_key in memory.sessions:
                    del memory.sessions[phone_key]
            return

        # Si de verdad no quedan preguntas pendientes:
        send_whatsapp_message(
            phone,
            "✅ Ya completaste todas las preguntas. Si deseas empezar de nuevo, escribe /start o espera el próximo cuestionario."
        )
        print(f"ℹ️ Usuario {phone_key} escribió tras completar el cuestionario.")
        return
