from fastapi import FastAPI
from pydantic import BaseModel
from services.prompts import get_prompt, get_flow
from services.whatsapp_service import send_whatsapp_message
from services.session_memory import sessions, CONFIG_DIR, SUPERVISORS_FILE
import json
import os
import re
import requests
import datetime
import pytz
from openai import OpenAI

# Flujos de supervisión
from workflows.fileteado_report import handle_fileteado_message
from workflows.fileteado_efficiency_report import handle_fileteado_efficiency_request
from workflows.daily_report import update_alert_status, check_alert_already_sent, get_admin_phone
from workflows.supervision_questions import (
    handle_response as supervision_handle_response,
    load_supervision_session_if_exists,
    send_supervision_questions,
    ask_supervision_questions,  # ← acepta source="manual" o "scheduler"
)

# Gestor NLU de tareas
from services.tasks_manager import (
    handle_followup,
    maybe_handle_task_message,
    send_task_menu
)

# Ventana WhatsApp 24h
from services.wa_window_manager import record_inbound, canon_phone_e164_co

# Informe de desempeño individual (operario) RTR clásico
from workflows.performance_report import handle_performance_report_request

# NUEVO: Informe de desempeño Impresión Gráfica
from workflows.performance_report_imprgraf import handle_performance_report_request_imprgraf

# Reporte de COSTURA
from workflows.costura_report import handle_costura_message

# Informe Gerencial de Línea (admin)
from workflows.admin_line_report import (
    handle_admin_line_report_request,
    claims_admin_line_report_command,
    maybe_handle_admin_line_report_followup,  # <<< IMPORTANTE
)

app = FastAPI()

# Directorio de tareas (opcional)
TASKS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks")
os.makedirs(CONFIG_DIR, exist_ok=True)

WEBHOOK_URL = "https://hook.make.com/tu_webhook"  # reemplaza con tu URL real
TZ = "America/Bogota"

# Cliente OpenAI
client = OpenAI()

# === NUEVO: rutas de operadores autorizados para informes ===
OPERATORS_FILE = os.path.join(CONFIG_DIR, "operators.json")
OPERATORS_IMPR_GRAF_FILE = os.path.join(CONFIG_DIR, "operators_impr_graf.json")


# =========================
# Utilidades
# =========================
def normalize_phone(p: str) -> str:
    if not p:
        return ""
    return re.sub(r"\D", "", p)

def _phones_from_json_array(arr) -> set[str]:
    phones = set()
    for item in arr or []:
        if isinstance(item, dict):
            u_raw = item.get("phone_e164") or item.get("phone") or ""
        else:
            u_raw = str(item)
        u_digits = normalize_phone(u_raw)
        if not u_digits:
            continue
        u_e164 = normalize_phone(canon_phone_e164_co(u_digits) or u_digits)
        phones.add(u_digits)
        phones.add(u_e164)
    return phones

def is_whitelisted_phone(phone_raw: str) -> bool:
    """Autoriza por unión de:
       - SUPERVISORS_FILE (users/supervisors/admins…)
       - operators.json (RTR)
       - operators_impr_graf.json (IMPRGRAF)
    """
    in_digits = normalize_phone(phone_raw or "")
    in_e164 = normalize_phone(canon_phone_e164_co(in_digits) or in_digits)

    # 1) Supervisores / Admins
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users_data = json.load(f)
    except Exception as e:
        print(f"⚠️ No se pudo leer SUPERVISORS_FILE para whitelist: {e}")
        users_data = {}

    pools = []
    pools += users_data.get("users", [])
    pools += users_data.get("supervisors", [])
    pools += users_data.get("administradores", [])
    pools += users_data.get("admins", [])

    sup_phones = _phones_from_json_array(pools)

    for key in ("admin_phone", "owner_phone", "gerente_phone"):
        u_digits = normalize_phone(str(users_data.get(key, "")))
        if u_digits:
            u_e164 = normalize_phone(canon_phone_e164_co(u_digits) or u_digits)
            sup_phones.add(u_digits)
            sup_phones.add(u_e164)

    if in_digits in sup_phones or in_e164 in sup_phones:
        return True

    # 2) Operadores RTR
    try:
        with open(OPERATORS_FILE, encoding="utf-8") as f:
            ops_data = json.load(f)
        ops_phones = _phones_from_json_array(ops_data.get("operators", []))
        if in_digits in ops_phones or in_e164 in ops_phones:
            return True
    except Exception:
        pass

    # 3) Operadores Impresión Gráfica
    try:
        with open(OPERATORS_IMPR_GRAF_FILE, encoding="utf-8") as f:
            ops_graf_data = json.load(f)
        ops_graf_phones = _phones_from_json_array(ops_graf_data.get("operators", []))
        if in_digits in ops_graf_phones or in_e164 in ops_graf_phones:
            return True
    except Exception:
        pass

    return False

def today_str():
    return datetime.datetime.now(pytz.timezone(TZ)).strftime("%Y-%m-%d")

def get_user_name_by_phone(phone_digits: str) -> str:
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users_data = json.load(f)
        pools = []
        pools += users_data.get("users", [])
        pools += users_data.get("supervisors", [])
        pools += users_data.get("administradores", [])
        pools += users_data.get("admins", [])
        for user in pools:
            if isinstance(user, dict):
                u_phone = normalize_phone(user.get("phone", "")) or normalize_phone(user.get("phone_e164", ""))
                if u_phone == normalize_phone(phone_digits) or canon_phone_e164_co(u_phone) == normalize_phone(phone_digits):
                    return user.get("name") or user.get("nombre") or "usuario"
    except Exception as e:
        print(f"⚠️ No se pudo leer SUPERVISORS_FILE para nombre: {e}")
    return "usuario"

def _legacy_tasks_today(nombre: str) -> str:
    try:
        archivo_tareas = os.path.join(TASKS_DIR, f"{nombre.lower()}.json")
        if os.path.exists(archivo_tareas):
            with open(archivo_tareas, encoding="utf-8") as f:
                tareas = json.load(f)
            hoy = today_str()
            lista = tareas.get(hoy)
            if lista:
                lineas = "\n".join([f"✅ {t}" for t in lista])
                return f"📋 *Tareas asignadas para hoy:*\n{lineas}"
        return "📭 Hoy no tienes tareas asignadas."
    except Exception as e:
        print(f"⚠️ Error leyendo tareas legacy: {e}")
        return "⚠️ No pude leer tus tareas por ahora."

def respuesta_inteligente(texto: str, nombre: str, numero: str) -> str:
    texto_low = (texto or "").strip().lower()
    if texto_low in ("tareas", "tareas del día", "tareas del dia"):
        return _legacy_tasks_today(nombre)

    prompt_sistema = (
        f"Eres un asistente virtual llamado CiplasBot. Tu tarea es responder con amabilidad y precisión al usuario {nombre}, "
        "quien trabaja en la planta de producción de Ciplas S.A.S. No tienes flujo activo en este momento, "
        "pero puedes responder preguntas generales sobre procesos, producción, dudas comunes o instrucciones simples. "
        "Si no sabes la respuesta, responde con: 'Lo siento, por ahora no tengo información sobre ese tema. "
        "Puedes escribir \"ayuda\" para ver opciones disponibles.'"
    )
    try:
        chat = client.chat.completions.create(
            model="o4-mini",
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": texto}
            ]
        )
        return chat.choices[0].message.content.strip()
    except Exception as e:
        print("❌ Error al generar respuesta con OpenAI:", e)
        return "Lo siento, estoy teniendo dificultades para procesar tu mensaje. Intenta más tarde."

# ========= helpers de sesión/flujo =========
def _session_file(phone_key: str) -> str:
    return os.path.join(CONFIG_DIR, f"{phone_key}_session.json")

def _is_today_created(session_obj: dict, path: str) -> bool:
    try:
        created_at = session_obj.get("created_at", "")
        if created_at:
            return created_at[:10] == today_str()
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(path), tz=pytz.timezone(TZ))
        return mtime.strftime("%Y-%m-%d") == today_str()
    except Exception:
        return True

def purge_stale_sessions(phone_key: str):
    s = sessions.setdefault(phone_key, {})
    af = s.get("active_flow")
    path = _session_file(phone_key)
    legacy_exists = os.path.exists(path)
    legacy_ok = False
    legacy_done = False
    if legacy_exists:
        try:
            with open(path, encoding="utf-8") as f:
                ses = json.load(f)
            flow = ses.get("flow", [])
            step_index = ses.get("step_index", 0)
            legacy_ok = _is_today_created(ses, path)
            legacy_done = bool(flow) and step_index >= len(flow)
        except Exception as e:
            print(f"⚠️ Error leyendo sesión legacy para purge: {e}")
    if af == "NOVEDADES" and (not legacy_exists or not legacy_ok or legacy_done):
        s["active_flow"] = None

def ensure_active_flow(phone_key: str):
    s = sessions.setdefault(phone_key, {})
    if s.get("active_flow"):
        return
    path = _session_file(phone_key)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                ses = json.load(f)
            flow = ses.get("flow", [])
            step_index = ses.get("step_index", 0)
            if flow and step_index < len(flow) and _is_today_created(ses, path):
                s["active_flow"] = "NOVEDADES"
        except Exception as e:
            print(f"⚠️ No se pudo asegurar active_flow desde sesión legacy: {e}")

def start_novedades_now(phone_key: str):
    path = _session_file(phone_key)
    if not os.path.exists(path):
        send_whatsapp_message(
            phone_key,
            "⚠️ No encontré un formulario de *Novedades* abierto para hoy. "
            "Espera el mensaje automático o contacta al administrador."
        )
        return
    try:
        with open(path, encoding="utf-8") as f:
            ses = json.load(f)
        if not _is_today_created(ses, path):
            send_whatsapp_message(
                phone_key,
                "⚠️ El formulario de *Novedades* disponible no corresponde al día de hoy."
            )
            return
        ses["answers"] = {}
        ses["step_index"] = 0
        sessions[phone_key] = ses
        sessions[phone_key]["active_flow"] = "NOVEDADES"
        first_prompt = get_prompt(ses["flow"][0], ses["process"])
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ses, f, indent=2, ensure_ascii=False)
        send_whatsapp_message(phone_key, first_prompt)
    except Exception as e:
        print(f"❌ Error al reiniciar Novedades: {e}")
        send_whatsapp_message(phone_key, "❌ No pude reiniciar el formulario de *Novedades*.")

# ========= Fin helpers =========

class WhatsAppMessage(BaseModel):
    phone: str
    message: str | None = None

@app.post("/ciplasbot")
async def handle_ciplasbot(payload: WhatsAppMessage):
    # Normaliza
    numero = normalize_phone(payload.phone)
    phone_key = canon_phone_e164_co(numero)

    texto = (payload.message or "").strip()
    texto_low = texto.lower()

    # Registrar inbound (solo whitelisted)
    try:
        if is_whitelisted_phone(numero):
            record_inbound(numero)
        else:
            print(f"ℹ️ Inbound ignorado para {numero}: no está en user.json/operators*.json.")
    except Exception as e:
        print(f"⚠️ Error al evaluar/registrar inbound para {numero}: {e}")

    # Si no hay texto, pedimos texto y salimos
    if not isinstance(texto, str) or texto == "":
        try:
            send_whatsapp_message(
                phone_key,
                "🙏 Para registrar tu respuesta necesito un *mensaje de texto*. ¿Puedes escribirlo, por favor?"
            )
        except Exception as e:
            print(f"❌ Error enviando aviso de texto requerido a {phone_key}: {e}")
        return {"status": "ok", "detail": "no text content; user prompted"}

    # =========================================================
    # 0) INTERCEPTOR ULTRA-TEMPRANO: FECHA/RANGO para Informe Gerencial
    #    (prueba con las 3 variantes del número para evitar desajustes)
    # =========================================================
    try:
        print("🧭 [admin_line] main: intentando follow-up temprano…")
        if (maybe_handle_admin_line_report_followup(payload.phone, texto) or
            maybe_handle_admin_line_report_followup(numero, texto) or
            maybe_handle_admin_line_report_followup(phone_key, texto)):
            print("🧭 [admin_line] main: follow-up capturado y manejado ✅")
            return {"status": "ok", "detail": "admin_line_report_followup_handled"}
        else:
            print("🧭 [admin_line] main: no había follow-up pendiente o no había fecha.")
    except Exception as e:
        print(f"❌ Error en admin_line_report follow-up (main): {e}")

    # =========================================================
    # 1) Comandos explícitos de TAREAS (vista directa)
    # =========================================================
    if texto_low in (
        "ver tareas", "ver tareas hoy", "ver tareas del dia", "ver tareas del día",
        "tareas pendientes", "tareas pendientes hoy", "ver mis tareas"
    ):
        nombre_usr = get_user_name_by_phone(phone_key)
        try:
            if maybe_handle_task_message(texto, nombre_usr, phone_key):
                return {"status": "ok", "detail": "task_message_list_handled"}
        except Exception as e:
            print(f"⚠️ NLU de tareas no respondió, uso fallback: {e}")
        reply = _legacy_tasks_today(nombre_usr)
        try:
            send_whatsapp_message(phone_key, reply)
        except Exception as e:
            print(f"❌ Error enviando lista de tareas (fallback) a {phone_key}: {e}")
        return {"status": "ok", "detail": "task_list_fallback_sent"}

    # Menú de tareas
    if texto_low.startswith("/nueva_tarea") or texto_low in ("/tareas", "menu tareas", "gestión de tareas", "gestionar tareas"):
        try:
            send_task_menu(phone_key)
        except Exception as e:
            print(f"❌ Error enviando menú de tareas: {e}")
        return {"status": "ok", "detail": "task_menu_sent"}

    # =========================================================
    # 2) FAST-PATH: Informe Gerencial con prefijo ("reporte linea …")
    # =========================================================
    try:
        if claims_admin_line_report_command(texto):
            handled_admin_line = handle_admin_line_report_request(phone_key, texto)
            if handled_admin_line:
                return {"status": "ok", "detail": "admin_line_report_handled_fastpath"}
    except Exception as e:
        print(f"❌ Error en admin_line_report fast-path: {e}")

    # =========================================================
    # 3) Seguimiento de TAREAS abierto
    # =========================================================
    if handle_followup(texto, phone_key):
        return {"status": "ok", "detail": "task_followup_handled"}

    # =========================================================
    # 4) INFORMES específicos (antes del NLU genérico)
    # =========================================================
    # 4.1) NUEVO: Informe de desempeño IMPRESIÓN GRÁFICA
    try:
        handled_perf_imprgraf = handle_performance_report_request_imprgraf(phone_key, texto)
    except Exception as e:
        print(f"❌ Error en handle_performance_report_request_imprgraf: {e}")
        handled_perf_imprgraf = False
    if handled_perf_imprgraf:
        return {"status": "ok", "detail": "performance_report_imprgraf_handled"}

    # 4.2) Informe de desempeño RTR clásico
    try:
        handled_perf = handle_performance_report_request(phone_key, texto)
    except Exception as e:
        print(f"❌ Error en handle_performance_report_request: {e}")
        handled_perf = False
    if handled_perf:
        return {"status": "ok", "detail": "performance_report_handled"}

    # 4.3) COSTURA
    try:
        handled_costura = handle_costura_message(phone_key, texto)
    except Exception as e:
        print(f"❌ Error en handle_costura_message: {e}")
        handled_costura = False
    if handled_costura:
        return {"status": "ok", "detail": "costura_report_handled"}

    # 4.4) FILETEADO
    try:
        handled_fileteado = handle_fileteado_message(phone_key, texto)
    except Exception as e:
        print(f"❌ Error en handle_fileteado_message: {e}")
        handled_fileteado = False
    if handled_fileteado:
        return {"status": "ok", "detail": "fileteado_report_handled"}

    # 4.5) Informe eficiencia fileteado (admin)
    try:
        handled_eff = handle_fileteado_efficiency_request(phone_key, texto)
    except Exception as e:
        print(f"❌ Error en handle_fileteado_efficiency_request: {e}")
        handled_eff = False
    if handled_eff:
        return {"status": "ok", "detail": "fileteado_efficiency_handled"}

    # (Por si llegó sin prefijo, intenta comando de admin otra vez)
    try:
        handled_admin_line = handle_admin_line_report_request(phone_key, texto)
    except Exception as e:
        print(f"❌ Error en handle_admin_line_report_request: {e}")
        handled_admin_line = False
    if handled_admin_line:
        return {"status": "ok", "detail": "admin_line_report_handled"}

    # =========================================================
    # 5) NLU genérica de tareas
    # =========================================================
    nombre_admin = get_user_name_by_phone(phone_key)
    if maybe_handle_task_message(texto, nombre_admin, phone_key):
        return {"status": "ok", "detail": "task_message_handled"}

    # =========================================================
    # 6) Rehidratar SUPERVISIÓN si hubo reload
    # =========================================================
    mem = sessions.setdefault(phone_key, {})
    if not mem.get("supervision"):
        has = load_supervision_session_if_exists(phone_key)
        if has:
            sup = sessions.get(phone_key, {}).get("supervision", {}) or {}
            f = sup.get("flow", [])
            i = sup.get("step_index", 0)
            if f and i < len(f) and not mem.get("active_flow"):
                mem["active_flow"] = "SUPERVISION"
                sessions[phone_key] = mem

    # =========================================================
    # 7) Mantenimiento de sesión/flujo
    # =========================================================
    purge_stale_sessions(phone_key)
    ensure_active_flow(phone_key)

    # Comandos explícitos de arranque
    if texto_low in ("/start novedades", "start novedades", "/start", "start"):
        start_novedades_now(phone_key)
        return {"status": "ok", "detail": "novedades_started"}

    if texto_low in ("/start supervision", "start supervision"):
        nombre = get_user_name_by_phone(phone_key)
        ask_supervision_questions(phone_key, nombre, source="manual")
        sessions.setdefault(phone_key, {})["active_flow"] = "SUPERVISION"
        return {"status": "ok", "detail": "supervision_started_manual"}

    # Rutear por flujo activo
    active_flow = sessions.get(phone_key, {}).get("active_flow")

    # SUPERVISIÓN
    if active_flow == "SUPERVISION":
        handled = supervision_handle_response(phone_key, texto)
        if handled:
            return {"status": "ok", "detail": "handled by supervision_questions"}

    # NOVEDADES (legacy)
    session_file = _session_file(phone_key)
    _session_candidate = sessions.get(phone_key, {})
    session = _session_candidate if isinstance(_session_candidate, dict) and "flow" in _session_candidate else None

    if active_flow == "NOVEDADES" and (not session or "flow" not in session):
        if os.path.exists(session_file):
            print("🔍 Cargando sesión desde archivo:", session_file)
            try:
                with open(session_file, encoding="utf-8") as f:
                    session = json.load(f)
                sessions[phone_key] = session
            except Exception as e:
                print(f"❌ Error cargando sesión desde archivo: {e}")
                session = None

    if not active_flow and not session:
        nombre = get_user_name_by_phone(phone_key)
        respuesta = respuesta_inteligente(texto, nombre, phone_key)
        try:
            if respuesta and respuesta.strip().lower() not in ("ok",):
                send_whatsapp_message(phone_key, respuesta)
        except Exception as e:
            print(f"❌ Error enviando respuesta general a {phone_key}: {e}")
        return {"status": "no_flow", "reply": respuesta}

    if active_flow == "NOVEDADES" and not session:
        nombre = get_user_name_by_phone(phone_key)
        send_whatsapp_message(
            phone_key,
            "⚠️ No encuentro un formulario de *Novedades* activo. "
            "Escribe */start novedades* para reiniciar si corresponde."
        )
        respuesta = respuesta_inteligente(texto, nombre, phone_key)
        return {"status": "warn_no_legacy_session", "reply": respuesta}

    if session:
        flow = session.get("flow", [])
        step_index = session.get("step_index", 0)

        if not flow or step_index >= len(flow):
            reply = "👉 Para iniciar de nuevo, escribe */start novedades*."
            try:
                send_whatsapp_message(phone_key, reply)
            except Exception as e:
                print(f"❌ Error enviando mensaje de flujo finalizado a {phone_key}: {e}")
            sessions[phone_key] = sessions.get(phone_key, {})
            sessions[phone_key]["active_flow"] = None
            return {"status": "done", "detail": "flow completed or index out of range"}

        current_step = flow[step_index]

        if texto.strip().upper() == "EDITAR":
            answers = session.get("answers", {})
            lines = [
                f"{idx+1}️⃣ {step}: {answers.get(step, '❌ Sin respuesta')}"
                for idx, step in enumerate(flow)
            ]
            listado = "\n".join(lines)
            reply = (
                f"📋 *Tus respuestas actuales:*\n{listado}\n\n"
                f"👉 Escribe el número de la pregunta que deseas corregir."
            )
            session["editing"] = True
            sessions[phone_key] = session
            try:
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Error guardando sesión (modo edición): {e}")
            try:
                send_whatsapp_message(phone_key, reply)
            except Exception as e:
                print(f"❌ Error enviando listado de edición a {phone_key}: {e}")
            return {"status": "ok", "mode": "editing", "reply": reply}

        if session.get("editing"):
            try:
                sel = int(texto.strip()) - 1
                if sel < 0 or sel >= len(flow):
                    reply = "⚠️ Número inválido. Escribe un número válido de la lista."
                else:
                    session["step_index"] = sel
                    session["answers"].pop(flow[sel], None)
                    reply = f"✏️ Corrige por favor: {get_prompt(flow[sel], session['process'])}"
                    session.pop("editing", None)
            except ValueError:
                reply = "⚠️ Por favor, escribe solo el número de la pregunta a corregir."
            sessions[phone_key] = session
            try:
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Error guardando sesión (selección de edición): {e}")
            try:
                send_whatsapp_message(phone_key, reply)
            except Exception as e:
                print(f"❌ Error enviando prompt de corrección a {phone_key}: {e}")
            return {"status": "ok", "mode": "editing", "reply": reply}

        session_answers = session.get("answers", {})
        if not isinstance(session_answers, dict):
            session_answers = {}
        session["answers"] = session_answers

        session["answers"][current_step] = texto
        session["step_index"] = step_index + 1

        if session["step_index"] < len(flow):
            reply = get_prompt(session["flow"][session["step_index"]], session["process"])
            sessions[phone_key] = session
            try:
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"❌ Error guardando progreso de sesión: {e}")
            try:
                send_whatsapp_message(phone_key, reply)
            except Exception as e:
                print(f"❌ Error enviando siguiente pregunta a {phone_key}: {e}")
            return {"status": "ok", "step": session.get("step_index"), "reply": reply}

        report_payload = {"process": session["process"], "answers": session["answers"]}
        try:
            response = requests.post(WEBHOOK_URL, json=report_payload)
            print(f"📤 Informe enviado a Make: {response.status_code}")
        except Exception as e:
            print(f"❌ Error enviando a Make: {e}")

        update_alert_status(phone_key, "completed_alert_sent")
        admin_phone = get_admin_phone()
        if admin_phone:
            try:
                nombre = get_user_name_by_phone(phone_key)
                send_whatsapp_message(
                    admin_phone,
                    f"✅ *Informe completado:*\nEl supervisor *{nombre}* ({phone_key}) completó su informe diario."
                )
            except Exception as e:
                print(f"❌ Error notificando al admin: {e}")

        sessions[phone_key] = session
        sessions[phone_key]["active_flow"] = None
        try:
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Error guardando sesión final: {e}")

        reply = "✅ Gracias. Toda la información ha sido registrada y enviada a gerencia. 🙌"
        try:
            send_whatsapp_message(phone_key, reply)
        except Exception as e:
            print(f"❌ Error enviando confirmación final a {phone_key}: {e}")

        return {"status": "ok", "detail": "done"}

    nombre = get_user_name_by_phone(phone_key)
    respuesta = respuesta_inteligente(texto, nombre, phone_key)
    try:
        if respuesta and respuesta.strip().lower() not in ("ok",):
            send_whatsapp_message(phone_key, respuesta)
    except Exception as e:
        print(f"❌ Error enviando respuesta general a {phone_key}: {e}")
    return {"status": "fallback", "reply": respuesta}
