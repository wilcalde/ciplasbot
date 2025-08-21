import os
import json
import time
import csv
from datetime import datetime, timedelta
from io import StringIO
from typing import List, Dict, Optional
from openai import OpenAI
import requests

from services.whatsapp_service import send_whatsapp_message
from services.session_memory import CONFIG_DIR, SUPERVISORS_FILE, ALERT_LOG_FILE

# 🌐 Webhook Make (se envían AMBOS correos aquí)
WEBHOOK_URL = "https://hook.us2.make.com/k2vr3eevsu1sc1l60lkqtnmdegdemfpa"

# 🧠 Cliente OpenAI
client = OpenAI()

# 📄 Google Sheets (CSV export)
SHEET_ID = "1-YqQDndpU8EVn35o2BegvkyQAlzDHdCBhoFb5EjV6JE"
GID = "223028791"
COSTURA_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"


# ────────────────────────────────────────────────────────────────────────────────
# Utilidades
# ────────────────────────────────────────────────────────────────────────────────

def get_admin_data():
    """
    Lee users.json y retorna nombre y teléfono del administrador.
    """
    with open(SUPERVISORS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    for user in data.get("users", []):
        if user.get("role", "").lower() == "administrador":
            return {"name": user["name"], "phone": user["phone"]}
    raise ValueError("⚠️ No se encontró usuario administrador en users.json")


def list_supervisor_sessions() -> (List[str], List[Dict]):
    """
    Lee todos los *_session.json en:
      - CONFIG_DIR
      - CONFIG_DIR/supervision_responses
    Retorna (lista_de_rutas_completas, lista_de_reportes_json)
    """
    session_dirs = [
        CONFIG_DIR,
        os.path.join(CONFIG_DIR, "supervision_responses"),
    ]
    session_filepaths: List[str] = []
    for d in session_dirs:
        try:
            for f in os.listdir(d):
                if f.endswith("_session.json"):
                    session_filepaths.append(os.path.join(d, f))
        except FileNotFoundError:
            continue

    print("🗂️ Sesiones encontradas:", session_filepaths)

    reports: List[Dict] = []
    for path in session_filepaths:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            reports.append(data)
        except Exception as e:
            print(f"❌ Error leyendo {path}: {e}")
    return session_filepaths, reports


def detect_date_column(headers: List[str]) -> Optional[str]:
    """
    Intenta detectar el nombre de la columna de fecha en la hoja.
    """
    candidates = [
        "FECHA", "Fecha", "fecha", "DIA", "Día", "día", "Dia",
        "FEC", "FECHA REGISTRO", "Fecha registro", "Fecha Registro"
    ]
    normalized = {h.strip(): h for h in headers}
    for cand in candidates:
        for key, original in normalized.items():
            if cand.lower().replace(" ", "") == key.lower().replace(" ", ""):
                return original
    return None


def parse_date_safe(text: str) -> Optional[datetime]:
    """
    Intenta parsear una fecha en múltiples formatos comunes.
    """
    if not text:
        return None
    text = text.strip()
    fmts = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%m-%d-%Y",
        "%Y/%m/%d", "%d.%m.%Y", "%Y.%m.%d"
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def fetch_costura_absences() -> List[Dict]:
    """
    Descarga la hoja como CSV, filtra por hoy y ayer,
    y retorna registros (TODOS los tipos de novedad, no solo ausencias).
    Los registros del CSV se enviarán bajo la sección fija 'COSTURA'.
    Cada ítem llevará Área; si viene vacío, se rellena con 'COSTURA'.
    """
    try:
        resp = requests.get(COSTURA_CSV_URL, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Error descargando hoja: {e}")
        return []

    text = resp.text
    f = StringIO(text)
    reader = csv.DictReader(f)
    rows = list(reader)
    if not rows:
        return []

    headers = list(rows[0].keys())
    col_fecha = detect_date_column(headers)

    today = datetime.now().date()
    yesterday = (datetime.now() - timedelta(days=1)).date()

    results = []
    for row in rows:
        # Fecha: hoy o ayer
        passes_date = True
        fecha_norm = ""
        if col_fecha is not None:
            dt = parse_date_safe(row.get(col_fecha, ""))
            if dt is None:
                passes_date = False
            else:
                d = dt.date()
                passes_date = (d == today) or (d == yesterday)
                fecha_norm = dt.strftime("%d/%m/%Y")
        if not passes_date:
            continue

        area_raw = (row.get("ÁREA") or row.get("AREA") or "").strip()
        area = area_raw if area_raw else "COSTURA"  # fallback solicitado
        turno = (row.get("TURNO") or "").strip()
        tipo = (
            row.get("TIPO DE NOVEDAD") or row.get("Tipo de novedad")
            or row.get("NOVEDAD") or row.get("Tipo") or ""
        ).strip()
        operario = (row.get("NOMBRE DEL OPERARIO") or row.get("OPERARIO") or "").strip()

        registro = {
            "AREA": area,                          # aseguramos no vacío
            "TURNO": turno,
            "TIPO_DE_NOVEDAD": tipo,
            "OPERARIO": operario,
            "FECHA": row.get(col_fecha, "").strip() if col_fecha else "",
            "FECHA_NORM": fecha_norm,
            # 🔒 Fijamos el grupo visible en el correo:
            "PROCESO": "COSTURA",
        }
        results.append(registro)

    return results


def ensure_signature(html: str) -> str:
    """
    Garantiza que el cuerpo HTML termine con la firma 'Agente IA CiplasBot 🤖'.
    Si ya la contiene, no duplica. Si detecta </body> o </html>, inserta antes del cierre.
    """
    signature_text = "Agente IA CiplasBot 🤖"
    if signature_text.lower() in (html or "").lower():
        return html

    extra = "\n<p>Atentamente,<br>Agente IA CiplasBot 🤖</p>"
    lower = (html or "").lower()
    if "</body>" in lower:
        idx = lower.rfind("</body>")
        return html[:idx] + extra + html[idx:]
    if "</html>" in lower:
        idx = lower.rfind("</html>")
        return html[:idx] + extra + html[idx:]
    return (html or "") + extra


# ────────────────────────────────────────────────────────────────────────────────
# Email 1 (SIN CAMBIOS)
# ────────────────────────────────────────────────────────────────────────────────

def build_email_1_body(reports: List[Dict]) -> str:
    system_prompt = (
        "Eres un asistente que genera un email en HTML para el equipo de producción. "
        "Debes detallar los reportes de cada supervisor, agrupados por proceso. "
        "Para cada proceso, incluye: supervisor, hora de registro, y listas con las novedades: personal ausente, operando, inventario, RESUMEN_PARO, paradas, y notas generales. "
        "Utiliza etiquetas HTML (<h3>, <h4>, <ul>, <li>, <p>) y emojis para cada sección. "
        "Al final, agrega un pie de página con agradecimiento y la firma 'Agente IA CiplasBot 🤖'."
    )
    user_prompt = (
        "Genera el cuerpo en HTML de un email de resumen diario de novedades basado en la siguiente lista de reportes (formato JSON):\n"
        f"{json.dumps(reports, ensure_ascii=False, indent=2)}"
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"❌ Error generando el resumen con OpenAI (Email 1): {e}")
        date_str = datetime.now().strftime('%d/%m/%Y')
        return (
            f"<p>👋 <strong>¡Buen día, equipo!</strong></p>"
            f"<p>📅 Actualización del área de conversión al <b>{date_str}</b>:</p>"
            "<p>No fue posible generar el resumen automáticamente.</p>"
            "<p>Atentamente,<br>Agente IA CiplasBot 🤖</p>"
        )


# ────────────────────────────────────────────────────────────────────────────────
# Email 2 — RR.HH. (supervisores + CSV bajo sección 'COSTURA')
# ────────────────────────────────────────────────────────────────────────────────

def build_email_2_absences_body(reports: List[Dict], csv_records: List[Dict]) -> str:
    """
    Construye el HTML para RR.HH. mostrando:
      - Ausentes/novedades detectados en reportes de supervisores (otros procesos).
      - TODOS los registros del CSV (hoy/ayer) bajo el encabezado fijo 'COSTURA'.
    Reglas:
      - Saludo/introducción con emojis.
      - Supervisores: 'Nombre — detalle' (sin '(Turno no disponible)').
      - CSV: 'Nombre — Turno: X — Área: Y — Novedad: Z — Fecha: DD/MM/YYYY'.
      - No mencionar 'fuente'.
    """
    system_prompt = (
        "Eres un asistente que redacta un email en HTML, claro y agradable, para Recursos Humanos. "
        "Debes listar ausentismo y novedades de asistencia agrupadas por proceso.\n\n"
        "FUENTES:\n"
        "1) Reportes de supervisores (JSON): extrae SOLO personas ausentes o con novedades de asistencia. "
        "Para cada persona, muestra: 'Nombre — <detalle>' (p. ej., 'vacaciones', 'incapacidad', 'permiso', 'no asistió'). "
        "Si no hay turno, no lo menciones. No digas 'fuente'.\n"
        "2) Registros del CSV (JSON): INCLUYE TODOS LOS REGISTROS de hoy y ayer. "
        "Muestra estos ítems en una sección con encabezado EXACTO 'COSTURA'. "
        "Para cada ítem usa exactamente: "
        "'Nombre — Turno: <turno> — Área: <área> — Novedad: <tipo> — Fecha: <FECHA_NORM si existe, si no FECHA>'. "
        "El campo Área NO debe quedar vacío; si viniera vacío en los datos, usa 'COSTURA'.\n\n"
        "ESTILO:\n"
        "- Comienza con saludo cordial e introducción con emojis (👋🧑‍💼🗓️) indicando que incluye hoy y ayer.\n"
        "- Usa secciones <h3> por proceso y listas <ul> con <li> por persona.\n"
        "- Evita tablas. No menciones 'fuente'. "
        "Cierra con una despedida y la firma 'Agente IA CiplasBot 🤖'."
    )

    user_prompt = (
        "Genera el email de RR.HH. combinando estas dos entradas.\n\n"
        "1) Reportes de supervisores (JSON):\n"
        f"{json.dumps(reports, ensure_ascii=False, indent=2)}\n\n"
        "2) Registros del CSV (ya filtrados por hoy/ayer). "
        "DEBEN IR BAJO UNA SECCIÓN TITULADA 'COSTURA':\n"
        f"{json.dumps(csv_records, ensure_ascii=False, indent=2)}\n\n"
        "IMPORTANTE:\n"
        "- Supervisores: muestra 'Nombre — detalle' (sin colocar textos de 'turno no disponible').\n"
        "- CSV: respeta 'Nombre — Turno: X — Área: Y — Novedad: Z — Fecha: ...'; "
        "si el área está vacía en el dato, coloca 'COSTURA'."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}]
        )
        content = response.choices[0].message.content
        return ensure_signature(content)
    except Exception as e:
        print(f"❌ Error generando el email de RR.HH. (Email 2): {e}")
        date_str = datetime.now().strftime('%d/%m/%Y')
        fallback = (
            f"<h2>👋 Resumen de ausentismo y novedades – {date_str}</h2>"
            "<p>No fue posible generar el detalle automáticamente.</p>"
        )
        return ensure_signature(fallback)


# ────────────────────────────────────────────────────────────────────────────────
# Flujo principal
# ────────────────────────────────────────────────────────────────────────────────

def compile_daily_summary():
    """
    Compila los reportes JSON de varios supervisores, genera dos emails si hay reportes:
      1) Resumen general (SIN CAMBIOS)
      2) RR.HH.: ausentismo/novedades de supervisores + TODOS los registros del CSV (hoy/ayer)
         en sección 'COSTURA'.
    Si NO hay reportes, envía SOLO el email 2 usando el CSV.
    Envía al mismo webhook espaciados por unos segundos.
    Finalmente, limpia archivos de sesión y alertas.
    """
    # 1) Sesiones de supervisores (en 2 rutas)
    session_filepaths, reports = list_supervisor_sessions()
    admin = get_admin_data()

    # 2) Registros CSV (siempre)
    csv_records = fetch_costura_absences()

    sent_email_1 = False

    if reports:
        # 3) Construir y enviar Email 1 (SIN CAMBIOS)
        final_body_1 = build_email_1_body(reports)
        subject_1 = f"Informe de Novedades Conversión – {datetime.now().strftime('%d/%m/%Y')}"
        try:
            requests.post(WEBHOOK_URL, json={'subject': subject_1, 'body': final_body_1})
            sent_email_1 = True
            # Notificar por WhatsApp tal como estaba
            send_whatsapp_message(
                admin['phone'],
                f"✅ {admin['name']}, el informe diario de novedades fue enviado correctamente."
            )
        except Exception as e:
            print(f"❌ Error enviando informe (Email 1): {e}")
    else:
        # Antes retornaba aquí. Ahora NO retornamos: seguimos con Email 2.
        try:
            send_whatsapp_message(
                admin['phone'],
                f"⚠️ {admin['name']}, hoy no se recibieron archivos de sesión de supervisores. "
                "Se enviará solo el email para RR.HH. basado en la hoja."
            )
        except Exception:
            pass

    # 4) Construir y enviar Email 2 (RR.HH.) — SIEMPRE con lo que haya
    final_body_2 = build_email_2_absences_body(reports, csv_records)

    # Asunto con rango de fechas (ayer y hoy)
    today_d = datetime.now().date()
    yest_d = today_d - timedelta(days=1)
    subject_2 = f"Ausentismo RR.HH. – {yest_d.strftime('%d/%m/%Y')} y {today_d.strftime('%d/%m/%Y')}"

    # Si ya enviamos el 1, separamos por unos segundos
    if sent_email_1:
        time.sleep(4)

    try:
        requests.post(WEBHOOK_URL, json={'subject': subject_2, 'body': final_body_2})
    except Exception as e:
        print(f"❌ Error enviando informe (Email 2): {e}")

    # 5) Limpieza de archivos de sesión y alertas
    for fullpath in session_filepaths:
        try:
            os.remove(fullpath)
        except Exception:
            pass
    if os.path.exists(ALERT_LOG_FILE):
        try:
            os.remove(ALERT_LOG_FILE)
        except Exception:
            pass


if __name__ == "__main__":
    compile_daily_summary()
