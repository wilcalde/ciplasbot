# services/whatsapp_media.py
import os
import json
import requests
from typing import Optional

# Intentar importar la config global de CiplasBot
try:
    from services.session_memory import CONFIG_DIR
except Exception:
    # fallback si no carga session_memory
    CONFIG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config"))

GRAPH_BASE = "https://graph.facebook.com/v20.0"
WABA_JSON = os.path.join(CONFIG_DIR, "waba.json")

class WhatsAppMediaError(Exception):
    pass

# Intentar importar la config ya usada en mensajes de texto
try:
    from services import whatsapp_service as _ws  # NO circular: whatsapp_service no importa este módulo
except Exception:
    _ws = None

def _read_waba_json() -> dict:
    """
    Lee config/waba.json si existe. Devuelve {} si no existe o hay error.
    """
    try:
        if os.path.exists(WABA_JSON):
            with open(WABA_JSON, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}

def _get_token() -> str:
    """
    Busca el token en:
    1) Variables de entorno
    2) services.whatsapp_service
    3) config/waba.json
    """
    cfg = _read_waba_json()

    token = (
        os.getenv("WHATSAPP_TOKEN")
        or os.getenv("WABA_TOKEN")
        or os.getenv("META_WABA_TOKEN")
        or (getattr(_ws, "WABA_TOKEN", None) if _ws else None)
        or (getattr(_ws, "WHATSAPP_TOKEN", None) if _ws else None)
        or cfg.get("WHATSAPP_TOKEN")
        or cfg.get("WABA_TOKEN")
        or cfg.get("META_WABA_TOKEN")
    )
    if not token:
        raise WhatsAppMediaError(
            "Falta WHATSAPP_TOKEN (o alias WABA_TOKEN/META_WABA_TOKEN). "
            "Define la variable de entorno o configúralo en config/waba.json."
        )
    return token

def _get_phone_number_id() -> str:
    """
    Busca el PHONE_NUMBER_ID en:
    1) Variables de entorno (varios nombres)
    2) services.whatsapp_service (varios atributos)
    3) config/waba.json (varios nombres)
    """
    cfg = _read_waba_json()

    candidates_env = [
        "WHATSAPP_PHONE_NUMBER_ID",
        "PHONE_NUMBER_ID",
        "WABA_PHONE_NUMBER_ID",
        "FROM_PHONE_NUMBER_ID",
        "BUSINESS_PHONE_ID",
    ]
    for k in candidates_env:
        v = os.getenv(k)
        if v:
            return v

    if _ws:
        candidates_ws = [
            "PHONE_NUMBER_ID",
            "WABA_PHONE_NUMBER_ID",
            "FROM_PHONE_NUMBER_ID",
            "FROM_NUMBER_ID",
            "BUSINESS_PHONE_ID",
        ]
        for attr in candidates_ws:
            v = getattr(_ws, attr, None)
            if v:
                return v

    candidates_json = [
        "WHATSAPP_PHONE_NUMBER_ID",
        "PHONE_NUMBER_ID",
        "WABA_PHONE_NUMBER_ID",
        "FROM_PHONE_NUMBER_ID",
        "BUSINESS_PHONE_ID",
    ]
    for k in candidates_json:
        v = cfg.get(k)
        if v:
            return v

    raise WhatsAppMediaError(
        "No se encontró PHONE_NUMBER_ID. "
        "Define alguna de estas llaves en variables de entorno o en config/waba.json: "
        "WHATSAPP_PHONE_NUMBER_ID / PHONE_NUMBER_ID / WABA_PHONE_NUMBER_ID / FROM_PHONE_NUMBER_ID / BUSINESS_PHONE_ID."
    )

def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_token()}"}

def upload_media_document(file_path: str, mime_type: str = "application/pdf") -> str:
    """
    Sube el archivo a WhatsApp y devuelve media_id.
    """
    phone_number_id = _get_phone_number_id()
    url = f"{GRAPH_BASE}/{phone_number_id}/media"

    if not os.path.exists(file_path):
        raise WhatsAppMediaError(f"El archivo no existe: {file_path}")

    with open(file_path, "rb") as f:
        files = {"file": (os.path.basename(file_path), f, mime_type)}
        data = {"messaging_product": "whatsapp", "type": mime_type}
        r = requests.post(url, headers=_headers(), files=files, data=data, timeout=60)

    if r.status_code >= 300:
        raise WhatsAppMediaError(f"Error subiendo media: {r.status_code} {r.text}")

    media_id = r.json().get("id")
    if not media_id:
        raise WhatsAppMediaError(f"Respuesta inesperada en upload: {r.text}")
    return media_id

def send_whatsapp_document(to_e164: str, file_path: str, caption: str = "") -> None:
    """
    Envía un documento (PDF) por WhatsApp usando el media_id subido.
    """
    media_id = upload_media_document(file_path)
    phone_number_id = _get_phone_number_id()
    url = f"{GRAPH_BASE}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to_e164,  # "573..." o "+573..." funcionan
        "type": "document",
        "document": {
            "id": media_id,
            "caption": caption,
            "filename": os.path.basename(file_path)
        }
    }
    r = requests.post(url, headers={**_headers(), "Content-Type": "application/json"}, json=payload, timeout=60)
    if r.status_code >= 300:
        raise WhatsAppMediaError(f"Error enviando documento: {r.status_code} {r.text}")

