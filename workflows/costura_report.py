# workflows/costura_report.py
import os
import re
import json
from io import BytesIO
from datetime import date

import pandas as pd
import requests
from fpdf import FPDF  # fpdf2

from services.session_memory import CONFIG_DIR, sessions, SUPERVISORS_FILE
from services.whatsapp_service import send_whatsapp_message
from services.whatsapp_media import send_whatsapp_document
from services.wa_window_manager import canon_phone_e164_co

# ────────────────────────────────────────────────────────────────────────────────
# Constantes / rutas
# ────────────────────────────────────────────────────────────────────────────────
SHEET_EDIT_URL = "https://docs.google.com/spreadsheets/d/1V-9iIVMLf19vuQIoiu53t6k2J2vlu49vUjEMnKS5bLY/edit?usp=drivesdk"

REPORTS_DIR = os.path.join(CONFIG_DIR, "costura_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# Selecciones
VALVE_CODES = {"VAL", "VAL1", "VAL2"}
BOTHEVEN_MACHINES = {
    "A9", "A10", "A11", "A14", "A15", "A16", "A17", "A18", "A19",
    "A21", "A22", "A23", "A24", "A25"
}

# ────────────────────────────────────────────────────────────────────────────────
# Utilidades
# ────────────────────────────────────────────────────────────────────────────────
def _export_csv_url(sheet_edit_url: str) -> str:
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", sheet_edit_url)
    if not m:
        raise RuntimeError("No se pudo extraer el ID de Google Sheets.")
    sid = m.group(1)
    return f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"

def _only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

# ────────────────────────────────────────────────────────────────────────────────
# Permisos
# ────────────────────────────────────────────────────────────────────────────────
def _is_admin_or_costura_supervisor(phone_raw: str) -> bool:
    """Admin o supervisor COSTURA"""
    try:
        with open(SUPERVISORS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f).get("users", [])
    except Exception:
        return False

    in_digits = _only_digits(canon_phone_e164_co(phone_raw) or phone_raw)

    for u in users:
        raw = u.get("phone_e164") or u.get("phone") or ""
        dig = _only_digits(canon_phone_e164_co(raw) or raw)
        if not dig or dig != in_digits:
            continue

        role = (u.get("role") or "").strip().casefold()
        proc = (u.get("process") or "").strip().upper()
        is_admin_flag = bool(u.get("is_admin"))

        if is_admin_flag or role == "administrador":
            return True
        if role == "supervisor" and proc == "COSTURA":
            return True
    return False

# ────────────────────────────────────────────────────────────────────────────────
# Carga de datos
# ────────────────────────────────────────────────────────────────────────────────
def _download_costura_df() -> pd.DataFrame | None:
    try:
        url = _export_csv_url(SHEET_EDIT_URL)
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        df = pd.read_csv(BytesIO(resp.content))
        df.columns = [str(c).strip() for c in df.columns]
        return df
    except Exception as e:
        print(f"❌ Error descargando base COSTURA: {e}")
        return None

def _date_range_in_df(df: pd.DataFrame):
    if df is None or df.empty:
        return None, None, None
    if "Fecha_Efectiva" not in df.columns:
        return None, None, None
    s = pd.to_datetime(df["Fecha_Efectiva"], errors="coerce")
    if s.dropna().empty:
        return None, None, "Fecha_Efectiva"
    return (s.min().date(), s.max().date(), "Fecha_Efectiva")

def _parse_date_or_range(text: str):
    t = (text or "").strip()
    m = re.fullmatch(r"\s*(\d{4}[-/]\d{2}[-/]\d{2})\s*\Z", t)
    if m:
        d = pd.to_datetime(m.group(1), errors="coerce")
        d = None if pd.isna(d) else d.date()
        return d, d
    m = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})\s*(?:a|al|hasta|-|to)\s*(\d{4}[-/]\d{2}[-/]\d{2})", t, flags=re.IGNORECASE)
    if m:
        d1 = pd.to_datetime(m.group(1), errors="coerce")
        d2 = pd.to_datetime(m.group(2), errors="coerce")
        if not pd.isna(d1) and not pd.isna(d2):
            d1, d2 = d1.date(), d2.date()
            if d1 > d2:
                d1, d2 = d2, d1
            return d1, d2
    return None, None

def _filter_by_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df is None or df.empty or "Fecha_Efectiva" not in df.columns:
        return df
    s = pd.to_datetime(df["Fecha_Efectiva"], errors="coerce")
    return df.loc[(s.dt.date >= start) & (s.dt.date <= end)].copy()

# ────────────────────────────────────────────────────────────────────────────────
# Cálculos
# ────────────────────────────────────────────────────────────────────────────────
def _compute_totals(df: pd.DataFrame) -> dict:
    res = {"BOTHEVEN": 0, "FUELLE": 0, "VALVULA": 0}
    if df is None or df.empty:
        return res

    if not {"Centro_Trabajo", "Maquina", "Cantidad_Completada"}.issubset(df.columns):
        return res

    centro = df["Centro_Trabajo"].astype(str).str.strip().str.upper()
    maq    = df["Maquina"].astype(str).str.strip().str.upper()
    qty    = pd.to_numeric(df["Cantidad_Completada"], errors="coerce").fillna(0)

    # BOTHEVEN
    mask_botheven = (centro == "COSTURA") & (maq.isin(BOTHEVEN_MACHINES))
    res["BOTHEVEN"] = int(qty[mask_botheven].sum())

    # FUELLE
    mask_fuelle = (centro == "FUELLE")
    res["FUELLE"] = int(qty[mask_fuelle].sum())

    # VALVULA
    mask_valvula = (centro == "COSTURA") & (maq.isin(VALVE_CODES))
    res["VALVULA"] = int(qty[mask_valvula].sum())

    return res

# ────────────────────────────────────────────────────────────────────────────────
# PDF
# ────────────────────────────────────────────────────────────────────────────────
def _sanitize_pdf_text(s: str) -> str:
    if not s:
        return ""
    repl = {"•": "-", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "\u00A0": " "}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

def _build_pdf_costura(start: date, end: date, totals: dict) -> str:
    title = "ANALISIS PROCESO COSTURA"
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    page_w = pdf.w - 2 * pdf.l_margin

    pdf.set_text_color(0, 128, 0); pdf.set_font("Helvetica", "B", 14)
    pdf.cell(page_w, 8, _sanitize_pdf_text("CIPLAS S.A.S"), ln=1, align="L")

    pdf.set_text_color(0, 0, 0); pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 12, _sanitize_pdf_text(title), ln=1, align="C")

    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, _sanitize_pdf_text(f"Rango seleccionado: {start.isoformat()} a {end.isoformat()}"), ln=1)

    pdf.ln(2); pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _sanitize_pdf_text("Unidades Producidas"), ln=1, align="L")
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 7, _sanitize_pdf_text(f"BOTHEVEN = {totals.get('BOTHEVEN', 0):,}".replace(",", ".")), ln=1, align="L")
    pdf.cell(0, 7, _sanitize_pdf_text(f"FUELLE   = {totals.get('FUELLE', 0):,}".replace(",", ".")), ln=1, align="L")
    pdf.cell(0, 7, _sanitize_pdf_text(f"VALVULA  = {totals.get('VALVULA', 0):,}".replace(",", ".")), ln=1, align="L")

    pdf.ln(8); pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 8, _sanitize_pdf_text("Informe Generado por CIPLASBOT - Agente I.A - Creado por Ing. Wilson Calderon"),
             ln=1, align="C")

    fname = f"Analisis_Costura_{start.isoformat()}_{end.isoformat()}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)
    pdf.output(out_path)
    return out_path

# ────────────────────────────────────────────────────────────────────────────────
# Handler
# ────────────────────────────────────────────────────────────────────────────────
def handle_costura_message(phone_key: str, text: str) -> bool:
    msg = (text or "").strip()
    low = msg.lower()
    state = sessions.setdefault(phone_key, {}).get("costura_state", {})

    # 1) comando
    if low == "informe costura":
        if not _is_admin_or_costura_supervisor(phone_key):
            send_whatsapp_message(phone_key, "⛔ Solo el administrador o el supervisor de COSTURA pueden generar este informe.")
            return True

        df = _download_costura_df()
        if df is None or df.empty:
            send_whatsapp_message(phone_key, "❌ No pude leer la base de COSTURA en este momento.")
            return True

        dmin, dmax, fecha_col = _date_range_in_df(df)
        if not dmin or not dmax:
            send_whatsapp_message(phone_key, "ℹ️ No encontré una columna de fecha válida en la base.")
            return True

        sessions.setdefault(phone_key, {})["costura_state"] = {
            "awaiting_range": True,
            "hint_min": dmin.isoformat(),
            "hint_max": dmax.isoformat(),
        }
        send_whatsapp_message(
            phone_key,
            ("🔎 Base de COSTURA encontrada.\n"
             f"Rango disponible: *{dmin.isoformat()}* a *{dmax.isoformat()}*.\n\n"
             "👉 Responde con una fecha 'YYYY-MM-DD' o un rango 'YYYY-MM-DD a YYYY-MM-DD' para continuar.")
        )
        return True

    # 2) espera rango
    if state.get("awaiting_range"):
        start, end = _parse_date_or_range(msg)
        hint_min, hint_max = state.get("hint_min"), state.get("hint_max")

        if not start or not end:
            send_whatsapp_message(
                phone_key,
                ("⚠️ Formato no reconocido. Envía una fecha 'YYYY-MM-DD' o un rango 'YYYY-MM-DD a YYYY-MM-DD'.\n"
                 f"Rango disponible en base: {hint_min} a {hint_max}.")
            )
            return True

        df = _download_costura_df()
        if df is None or df.empty:
            send_whatsapp_message(phone_key, "❌ No pude leer la base de COSTURA en este momento.")
            sessions[phone_key].pop("costura_state", None)
            return True

        df_sel = _filter_by_range(df, start, end)
        totals = _compute_totals(df_sel)

        try:
            pdf_path = _build_pdf_costura(start, end, totals)
            send_whatsapp_document(phone_key, pdf_path, caption="📄 Análisis proceso COSTURA")
        except Exception as e:
            send_whatsapp_message(phone_key, f"❌ Error generando o enviando el informe: {e}")

        sessions[phone_key].pop("costura_state", None)
        return True

    return False
