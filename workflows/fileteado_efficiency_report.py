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

# --- Utilidades Internas ---

def _normalize_phone(phone_raw: str) -> str:
    return "".join(ch for ch in (phone_raw or "") if ch.isdigit())

def _collect_user_pools(users_data: dict) -> list:
    pools = []
    pools += users_data.get("users", [])
    pools += users_data.get("supervisors", [])
    pools += users_data.get("administradores", [])
    pools += users_data.get("admins", [])
    return pools

# --- Búsqueda de Supervisores por Área ---

def _find_supervisors_by_keyword(keyword: str) -> list[tuple[str, str]]:
    supervisors: list[tuple[str, str]] = []
    users_data = {}
    try:
        with open(SUPERVISORS_FILE, encoding="utf-8") as f:
            users_data = json.load(f)
    except Exception:
        users_data = {}

    def _push_supervisor(name: str | None, raw: str | None) -> None:
        if not raw: return
        digits = _normalize_phone(raw)
        if not digits: return
        e164 = _normalize_phone(canon_phone_e164_co(digits) or digits)
        supervisors.append((name or "Supervisor", e164 or digits))

    for user in _collect_user_pools(users_data):
        if not isinstance(user, dict): continue
        role = (user.get("role") or "").casefold()
        area = (user.get("area") or user.get("linea") or user.get("linea_produccion") or "").casefold()
        process = (user.get("process") or "").casefold()
        # Buscamos la palabra clave en cualquier campo de identificación
        if keyword in role or keyword in area or keyword in process:
            _push_supervisor(user.get("name") or user.get("nombre"), user.get("phone_e164") or user.get("phone"))

    uniq: dict[str, str] = {}
    for name, phone in supervisors:
        if phone not in uniq:
            uniq[phone] = name
    return [(name, phone) for phone, name in uniq.items()]

# --- Servicios de Notificación ---

def _notify_recipients(phones: set[str], message: str) -> None:
    for phone in phones: send_whatsapp_message(phone, message)

def _send_reports_to_recipients(phones: set[str], pdf_path: str, caption: str) -> None:
    for phone in phones: send_whatsapp_document(phone, pdf_path, caption=caption)

def _send_followup_notice(phones: set[str], message: str) -> None:
    for phone in phones: send_whatsapp_message(phone, message)

def _cleanup_paths(paths: list[str]) -> None:
    for p in paths:
        try:
            if p and os.path.exists(p): os.remove(p)
        except Exception: pass

# --- Handler Principal ---

def handle_fileteado_efficiency_request(phone_key: str, text: str) -> bool:
    msg = (text or "").strip()
    low = msg.lower()
    state = sessions.setdefault(phone_key, {}).get("fileteado_eff_state", {})

    # 1. Comando Inicial
    if low == "informe eficiencia":
        if not _is_admin_phone(phone_key):
            send_whatsapp_message(phone_key, "⛔ Solo el administrador puede generar este informe.")
            return True

        sessions[phone_key]["fileteado_eff_state"] = {"awaiting_menu": True}
        menu_text = (
            "📌 *Selecciona el área para el informe*:\n\n"
            "1) *Fileteado*\n"
            "2) *Costura*\n"
            "3) *Cuerdas* (Cableado, Torsión, Trenzado, Embobinado)\n"
            "4) *Impresion* (Comexi, Saturno, Stelflex)\n\n"
            "👉 Responde con el *número* o el *nombre* del área."
        )
        send_whatsapp_message(phone_key, menu_text)
        return True

    # 2. Selección del Menú
    if state.get("awaiting_menu"):
        choice = low.strip()
        target = None
        df_func = None
        
        if choice in ("1", "fileteado"):
            target, df_func = "fileteado", _download_fileteado_df
        elif choice in ("2", "costura"):
            from workflows.costura_report import _download_costura_df
            target, df_func = "costura", _download_costura_df
        elif choice in ("3", "cuerdas"):
            from workflows.cableado_report import _download_cableado_df
            target, df_func = "cuerdas", _download_cableado_df
        elif choice in ("4", "impresion"):
            from workflows.impresion_report import download_impresion_df
            target, df_func = "impresion", download_impresion_df
        
        if not target:
            send_whatsapp_message(phone_key, "⚠️ Opción no reconocida. Responde *1*, *2*, *3* o *4*.")
            return True

        try:
            df = df_func()
            if df is None or df.empty: raise ValueError(f"La base de datos de {target} está vacía.")
            
            dmin, dmax, fecha_col = _date_range_in_df(df)
            sessions[phone_key]["fileteado_eff_state"] = {
                "awaiting_range": True,
                "target": target,
                "fecha_col": fecha_col,
                "hint_min": dmin.isoformat(),
                "hint_max": dmax.isoformat(),
            }
            
            prompt = (
                f"🔎 Base de *{target.upper()}* conectada.\n"
                f"Datos disponibles: *{dmin.isoformat()}* al *{dmax.isoformat()}*.\n\n"
                "👉 Responde con la fecha o rango deseado (ej: *2025-10-15* o *2025-10-01 a 2025-10-15*)."
            )
            send_whatsapp_message(phone_key, prompt)
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error al conectar: {e}")
            sessions[phone_key].pop("fileteado_eff_state", None)
        return True

    # 3. Procesamiento de Rango y Generación
    if state.get("awaiting_range"):
        start, end = _parse_date_or_range(msg)
        if not start or not end:
            send_whatsapp_message(phone_key, "⚠️ Formato de fecha inválido. Intenta con YYYY-MM-DD.")
            return True

        target = state.get("target")
        fecha_col = state.get("fecha_col")
        admin_phone = _normalize_phone(phone_key)
        
        # Re-descargar según el target para obtener datos frescos
        if target == "costura":
            from workflows.costura_report import _download_costura_df
            df = _download_costura_df()
            supervisors = _find_supervisors_by_keyword("costura")
        elif target == "cuerdas":
            from workflows.cableado_report import _download_cableado_df
            df = _download_cableado_df()
            supervisors = _find_supervisors_by_keyword("cuerdas")
        elif target == "impresion":
            from workflows.impresion_report import download_impresion_df
            df = download_impresion_df()
            supervisors = _find_supervisors_by_keyword("impresion")
        else:
            df = _download_fileteado_df()
            supervisors = _find_supervisors_by_keyword("fileteado")

        df_sel = _filter_by_range(df, start, end, fecha_col)
        recipients_admin = {admin_phone} if admin_phone else set()
        _notify_recipients(recipients_admin, f"📄 Procesando informes de *{target.upper()}*...")

        temp_paths = []
        try:
            if target == "costura":
                from workflows.costura_report import build_pdf_costura
                p, t = build_pdf_costura(df_sel, start, end)
                temp_paths.extend([p, *(t or [])])
                _send_reports_to_recipients(recipients_admin, p, "📄 EFICIENCIA COSTURA")
            
            elif target == "cuerdas":
                from workflows.cableado_report import build_pdf_cableado
                from workflows.torsion_report import build_pdf_torsion
                from workflows.trenzado_report import build_pdf_trenzado
                from workflows.embobina_report import build_pdf_embobina
                
                for func, name in [(build_pdf_cableado, "CABLEADO"), (build_pdf_torsion, "TORSIÓN"), 
                                   (build_pdf_trenzado, "TRENZADO"), (build_pdf_embobina, "EMBOBINADO")]:
                    p, t = func(df_sel, start, end)
                    if p:
                        temp_paths.extend([p, *(t or [])])
                        _send_reports_to_recipients(recipients_admin, p, f"📄 EFICIENCIA {name}")

            elif target == "impresion":
                from workflows.impresion_report import build_pdf_impresion
                p, t = build_pdf_impresion(df_sel, start, end)
                if p:
                    temp_paths.extend([p, *(t or [])])
                    _send_reports_to_recipients(recipients_admin, p, "📄 EFICIENCIA IMPRESIÓN")
            
            else: # Fileteado
                from workflows.fileteado_gasa import build_pdf_gasa
                from workflows.fileteado_leno import build_pdf_leno
                from workflows.fileteado_planas import build_pdf_planas
                from workflows.fileteado_cortadoras import build_pdf_cortadoras
                for f, n in [(build_pdf_gasa, "GASA"), (build_pdf_leno, "LENO"), 
                             (build_pdf_planas, "PLANAS"), (build_pdf_cortadoras, "CORTADORAS")]:
                    p, t = f(df_sel, start, end)
                    if p:
                        temp_paths.extend([p, *(t or [])])
                        _send_reports_to_recipients(recipients_admin, p, f"📄 EFICIENCIA {n}")

            # Preparar confirmación para envío a supervisores
            supervisor_name = supervisors[0][0] if supervisors else f"supervisor de {target}"
            sessions[phone_key]["fileteado_eff_state"] = {
                "awaiting_confirm": True,
                "target": target,
                "supervisor_name": supervisor_name,
                "supervisor_phones": sorted({p for _, p in supervisors}),
                "pending_paths": temp_paths,
                "rango_texto": f"{start.isoformat()} a {end.isoformat()}",
            }
            
            sup_list_str = " / ".join(sorted({p for _, p in supervisors})) if supervisors else "N/D"
            send_whatsapp_message(phone_key, f"¿Deseas enviar estos informes al supervisor *{supervisor_name}* ({sup_list_str})? (si/no)")
            
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error en generación: {e}")
            _cleanup_paths(temp_paths)
            sessions[phone_key].pop("fileteado_eff_state", None)
        return True

    # 4. Confirmación de Envío a Supervisor
    if state.get("awaiting_confirm"):
        reply = low.strip()
        target = state.get("target")
        supervisor_phones = state.get("supervisor_phones", [])
        supervisor_name = state.get("supervisor_name")
        pending_paths = state.get("pending_paths", [])

        if reply in ("si", "sí", "s", "yes"):
            if supervisor_phones:
                for path in pending_paths:
                    if path.lower().endswith(".pdf"):
                        _send_reports_to_recipients(set(supervisor_phones), path, f"📄 Reporte Eficiencia {target.capitalize()}")
                
                followup = (
                    f"🤖 *CiplasBot Informa*: Se han generado los reportes de eficiencia "
                    f"del periodo *{state.get('rango_texto')}* para el área de *{target.upper()}*.\n\n"
                    "Favor revisar el desempeño y realizar seguimiento a los indicadores críticos."
                )
                _send_followup_notice(set(supervisor_phones), followup)
                send_whatsapp_message(phone_key, f"✅ Informes enviados con éxito a {supervisor_name}.")
            else:
                send_whatsapp_message(phone_key, "⚠️ No se encontraron contactos de supervisores para esta área.")
        else:
            send_whatsapp_message(phone_key, f"🆗 Entendido. Los reportes no se enviaron a {supervisor_name}.")
        
        _cleanup_paths(pending_paths)
        sessions[phone_key].pop("fileteado_eff_state", None)
        return True

    return False