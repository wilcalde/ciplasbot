# workflows/fileteado_efficiency_report.py
import os
import json

import pandas as pd

from services.session_memory import sessions, SUPERVISORS_FILE
from services.whatsapp_service import send_whatsapp_message
from services.whatsapp_media import send_whatsapp_document
from services.wa_window_manager import canon_phone_e164_co

from workflows.fileteado_report import (
    _download_fileteado_df,
    _date_range_in_df,
    _parse_date_or_range,
    _filter_by_range,
    _find_col,
    _is_admin_phone,
    COLS,
)


def _normalize_phone(phone_raw: str) -> str:
    return "".join(ch for ch in (phone_raw or "") if ch.isdigit())


def _collect_user_pools(users_data: dict) -> list:
    pools = []
    pools += users_data.get("users", [])
    pools += users_data.get("supervisors", [])
    pools += users_data.get("administradores", [])
    pools += users_data.get("admins", [])
    return pools


def _find_fileteado_supervisors() -> set[str]:
    phones: set[str] = set()
    users_data = {}
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users_data = json.load(f)
    except Exception:
        users_data = {}

    def _push_phone(raw: str | None) -> None:
        if not raw:
            return
        digits = _normalize_phone(raw)
        if not digits:
            return
        phones.add(digits)
        e164 = _normalize_phone(canon_phone_e164_co(digits) or digits)
        if e164:
            phones.add(e164)

    for user in _collect_user_pools(users_data):
        if not isinstance(user, dict):
            continue
        role = (user.get("role") or "").casefold()
        area = (user.get("area") or user.get("linea") or user.get("linea_produccion") or "").casefold()
        if "fileteado" in role or "fileteado" in area:
            _push_phone(user.get("phone_e164") or user.get("phone"))

    try:
        from config.users import SUPERVISORS
    except Exception:
        SUPERVISORS = {}
    for name, phone in SUPERVISORS.items():
        if "filete" in str(name).casefold():
            _push_phone(phone)

    return phones


def _notify_recipients(phones: set[str], message: str) -> None:
    for phone in phones:
        send_whatsapp_message(phone, message)


def _send_reports_to_recipients(phones: set[str], pdf_path: str, caption: str) -> None:
    for phone in phones:
        send_whatsapp_document(phone, pdf_path, caption=caption)


def handle_fileteado_efficiency_request(phone_key: str, text: str) -> bool:
    msg = (text or "").strip()
    low = msg.lower()
    state = sessions.setdefault(phone_key, {}).get("fileteado_eff_state", {})

    if low == "informe eficiencia":
        if not _is_admin_phone(phone_key):
            send_whatsapp_message(phone_key, "⛔ Solo el administrador puede generar este informe.")
            return True

        df = _download_fileteado_df()
        if df is None or df.empty:
            send_whatsapp_message(phone_key, "❌ No pude leer la base de fileteado en este momento.")
            return True

        dmin, dmax, fecha_col = _date_range_in_df(df)
        if not dmin or not dmax:
            send_whatsapp_message(phone_key, "ℹ️ No encontré una columna de fecha válida en la base.")
            return True

        sessions.setdefault(phone_key, {})["fileteado_eff_state"] = {
            "awaiting_range": True,
            "fecha_col": fecha_col,
            "hint_min": dmin.isoformat(),
            "hint_max": dmax.isoformat(),
        }
        send_whatsapp_message(
            phone_key,
            ("🔎 Base de fileteado encontrada.\n"
             f"Rango disponible: *{dmin.isoformat()}* a *{dmax.isoformat()}*.\n\n"
             "👉 Responde con una fecha 'YYYY-MM-DD' o un rango 'YYYY-MM-DD a YYYY-MM-DD' para continuar.")
        )
        return True

    if state.get("awaiting_range"):
        start, end = _parse_date_or_range(msg)
        hint_min = state.get("hint_min") or "N/D"
        hint_max = state.get("hint_max") or "N/D"
        if not start or not end:
            send_whatsapp_message(
                phone_key,
                ("⚠️ Formato no reconocido. Envía una fecha 'YYYY-MM-DD' o un rango 'YYYY-MM-DD a YYYY-MM-DD'.\n"
                 f"Rango disponible en base: {hint_min} a {hint_max}.")
            )
            return True

        df = _download_fileteado_df()
        if df is None or df.empty:
            send_whatsapp_message(phone_key, "❌ No pude leer la base de fileteado en este momento.")
            sessions[phone_key].pop("fileteado_eff_state", None)
            return True

        fecha_col = state.get("fecha_col") or _find_col(df, COLS["fecha"])
        if not fecha_col:
            send_whatsapp_message(phone_key, "ℹ️ No encontré una columna de fecha válida en la base.")
            sessions[phone_key].pop("fileteado_eff_state", None)
            return True

        df_sel = _filter_by_range(df, start, end, fecha_col)
        recipients = {_normalize_phone(phone_key)}
        recipients.update(_find_fileteado_supervisors())
        recipients = {r for r in recipients if r}

        _notify_recipients(recipients, "📄 Generando informes de eficiencia de fileteado...")

        try:
            from workflows.fileteado_gasa import build_pdf_gasa
            gasa_pdf, gasa_tmp = build_pdf_gasa(df_sel, start, end)
            _send_reports_to_recipients(recipients, gasa_pdf, "📄 Informe eficiencia – GASA")
            for p in [gasa_pdf, *(gasa_tmp or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe GASA: {e}")

        try:
            from workflows.fileteado_leno import build_pdf_leno
            leno_pdf, leno_tmp = build_pdf_leno(df_sel, start, end)
            _send_reports_to_recipients(recipients, leno_pdf, "📄 Informe eficiencia – LENO")
            for p in [leno_pdf, *(leno_tmp or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe LENO: {e}")

        try:
            from workflows.fileteado_planas import build_pdf_planas
            planas_pdf, planas_tmp = build_pdf_planas(df_sel, start, end)
            _send_reports_to_recipients(recipients, planas_pdf, "📄 Informe eficiencia – PLANAS")
            for p in [planas_pdf, *(planas_tmp or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe PLANAS: {e}")

        try:
            from workflows.fileteado_cortadoras import build_pdf_cortadoras
            cort_pdf, cort_tmp = build_pdf_cortadoras(df_sel, start, end)
            _send_reports_to_recipients(recipients, cort_pdf, "📄 Informe eficiencia – CORTADORAS")
            for p in [cort_pdf, *(cort_tmp or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe CORTADORAS: {e}")

        send_whatsapp_message(phone_key, "✅ Informes de eficiencia enviados.")
        sessions[phone_key].pop("fileteado_eff_state", None)
        return True

    return False
