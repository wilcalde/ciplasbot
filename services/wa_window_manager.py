# services/wa_window_manager.py
import os
import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from services.session_memory import CONFIG_DIR
from services.whatsapp_service import send_whatsapp_message

# Rutas de archivos
CONFIG_FILE = os.path.join(CONFIG_DIR, "chat_window.json")
STATE_FILE = os.path.join(CONFIG_DIR, "wa_conversations.json")

os.makedirs(CONFIG_DIR, exist_ok=True)
print(f"🗂️ CONFIG_DIR (wa_window_manager): {CONFIG_DIR}")

# ========================
# Utilidades de tiempo
# ========================
def _now() -> datetime:
    return datetime.now()  # si prefieres UTC: datetime.utcnow()

def _to_iso(dt: datetime) -> str:
    return dt.isoformat()

def _from_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

# ========================
# I/O JSON
# ========================
def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _write_json(path: str, data: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

# ========================
# Configuración
# ========================
_DEFAULT_CFG = {
    "enabled": True,
    "window_hours": 24,             # ventana WhatsApp
    "nudge_before_minutes": 120,     # enviar nudge 120 min antes de expirar
    "check_every_minutes": 60,       # intervalo (min) del job periódico
    "nudge_message": (
        "⏰ *Pronto se completan 24 horas del ultima interaccion con CiplasBot.* Pasadas 24 horas no pdre enviarte mensajes. Tu ventana de contexto vence en {mins} min.\n"
        "Responde *sí* o *Ok* para manener activa la comunicacion 🙌"
    )
}

def load_config() -> Dict[str, Any]:
    cfg = _read_json(CONFIG_FILE, {})
    if not isinstance(cfg, dict):
        cfg = {}
    merged = {**_DEFAULT_CFG, **cfg}
    merged["window_hours"] = int(merged.get("window_hours", 24))
    merged["nudge_before_minutes"] = int(merged.get("nudge_before_minutes", 60))
    merged["check_every_minutes"] = max(1, int(merged.get("check_every_minutes", 5)))
    merged["enabled"] = bool(merged.get("enabled", True))
    return merged

def save_config(new_cfg: Dict[str, Any]) -> None:
    current = load_config()
    current.update(new_cfg or {})
    _write_json(CONFIG_FILE, current)

# ========================
# Estado por número
# ========================
def _load_state() -> Dict[str, Any]:
    st = _read_json(STATE_FILE, {})
    return st if isinstance(st, dict) else {}

def _save_state(st: Dict[str, Any]) -> None:
    print(f"📝 Guardando estado en: {STATE_FILE}")
    _write_json(STATE_FILE, st)

def _get_contact(st: Dict[str, Any], phone: str) -> Dict[str, Any]:
    return st.setdefault(phone, {
        "last_inbound": None,   # ISO
        "last_outbound": None,  # ISO
        "last_nudge_at": None,  # ISO
        "nudged_for_inbound": None  # ISO del inbound para el que ya nudgemos
    })

# ========================
# Normalización de teléfono
# ========================
def _digits_only(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isdigit())

def canon_phone_e164_co(phone: str) -> str:
    d = _digits_only(phone)
    if d.startswith("57") and len(d) == 12:
        return d
    if len(d) >= 10:
        return "57" + d[-10:]
    return d

# ========================
# API pública
# ========================
def record_inbound(phone: str, ts: Optional[datetime] = None) -> None:
    """Registrar último mensaje *recibido* del usuario."""
    phone_key = canon_phone_e164_co(phone)
    st = _load_state()
    c = _get_contact(st, phone_key)
    when = ts or _now()
    c["last_inbound"] = _to_iso(when)
    c["nudged_for_inbound"] = None  # reinicia nudge para nueva ventana
    print(f"✅ record_inbound para {phone_key} -> archivo: {STATE_FILE}")
    _save_state(st)

def record_outbound(phone: str, ts: Optional[datetime] = None) -> None:
    """Registrar último mensaje *enviado* (nuestro)."""
    phone_key = canon_phone_e164_co(phone)
    st = _load_state()
    c = _get_contact(st, phone_key)
    when = ts or _now()
    c["last_outbound"] = _to_iso(when)
    _save_state(st)

def can_send_freeform(phone: str, now: Optional[datetime] = None) -> bool:
    """¿Podemos enviar mensaje no-plantilla? (dentro de la ventana)."""
    phone_key = canon_phone_e164_co(phone)
    cfg = load_config()
    st = _load_state()
    c = _get_contact(st, phone_key)
    last_in = _from_iso(c.get("last_inbound"))
    if not last_in:
        return False
    now = now or _now()
    return (now - last_in) < timedelta(hours=cfg["window_hours"])

def time_until_expiry(phone: str, now: Optional[datetime] = None) -> Optional[timedelta]:
    """Tiempo restante para que expire la ventana (desde el último inbound)."""
    phone_key = canon_phone_e164_co(phone)
    cfg = load_config()
    st = _load_state()
    c = _get_contact(st, phone_key)
    last_in = _from_iso(c.get("last_inbound"))
    if not last_in:
        return None
    now = now or _now()
    expiry = last_in + timedelta(hours=cfg["window_hours"])
    return expiry - now

def _should_nudge(phone_key: str, now: datetime, cfg: Dict[str, Any], st: Dict[str, Any]) -> bool:
    c = _get_contact(st, phone_key)
    last_in = _from_iso(c.get("last_inbound"))
    if not last_in:
        return False
    # ¿ya expiró?
    remaining = (last_in + timedelta(hours=cfg["window_hours"])) - now
    if remaining.total_seconds() <= 0:
        return False
    # ¿dentro de franja de nudge?
    if remaining > timedelta(minutes=cfg["nudge_before_minutes"]):
        return False
    # ¿ya nudgemos para ESTE inbound?
    if c.get("nudged_for_inbound") == _to_iso(last_in):
        return False
    return True

def run_nudges(now: Optional[datetime] = None) -> List[str]:
    """
    Busca números que deban recibir nudge y envía el mensaje por WhatsApp.
    Devuelve la lista de teléfonos notificados.
    """
    cfg = load_config()
    if not cfg.get("enabled", True):
        return []

    now = now or _now()
    st = _load_state()
    notified: List[str] = []

    for phone_key, c in list(st.items()):
        if _should_nudge(phone_key, now, cfg, st) and can_send_freeform(phone_key, now):
            last_in = _from_iso(c.get("last_inbound"))
            remaining = (last_in + timedelta(hours=cfg["window_hours"])) - now
            mins = max(1, int(remaining.total_seconds() // 60))

            message = cfg["nudge_message"].replace("{mins}", str(mins))
            try:
                send_whatsapp_message(phone_key, message)
                c["last_outbound"] = _to_iso(now)
                c["last_nudge_at"] = _to_iso(now)
                c["nudged_for_inbound"] = _to_iso(last_in)
                notified.append(phone_key)
            except Exception as e:
                print(f"❌ Error enviando nudge a {phone_key}: {e}")

    _save_state(st)
    return notified

# ========================
# Scheduling
# ========================
def schedule_window_jobs(scheduler) -> None:
    """
    Registra el job periódico que llama run_nudges() según config.
    """
    cfg = load_config()
    minutes = cfg["check_every_minutes"]
    scheduler.add_job(
        func=lambda: run_nudges(),
        trigger="interval",
        minutes=minutes,
        id="wa_window_nudges",
        replace_existing=True
    )
    print(f"⏲️ Job wa_window_nudges cada {minutes} min registrado.")
