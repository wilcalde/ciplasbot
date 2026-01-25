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


def _find_fileteado_supervisors() -> list[tuple[str, str]]:
    supervisors: list[tuple[str, str]] = []
    users_data = {}
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users_data = json.load(f)
    except Exception:
        users_data = {}

    def _push_supervisor(name: str | None, raw: str | None) -> None:
        if not raw:
            return
        digits = _normalize_phone(raw)
        if not digits:
            return
        e164 = _normalize_phone(canon_phone_e164_co(digits) or digits)
        supervisors.append((name or "Supervisor", e164 or digits))

    for user in _collect_user_pools(users_data):
        if not isinstance(user, dict):
            continue
        role = (user.get("role") or "").casefold()
        area = (user.get("area") or user.get("linea") or user.get("linea_produccion") or "").casefold()
        if "fileteado" in role or "fileteado" in area:
            _push_supervisor(user.get("name") or user.get("nombre"), user.get("phone_e164") or user.get("phone"))

    try:
        from config.users import SUPERVISORS
    except Exception:
        SUPERVISORS = {}
    for name, phone in SUPERVISORS.items():
        if "filete" in str(name).casefold():
            _push_supervisor(str(name), phone)

    try:
        from config.user import SUPERVISORS as LEGACY_SUPERVISORS  # type: ignore
    except Exception:
        LEGACY_SUPERVISORS = {}
    for name, phone in LEGACY_SUPERVISORS.items():
        if "filete" in str(name).casefold():
            _push_supervisor(str(name), phone)

    uniq: dict[str, str] = {}
    for name, phone in supervisors:
        if phone not in uniq:
            uniq[phone] = name
    return [(name, phone) for phone, name in uniq.items()]


def _notify_recipients(phones: set[str], message: str) -> None:
    for phone in phones:
        send_whatsapp_message(phone, message)


def _send_reports_to_recipients(phones: set[str], pdf_path: str, caption: str) -> None:
    for phone in phones:
        send_whatsapp_document(phone, pdf_path, caption=caption)


def _cleanup_paths(paths: list[str]) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


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
        admin_phone = _normalize_phone(phone_key)
        supervisors = _find_fileteado_supervisors()
        supervisor_name = supervisors[0][0] if supervisors else "supervisor de fileteado"
        supervisor_phones = {phone for _, phone in supervisors}
        recipients_admin = {admin_phone} if admin_phone else set()

        _notify_recipients(recipients_admin, "📄 Generando informes de eficiencia de fileteado...")

        temp_paths: list[str] = []
        try:
            from workflows.fileteado_gasa import build_pdf_gasa
            gasa_pdf, gasa_tmp = build_pdf_gasa(df_sel, start, end)
            temp_paths.extend([gasa_pdf, *(gasa_tmp or [])])
            _send_reports_to_recipients(recipients_admin, gasa_pdf, "📄 Informe eficiencia – GASA")
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe GASA: {e}")

        try:
            from workflows.fileteado_leno import build_pdf_leno
            leno_pdf, leno_tmp = build_pdf_leno(df_sel, start, end)
            temp_paths.extend([leno_pdf, *(leno_tmp or [])])
            _send_reports_to_recipients(recipients_admin, leno_pdf, "📄 Informe eficiencia – LENO")
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe LENO: {e}")

        try:
            from workflows.fileteado_planas import build_pdf_planas
            planas_pdf, planas_tmp = build_pdf_planas(df_sel, start, end)
            temp_paths.extend([planas_pdf, *(planas_tmp or [])])
            _send_reports_to_recipients(recipients_admin, planas_pdf, "📄 Informe eficiencia – PLANAS")
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe PLANAS: {e}")

        try:
            from workflows.fileteado_cortadoras import build_pdf_cortadoras
            cort_pdf, cort_tmp = build_pdf_cortadoras(df_sel, start, end)
            temp_paths.extend([cort_pdf, *(cort_tmp or [])])
            _send_reports_to_recipients(recipients_admin, cort_pdf, "📄 Informe eficiencia – CORTADORAS")
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando informe CORTADORAS: {e}")

        sessions.setdefault(phone_key, {})["fileteado_eff_state"] = {
            "awaiting_confirm": True,
            "supervisor_name": supervisor_name,
            "supervisor_phones": sorted(supervisor_phones),
            "pending_paths": temp_paths,
        }
        supervisor_phone_display = " / ".join(sorted(supervisor_phones)) if supervisor_phones else "N/D"
        send_whatsapp_message(
            phone_key,
            ("¿Deseas enviar estos informes al supervisor "
             f"{supervisor_name} ({supervisor_phone_display})? Responde *si* o *no*.")
        )
        return True

    if state.get("awaiting_confirm"):
        reply = low.strip()
        supervisor_name = state.get("supervisor_name") or "supervisor de fileteado"
        supervisor_phones = set(state.get("supervisor_phones") or [])
        pending_paths = list(state.get("pending_paths") or [])
        if reply in ("si", "sí", "s", "yes"):
            if supervisor_phones:
                for path in pending_paths:
                    if path.lower().endswith(".pdf"):
                        _send_reports_to_recipients(supervisor_phones, path, f"📄 Informe eficiencia – {supervisor_name}")
            send_whatsapp_message(phone_key, f"✅ Informes enviados a {supervisor_name}.")
        else:
            send_whatsapp_message(phone_key, "✅ Listo, no se enviaron informes al supervisor.")
        _cleanup_paths(pending_paths)
        sessions[phone_key].pop("fileteado_eff_state", None)
        return True

    return False
