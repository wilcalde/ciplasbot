# compile_supervision_report.py
import os
import json
import requests
from datetime import datetime
from collections import defaultdict
from typing import List, Dict, Tuple, Optional
from openai import OpenAI

# 📍 Rutas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "../config")
RESPONSES_DIR = os.path.join(CONFIG_DIR, "supervision_responses")
USERS_FILE = os.path.join(CONFIG_DIR, "users.json")

# 🌐 Webhook de Make (reemplaza si aplica)
MAKE_WEBHOOK_URL = "https://hook.us2.make.com/hmlnjg64d7360ly5b90d337a6cv678kn"

# 🧠 Cliente OpenAI
client = OpenAI()

# 🎯 Temas oficiales y sinónimos para clasificar respuestas
THEME_MAP: Dict[str, List[str]] = {
    "programacion": ["programacion", "programadas", "planificacion", "programación"],
    "calidad": ["calidad", "producto no conforme", "pnc", "no conforme"],
    "novedades_mantenimiento": [
        "mantenimiento", "paradas", "atención y novedades con mantenimiento",
        "novedades mantenimiento", "novedades de mantenimiento"
    ],
    "inventarios": ["inventario", "inventarios"],
    "personal": ["personal", "asistencia", "retroalimentación al personal", "retroalimentacion al personal"],
    "documentacion": ["documentación", "documentacion", "documentos", "métodos", "metodos", "normas", "procedimientos"],
}

THEME_TITLES: Dict[str, str] = {
    "programacion": "📅 Programación",
    "calidad": "✅ Calidad",
    "novedades_mantenimiento": "🛠️ Novedades de mantenimiento",
    "inventarios": "📦 Inventarios",
    "personal": "👥 Personal",
    "documentacion": "🗂️ Documentación",
}

THEME_ORDER: List[str] = [
    "programacion",
    "calidad",
    "novedades_mantenimiento",
    "inventarios",
    "personal",
    "documentacion",
]

# ——————————————————————————————————————————————
# Utilidades
# ——————————————————————————————————————————————
def _nz(s: Optional[str]) -> str:
    return (s or "").strip()

def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _normalize_phone(phone: str) -> str:
    return "".join(ch for ch in str(phone) if ch.isdigit())

def detect_theme(key_lower: str) -> Optional[str]:
    for canonical, synonyms in THEME_MAP.items():
        for syn in synonyms:
            if syn.lower() in key_lower:
                return canonical
    return None

def load_users_map() -> Dict[str, Dict[str, str]]:
    """
    Carga users.json y devuelve {telefono_normalizado: {"name":..., "role":...}}
    """
    users_map: Dict[str, Dict[str, str]] = {}
    if not os.path.exists(USERS_FILE):
        print(f"⚠️ users.json no encontrado en {USERS_FILE}")
        return users_map
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for u in data.get("users", []):
            phone = _normalize_phone(u.get("phone", ""))
            if phone:
                users_map[phone] = {
                    "name": u.get("name", "").strip(),
                    "role": (u.get("role") or "").strip().lower()
                }
    except Exception as e:
        print(f"❌ Error leyendo users.json: {e}")
    return users_map

def detect_process_and_supervisor(data: dict, filename: str, users_map: Dict[str, Dict[str, str]]) -> Tuple[str, str]:
    """
    Devuelve (process_name, supervisor_name). Intenta:
    1) Campos dentro del JSON
    2) Nombre desde users.json usando teléfono (en JSON o en nombre archivo)
    3) Fallbacks
    """
    # Proceso
    proc_candidates = [
        data.get("process"), data.get("proceso"), data.get("area"),
        data.get("Proceso"), data.get("Proceso_Area")
    ]
    process_name = next(( _nz(c) for c in proc_candidates if _nz(c) ), "")

    # Teléfono (para mapear supervisor)
    phone_json = _normalize_phone(data.get("phone", "") or data.get("telefono", ""))
    phone_file = ""
    try:
        base = os.path.splitext(os.path.basename(filename))[0]
        # esperado: <phone>_<YYYYMMDD>.json
        phone_file = base.split("_")[0]
        phone_file = _normalize_phone(phone_file)
    except Exception:
        pass

    # Supervisor desde JSON
    sup_candidates = [
        data.get("supervisor"), data.get("Supervisor"), data.get("responsable")
    ]
    supervisor_name = next(( _nz(c) for c in sup_candidates if _nz(c) ), "")

    # Si no hay supervisor, intentar mapear por teléfono
    if not supervisor_name:
        for cand in [phone_json, phone_file]:
            if cand and cand in users_map:
                supervisor_name = _nz(users_map[cand].get("name"))
                if supervisor_name:
                    break

    # Fallback por nombre de archivo para proceso (palabras clave comunes)
    if not process_name:
        base_low = os.path.splitext(os.path.basename(filename))[0].replace("-", "_").lower()
        for key in ["costura", "fileteado", "torsion", "embobina", "rtr", "impresion", "cuerdas", "supervision"]:
            if key in base_low:
                process_name = key.upper() if key != "rtr" else "RTR"
                break

    if not process_name:
        process_name = "SUPERVISIÓN"
    if not supervisor_name:
        supervisor_name = "—"  # Evita "Supervisor sin nombre"

    return process_name, supervisor_name

def load_sessions() -> List[dict]:
    """
    Carga todos los archivos .json de RESPONSES_DIR y retorna
    una lista de bloques normalizados:
    {
      "process": "FILETEADO",
      "supervisor": "Ciro Marín",
      "themes": {
          "programacion": [ ...textos... ],
          "calidad": [ ... ],
          ...
      }
    }
    """
    if not os.path.exists(RESPONSES_DIR):
        print(f"⚠️ Carpeta de respuestas no encontrada: {RESPONSES_DIR}")
        return []

    users_map = load_users_map()
    blocks: List[dict] = []

    print(f"📁 Archivos en {RESPONSES_DIR}:")
    for filename in os.listdir(RESPONSES_DIR):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(RESPONSES_DIR, filename)
        print(" -", filename)

        # Cargar JSON
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"   ❌ Error leyendo {filename}: {e}")
            continue

        # Ubicar diccionario de respuestas (respuestas/answers)
        respuestas = data.get("respuestas") or data.get("answers") or {}
        if not isinstance(respuestas, dict) or not respuestas:
            print(f"   ⚠️ No se encontraron respuestas válidas en {filename}")
            continue

        process_name, supervisor_name = detect_process_and_supervisor(data, filename, users_map)
        themes_bucket: Dict[str, List[str]] = defaultdict(list)

        # Clasificar cada clave de respuesta en un tema oficial
        for k, v in respuestas.items():
            key_l = _norm(k)
            val = _nz(v)
            if not val:
                continue
            theme = detect_theme(key_l)
            if theme is None:
                # Si no casa con ningún tema, lo enviamos a 'documentacion' como neutro
                theme = "documentacion"
            # Guardamos como "EtiquetaOriginal: contenido"
            themes_bucket[theme].append(f"{k}: {val}")

        if not themes_bucket:
            print(f"   ⚠️ No hubo contenido clasificable en {filename}")
            continue

        blocks.append({
            "process": process_name,
            "supervisor": supervisor_name,
            "themes": themes_bucket
        })

    return blocks

# 🔧 Prompt para generar HTML agradable (sin CSS, con emojis)
SYSTEM_PROMPT = """
Eres un redactor corporativo experto en comunicación clara. Devuelve **EXCLUSIVAMENTE HTML**, usando solo estas etiquetas: <h2>, <h3>, <p>, <ul>, <li>, <strong>.
Usa redacción breve, accionable, con **emojis** para hacerlo más ameno (sin excederte).

FORMATO POR PROCESO:
<h2>🔎 Proceso: [Nombre del proceso] — Supervisor: [Nombre del supervisor]</h2>

Para cada tema con contenido (en este orden exacto), incluye:
<h3>📅 Programación</h3>
<p><strong>Puntos a mejorar:</strong></p>
<ul>
  <li>…</li>
</ul>

<h3>✅ Calidad</h3>
<p><strong>Puntos a mejorar:</strong></p>
<ul>
  <li>…</li>
</ul>

<h3>🛠️ Novedades de mantenimiento</h3>
<p><strong>Puntos a mejorar:</strong></p>
<ul>
  <li>…</li>
</ul>

<h3>📦 Inventarios</h3>
<p><strong>Puntos a mejorar:</strong></p>
<ul>
  <li>…</li>
</ul>

<h3>👥 Personal</h3>
<p><strong>Puntos a mejorar:</strong></p>
<ul>
  <li>…</li>
</ul>

<h3>🗂️ Documentación</h3>
<p><strong>Puntos a mejorar:</strong></p>
<ul>
  <li>…</li>
</ul>

REGLAS IMPORTANTES:
- No inventes información. Resume con bullets claros y sin redundancias.
- Evita repetir el mismo punto con palabras distintas.
- Si un tema no tiene contenido, no lo muestres.

AL FINAL DEL DOCUMENTO:
<h2>🌐 Visión general (temas transversales)</h2>
<p>Sintetiza los puntos comunes que el líder debe atacar de forma global.</p>
<ul>
  <li>…</li>
</ul>
"""

def build_llm_input(blocks: List[dict]) -> str:
    """
    Prepara un texto de entrada estructurado para el modelo,
    agrupando por proceso y por tema.
    """
    lines: List[str] = []
    for b in blocks:
        proc = b["process"]
        sup = b.get("supervisor", "")
        lines.append(f"PROCESO: {proc} | SUPERVISOR: {sup}")
        # Orden por THEME_ORDER
        for theme_key in THEME_ORDER:
            items = b["themes"].get(theme_key, [])
            if not items:
                continue
            lines.append(f"TEMA: {THEME_TITLES[theme_key]}")
            for it in items:
                # Cada item ya viene como "EtiquetaOriginal: contenido"
                lines.append(f"- {it}")
        lines.append("---")  # separador entre procesos
    return "\n".join(lines)

def compile_supervision_report():
    print("📋 Compilando informe de supervisión por temas...")

    blocks = load_sessions()
    if not blocks:
        print("⚠️ No se encontraron respuestas de supervisores.")
        return

    draft = build_llm_input(blocks)
    # print("DEBUG INPUT LLM:\n", draft)

    print("🧠 Enviando contenido al modelo OpenAI para estructurar HTML...")
    try:
        response = client.chat.completions.create(
            model="o4-mini",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": draft}
            ]
        )
        final_body = (response.choices[0].message.content or "").strip()
    except Exception as e:
        print(f"❌ No se pudo generar el HTML del informe: {e}")
        return

    today = datetime.now().strftime("%Y-%m-%d")
    subject = f"📋 Informe Diario — Supervisión ({today})"

    payload = {
        "subject": subject,
        "body": final_body
    }

    try:
        res = requests.post(MAKE_WEBHOOK_URL, json=payload)
        if res.status_code == 200:
            print("📤 Informe enviado correctamente al administrador.")
        else:
            print(f"❌ Error al enviar email (status {res.status_code}): {res.text}")
    except Exception as e:
        print(f"❌ Error al enviar al webhook de Make: {e}")
        return

    # ✅ Limpiar archivos procesados
    deleted = 0
    try:
        for filename in os.listdir(RESPONSES_DIR):
            if filename.endswith(".json"):
                try:
                    os.remove(os.path.join(RESPONSES_DIR, filename))
                    deleted += 1
                except Exception as e:
                    print(f"⚠️ No se pudo eliminar {filename}: {e}")
    finally:
        print(f"🧹 {deleted} archivos temporales de supervisión eliminados.")

if __name__ == "__main__":
    compile_supervision_report()
