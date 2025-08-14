# workflows/send_daily_tasks.py
import json
import os
import unicodedata
from datetime import datetime
from typing import List, Dict, Any

from services.openai_service import get_openai_client
from services.whatsapp_service import send_whatsapp_message

# 📂 Rutas base
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
TASKS_DIR = os.path.join(CONFIG_DIR, "tasks")
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")

# =========================
# Utilidades de texto / mojibake / días
# =========================
def _fix_mojibake(s: str) -> str:
    """
    Repara textos UTF-8 leídos como latin1 (mojibake), p.ej. 'MiÃ©rcoles' -> 'Miércoles'.
    Si no aplica, devuelve s sin cambios.
    """
    if not isinstance(s, str):
        return ""
    try:
        return s.encode("latin1").decode("utf-8")
    except Exception:
        return s

def _strip_accents(s: str) -> str:
    """Elimina tildes y diacríticos (incluye ñ->n si existiera en combinaciones raras)."""
    if not isinstance(s, str):
        return ""
    nfkd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")

def _safe_lower_collapse(s: str) -> str:
    """Aplica fix de mojibake, minúsculas y colapsa espacios múltiples."""
    s2 = _fix_mojibake(s)
    s2 = s2.lower()
    s2 = " ".join(s2.split())
    return s2

# Días canónicos (sin tildes)
_CANON_DAYS = {
    "lunes": "lunes",
    "martes": "martes",
    "miercoles": "miercoles",
    "jueves": "jueves",
    "viernes": "viernes",
    "sabado": "sabado",
    "domingo": "domingo",
}
# Para impresión bonita con tildes
_ES_PRINT = {
    "lunes": "Lunes",
    "martes": "Martes",
    "miercoles": "Miércoles",
    "jueves": "Jueves",
    "viernes": "Viernes",
    "sabado": "Sábado",
    "domingo": "Domingo",
}
# EN->ES del día del sistema
_EN_TO_ES = {
    "monday": "lunes",
    "tuesday": "martes",
    "wednesday": "miercoles",
    "thursday": "jueves",
    "friday": "viernes",
    "saturday": "sabado",
    "sunday": "domingo",
}

def _canonical_day(s: str) -> str:
    """
    Normaliza un token de día a uno canónico sin acentos.
    Corrige mojibake y errores frecuentes: 'miarcoles', 'm artes', 'ma rtes', 'l unes'.
    También acepta abreviaturas (lun, mar, mie, jue, vie, sab, dom).
    """
    if not s:
        return ""
    s0 = _safe_lower_collapse(s)      # repara mojibake, lower, colapsa espacios
    s1 = _strip_accents(s0)           # quita tildes
    # Conserva solo letras y espacios
    s1 = "".join(ch for ch in s1 if ch.isalpha() or ch.isspace())
    s1 = " ".join(s1.split())

    # Abreviaturas
    if s1 in ("lun", "lu"):
        s1 = "lunes"
    elif s1 in ("mar", "ma"):
        s1 = "martes"
    elif s1 in ("mie", "mier", "mi"):
        s1 = "miercoles"
    elif s1 in ("jue", "ju"):
        s1 = "jueves"
    elif s1 in ("vie", "vi"):
        s1 = "viernes"
    elif s1 in ("sab", "sa"):
        s1 = "sabado"
    elif s1 in ("dom", "do"):
        s1 = "domingo"

    # Correcciones vistas en tus logs
    s1 = s1.replace("mi arcoles", "miercoles")
    s1 = s1.replace("miarcoles", "miercoles")
    s1 = s1.replace("m artes", "martes")
    s1 = s1.replace("ma rtes", "martes")
    s1 = s1.replace("l unes", "lunes")

    # Resultado final
    if s1 in _CANON_DAYS:
        return s1
    s2 = "".join(ch for ch in s1 if ch.isalpha())
    return _CANON_DAYS.get(s2, s2)

def _normalize_days_list(dias: List[str]) -> List[str]:
    """Devuelve lista única de días canónicos, filtrando valores no válidos."""
    seen = set()
    out: List[str] = []
    for d in dias or []:
        cd = _canonical_day(d)
        if cd in _CANON_DAYS and cd not in seen:
            seen.add(cd)
            out.append(cd)
    return out

def _today_canonical_es() -> str:
    """Día de hoy en canónico ES (sin tildes)."""
    weekday_en = datetime.now().strftime("%A").lower()
    return _EN_TO_ES.get(weekday_en, weekday_en)

# =========================
# Envío de tareas diarias
# =========================
def send_daily_tasks():
    print("🚦 Enviando tareas diarias motivadoras...")

    client = get_openai_client()

    weekday_en = datetime.today().strftime("%A")       # 'Wednesday'
    hoy = _today_canonical_es()                        # 'miercoles'
    print(f"📅 Hoy es: {weekday_en} ➜ {_ES_PRINT.get(hoy, hoy.capitalize())} ➜ Normalizado: {hoy}")

    # 📂 Leer usuarios (UTF-8)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        users = data.get("users", [])

    for supervisor in users:
        name = supervisor.get("name", "")
        phone = supervisor.get("phone", "")
        # process = supervisor.get("process", "")  # por si se usa en el futuro

        filename = f"{str(name).lower().replace(' ', '_')}.json"
        task_file = os.path.join(TASKS_DIR, filename)
        print(f"🔍 Buscando archivo: {task_file}")

        if not os.path.exists(task_file):
            print(f"⚠️ Archivo NO encontrado: {task_file}")
            continue

        # Lee tareas (UTF-8)
        with open(task_file, "r", encoding="utf-8") as f:
            tasks_data = json.load(f)

        today_tasks: List[Dict[str, Any]] = []
        for task in tasks_data.get("daily_tasks", []):
            # Repara mojibake en campos de texto que enviaremos/imprimiremos
            actividad = _fix_mojibake(task.get("actividad", "(sin actividad)"))
            hora = _fix_mojibake(task.get("hora", "—"))

            # Normaliza días
            dias_originales = task.get("dias", [])
            dias_normalizados = _normalize_days_list(dias_originales)

            # Log equivalente al tuyo, pero con días ya canónicos y sin errores tipo 'miarcoles'
            print(f"🧪 Tarea: {actividad} ➜ Días normalizados: {dias_normalizados}")

            if hoy in dias_normalizados:
                # Usamos la actividad/hora ya reparadas
                today_tasks.append({
                    **task,
                    "actividad": actividad,
                    "hora": hora,
                    "_dias_norm": dias_normalizados,
                })

        if not today_tasks:
            print(f"⏭️ No hay tareas para hoy para {name}")
            continue

        # ✏️ Construir prompt para OpenAI (resumen motivador)
        task_lines = "\n".join([f"- {t['actividad']} (⏰ {t.get('hora','—')})" for t in today_tasks])
        prompt = (
            f"Redacta un mensaje breve, motivador y profesional para {name} "
            f"que incluya:\n\n"
            f"👋 Un saludo cordial.\n"
            f"📋 Las tareas del día:\n{task_lines}\n\n"
            f"💪 Un cierre motivador deseándole un excelente día de trabajo.\n"
            f"Usa emojis apropiados de motivación y trabajo."
        )

        # ✅ Generar mensaje con OpenAI
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un asistente de producción motivador, breve y claro."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
        )

        message = (completion.choices[0].message.content or "").strip()

        # ✅ Mostrar y enviar
        print(f"\n📋 Mensaje generado para {name}:\n{message}\n")
        if phone:
            try:
                send_whatsapp_message(phone, message)
            except Exception as e:
                print(f"⚠️ Error enviando WhatsApp a {name} ({phone}): {e}")

if __name__ == "__main__":
    send_daily_tasks()
