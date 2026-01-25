# workflows/fileteado_report.py
import os
import re
import json
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import requests

# PDF
try:
    from fpdf import FPDF  # fpdf2
except ImportError as e:
    raise RuntimeError("Falta la dependencia 'fpdf2'. Instálala con: pip install fpdf2") from e

# Gráficos
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Wedge, Circle
except ImportError as e:
    raise RuntimeError("Falta la dependencia 'matplotlib'. Instálala con: pip install matplotlib") from e

from services.session_memory import CONFIG_DIR, sessions, SUPERVISORS_FILE
from services.whatsapp_service import send_whatsapp_message
from services.whatsapp_media import send_whatsapp_document
from services.wa_window_manager import canon_phone_e164_co

# ────────────────────────────────────────────────────────────────────────────────
# Constantes / rutas
# ────────────────────────────────────────────────────────────────────────────────
FILETEADO_XLSX_URL = (
    "https://docs.google.com/spreadsheets/d/1FYLgfQhLvCUtiuxGnn5aQK6aCChoFPmMU-eMa0KAHrg/export?format=xlsx"
)

REPORTS_DIR = os.path.join(CONFIG_DIR, "fileteado_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# Columnas candidatas
COLS = {
    "fecha": ["Fecha", "Fecha_Efectiva", "Fecha_Registro", "fecha", "fecha_efectiva"],
    "linea": ["Linea_Produccion", "linea_produccion", "Linea", "linea"],
    "articulo": ["Numero_Articulo", "numero_articulo", "Articulo", "articulo", "Numero de articulo"],
    "maquina": ["Maquina", "Máquina", "maquina", "máquina", "Equipo", "equipo"],
    "cantidad": ["Cantidad_Completada", "cantidad_completada", "Cantidad", "cantidad"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_corrida", "tpo_cda"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrida_estandar"],
    "cause": ["Causa_Paro", "causa_paro", "motivo_paro", "Causa"],
    "turno": ["Turno", "turno"],
    "operario": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre_Operario"],
}

# ────────────────────────────────────────────────────────────────────────────────
# Menú de líneas
# ────────────────────────────────────────────────────────────────────────────────
LINE_CHOICES = {
    "1": "gasa",
    "2": "leno",
    "3": "planas",
    "4": "cortadoras",
    "gasa": "gasa",
    "leno": "leno",
    "plana": "planas",
    "planas": "planas",
    "cortadora": "cortadoras",
    "cortadoras": "cortadoras",
}

def _send_line_menu(phone_key: str):
    send_whatsapp_message(
        phone_key,
        ("📌 *Selecciona la línea para el informe*:\n"
         "1) Gasa\n"
         "2) Leno\n"
         "3) Planas\n"
         "4) Cortadoras\n\n"
         "👉 Responde con el *número* o el *nombre* de la línea (ej. `1` o `gasa`).")
    )


# ────────────────────────────────────────────────────────────────────────────────
# Utilidades
# ────────────────────────────────────────────────────────────────────────────────
def _only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")

def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    norm_map = {_normalize(c): c for c in df.columns}
    for c in candidates:
        key = _normalize(c)
        if key in norm_map:
            return norm_map[key]
    return None

def _fmt_int(n) -> str:
    try:
        return f"{int(round(float(n))):,}".replace(",", ".")
    except Exception:
        return "0"

def _fmt_float(n, d=2) -> str:
    try:
        return f"{float(n):,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return "0"

def _fmt_pct(v) -> str:
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return "N/D"

def _sanitize_pdf_text(s: str) -> str:
    """Reemplaza caracteres no soportados por 'Helvetica' (evita errores en PDF)."""
    if not s:
        return ""
    repl = {"•": "-", "–": "-", "—": "-", "―": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "\u00A0": " "}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

def _truncate_to_width(pdf: FPDF, text: str, max_w: float) -> str:
    """Recorta texto para que quepa en 'max_w' con '...' (usar tras set_font())."""
    s = _sanitize_pdf_text(text or "")
    if pdf.get_string_width(s) <= max_w:
        return s
    ell = "..."
    while s and pdf.get_string_width(s + ell) > max_w:
        s = s[:-1]
    return (s + ell) if s else ell

# ────────────────────────────────────────────────────────────────────────────────
# Datos
# ────────────────────────────────────────────────────────────────────────────────
def _download_fileteado_df() -> pd.DataFrame | None:
    try:
        resp = requests.get(FILETEADO_XLSX_URL, timeout=60)
        resp.raise_for_status()
        return pd.read_excel(BytesIO(resp.content))  # requiere openpyxl
    except Exception as e:
        print(f"❌ Error descargando base de fileteado: {e}")
        return None

def _date_range_in_df(df: pd.DataFrame) -> tuple[date | None, date | None, str | None]:
    if df is None or df.empty:
        return None, None, None
    fecha_col = _find_col(df, COLS["fecha"])
    if not fecha_col:
        return None, None, None
    s = pd.to_datetime(df[fecha_col], errors="coerce")
    if s.dropna().empty:
        return None, None, fecha_col
    return (s.min().date(), s.max().date(), fecha_col)

def _parse_date_or_range(text: str) -> tuple[date | None, date | None]:
    t = (text or "").strip()
    m = re.fullmatch(r"\s*(\d{4}[-/]\d{2}[-/]\d{2})\s*\Z", t)
    if m:
        d = pd.to_datetime(m.group(1), errors="coerce")
        return (None if pd.isna(d) else d.date(), None if pd.isna(d) else d.date())
    m = re.search(r"(\d{4}[-/]\d{2}[-/]\d{2})\s*(?:a|al|hasta|-|to)\s*(\d{4}[-/]\d{2}[-/]\d{2})", t, flags=re.IGNORECASE)
    if m:
        d1 = pd.to_datetime(m.group(1), errors="coerce")
        d2 = pd.to_datetime(m.group(2), errors="coerce")
        if not pd.isna(d1) and not pd.isna(d2):
            d1, d2 = d1.date(), d2.date()
            if d1 > d2:
                d1, d2 = d2, d1
            return d1, d2
    m = re.search(
        r"desde\s+(\d{4}[-/]\d{2}[-/]\d{2})\s+(?:hasta|a|al|to)\s+(\d{4}[-/]\d{2}[-/]\d{2})",
        t, flags=re.IGNORECASE,
    )
    if m:
        d1 = pd.to_datetime(m.group(1), errors="coerce")
        d2 = pd.to_datetime(m.group(2), errors="coerce")
        if not pd.isna(d1) and not pd.isna(d2):
            d1, d2 = d1.date(), d2.date()
            if d1 > d2:
                d1, d2 = d2, d1
            return d1, d2
    return None, None

def _filter_by_range(df: pd.DataFrame, start: date, end: date, fecha_col: str) -> pd.DataFrame:
    if df is None or df.empty or not fecha_col:
        return df
    s = pd.to_datetime(df[fecha_col], errors="coerce")
    return df.loc[(s.dt.date >= start) & (s.dt.date <= end)].copy()

# ────────────────────────────────────────────────────────────────────────────────
# Cálculos
# ────────────────────────────────────────────────────────────────────────────────
def _starts_with_ci(series: pd.Series, prefix: str) -> pd.Series:
    return series.astype(str).str.upper().str.startswith(prefix.upper())

def _eq_ci(series: pd.Series, value: str) -> pd.Series:
    return series.astype(str).str.strip().str.casefold() == value.strip().casefold()

def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)

def _to_num_locale(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)

def _mask_all(df: pd.DataFrame) -> pd.Series:
    return pd.Series(True, index=df.index)

def _subset_refs_cag(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    c_art = _find_col(df, COLS["articulo"])
    c_lin = _find_col(df, COLS["linea"])
    if not c_art or not c_lin:
        return df.iloc[0:0].copy()
    mask = _eq_ci(df[c_lin], "FILETEAD") & _starts_with_ci(df[c_art], "CAG")
    return df.loc[mask].copy()

def _compute_units_by_lines(df: pd.DataFrame) -> dict:
    res = {k: 0 for k in ["Filete_Gasa", "Filete_Leno", "Planas", "Cortadoras", "Auto_7"]}
    if df is None or df.empty:
        return res
    c_linea = _find_col(df, COLS["linea"])
    c_art   = _find_col(df, COLS["articulo"])
    c_maq   = _find_col(df, COLS["maquina"])
    c_qty   = _find_col(df, COLS["cantidad"])
    if not c_qty:
        return res
    qty = _to_num(df[c_qty])

    mask_gasa = _mask_all(df)
    if c_linea: mask_gasa &= _eq_ci(df[c_linea], "FILETEAD")
    if c_art:   mask_gasa &= _starts_with_ci(df[c_art], "CAG")
    res["Filete_Gasa"] = int(round(qty[mask_gasa].sum()))

    mask_leno = _mask_all(df)
    if c_linea: mask_leno &= _eq_ci(df[c_linea], "FILETEAD")
    if c_art:   mask_leno &= _starts_with_ci(df[c_art], "LEN")
    if c_maq:   mask_leno &= ~_starts_with_ci(df[c_maq], "FIPLA")
    res["Filete_Leno"] = int(round(qty[mask_leno].sum()))

    if c_maq:
        res["Planas"]     = int(round(qty[_starts_with_ci(df[c_maq], "FIPLA")].sum()))
        res["Cortadoras"] = int(round(qty[_starts_with_ci(df[c_maq], "CORT")].sum()))
        res["Auto_7"]     = int(round(qty[_starts_with_ci(df[c_maq], "KOM2000")].sum()))
    return res

def _compute_productivity_filete_gasa(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    c_linea = _find_col(df, COLS["linea"])
    c_art   = _find_col(df, COLS["articulo"])
    c_tc    = _find_col(df, COLS["tc"])
    c_tp    = _find_col(df, COLS["tp"])
    c_cs    = _find_col(df, COLS["cs"])
    if not (c_linea and c_art and c_tc and c_tp and c_cs):
        return None
    mask = _eq_ci(df[c_linea], "FILETEAD") & _starts_with_ci(df[c_art], "CAG")
    tc = _to_num(df.loc[mask, c_tc]).sum()
    tp = _to_num(df.loc[mask, c_tp]).sum()
    cs = _to_num(df.loc[mask, c_cs]).sum()
    denom = tc + tp
    if denom <= 0:
        return None
    return float((cs / denom) * 100.0)

def _downtime_causes_filete_gasa(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["cause", "horas"])
    c_linea = _find_col(df, COLS["linea"])
    c_art   = _find_col(df, COLS["articulo"])
    c_tp    = _find_col(df, COLS["tp"])
    c_cause = _find_col(df, COLS["cause"])
    if not (c_linea and c_art and c_tp and c_cause):
        return pd.DataFrame(columns=["cause", "horas"])
    mask = _eq_ci(df[c_linea], "FILETEAD") & _starts_with_ci(df[c_art], "CAG")
    sub = df.loc[mask, [c_cause, c_tp]].copy()
    sub[c_tp] = _to_num(sub[c_tp])
    sub[c_cause] = sub[c_cause].astype(str).str.strip()
    sub = sub[sub[c_cause] != ""]
    g = sub.groupby(c_cause, dropna=False)[c_tp].sum().reset_index()
    g = g.rename(columns={c_cause: "cause", c_tp: "horas"})
    g["horas"] = pd.to_numeric(g["horas"], errors="coerce").fillna(0.0)
    g = g[g["horas"] > 0]
    return g.sort_values("horas", ascending=False, kind="stable").reset_index(drop=True)

def _table_by_reference(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["referencia", "Cantidad", "T.corrida", "t.perdido", "#_puestos", "%_prod"])
    df = _subset_refs_cag(df)
    if df.empty:
        return pd.DataFrame(columns=["referencia", "Cantidad", "T.corrida", "t.perdido", "#_puestos", "%_prod"])
    c_art = _find_col(df, COLS["articulo"])
    c_qty = _find_col(df, COLS["cantidad"])
    c_tc  = _find_col(df, COLS["tc"])
    c_tp  = _find_col(df, COLS["tp"])
    c_cs  = _find_col(df, COLS["cs"])
    if not (c_art and c_qty and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=["referencia", "Cantidad", "T.corrida", "t.perdido", "#_puestos", "%_prod"])
    g = df.groupby(c_art).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    g = g.rename(columns={c_art: "referencia", c_qty: "Cantidad", c_tc: "T.corrida", c_tp: "t.perdido", c_cs: "Corrida_Standar_sum"})
    for col in ["Cantidad", "T.corrida", "t.perdido", "Corrida_Standar_sum"]:
        g[col] = pd.to_numeric(g[col], errors="coerce").fillna(0.0)
    g["#_puestos"] = (g["T.corrida"] + g["t.perdido"]) / 8.0
    denom = g["T.corrida"] + g["t.perdido"]
    g["%_prod"] = np.where(denom > 0, (g["Corrida_Standar_sum"] / denom) * 100.0, np.nan)
    g = g.drop(columns=["Corrida_Standar_sum"])
    return g.sort_values("Cantidad", ascending=False, kind="stable").reset_index(drop=True)

# ── IMPORTANTE: esta función faltaba (causó el NameError)
def _turno_prod_and_puestos(df: pd.DataFrame) -> pd.DataFrame:
    """
    Por cada Turno (con filtros CAG + FILETEAD):
      prod = sum(cs) / sum(tc+tp) * 100
      puestos = (sum(tc)+sum(tp))/8
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["Turno", "prod", "puestos"])

    df = _subset_refs_cag(df)
    if df.empty:
        return pd.DataFrame(columns=["Turno", "prod", "puestos"])

    c_turno = _find_col(df, COLS["turno"])
    c_tc    = _find_col(df, COLS["tc"])
    c_tp    = _find_col(df, COLS["tp"])
    c_cs    = _find_col(df, COLS["cs"])
    if not (c_turno and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=["Turno", "prod", "puestos"])

    g = df.groupby(c_turno).agg({c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    g = g.rename(columns={c_turno: "Turno", c_tc: "tc", c_tp: "tp", c_cs: "cs"})

    g = g.sort_values("Turno")
    g["puestos"] = (pd.to_numeric(g["tc"], errors="coerce").fillna(0) +
                    pd.to_numeric(g["tp"], errors="coerce").fillna(0)) / 8.0
    denom = pd.to_numeric(g["tc"], errors="coerce").fillna(0) + pd.to_numeric(g["tp"], errors="coerce").fillna(0)
    g["prod"] = np.where(denom > 0, (pd.to_numeric(g["cs"], errors="coerce").fillna(0) / denom) * 100.0, np.nan)
    return g[["Turno", "prod", "puestos"]]

# ── Tabla Operario TOTAL (sin referencia)
def _table_operario_total(df: pd.DataFrame) -> pd.DataFrame:
    """
    Columnas: Operario | Cantidad | T.corrida | T.perdido | Productividad | Perdida
    Filtros: rango aplicado + FILETEAD + Numero_Articulo empieza por CAG/cag.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=["Operario", "Cantidad", "T.corrida", "T.perdido", "Productividad", "Perdida"])
    df = _subset_refs_cag(df)
    if df.empty:
        return pd.DataFrame(columns=["Operario", "Cantidad", "T.corrida", "T.perdido", "Productividad", "Perdida"])

    c_op  = _find_col(df, COLS["operario"])
    c_qty = _find_col(df, COLS["cantidad"])
    c_tc  = _find_col(df, COLS["tc"])
    c_tp  = _find_col(df, COLS["tp"])
    c_cs  = _find_col(df, COLS["cs"])
    if not (c_op and c_qty and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=["Operario", "Cantidad", "T.corrida", "T.perdido", "Productividad", "Perdida"])

    df = df.copy()
    df[c_op] = df[c_op].astype(str).str.strip()

    g = df.groupby(c_op).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    g = g.rename(columns={c_op: "Operario", c_qty: "Cantidad", c_tc: "T.corrida", c_tp: "T.perdido", c_cs: "Corrida_Standar_sum"})

    for col in ["Cantidad", "T.corrida", "T.perdido", "Corrida_Standar_sum"]:
        g[col] = _to_num_locale(g[col])

    denom = g["T.corrida"] + g["T.perdido"]
    g["Productividad"] = np.where(denom > 0, (g["Corrida_Standar_sum"] / denom) * 100.0, np.nan)
    g["Perdida"] = g["Cantidad"] - (denom * 375.0)

    g = g.drop(columns=["Corrida_Standar_sum"])
    return g.sort_values(["Cantidad", "Operario"], ascending=[False, True], kind="stable").reset_index(drop=True)

# ────────────────────────────────────────────────────────────────────────────────
# Gráficos
# ────────────────────────────────────────────────────────────────────────────────
def _plot_gauge_percent(value: float | None, title: str) -> tuple[str | None, float]:
    if value is None or (isinstance(value, float) and (np.isnan(value) or np.isinf(value))):
        value = 0.0
    v = max(0.0, min(100.0, float(value)))
    fig_w, fig_h = 3.2, 2.3
    aspect = fig_h / fig_w
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    ax.set_aspect('equal')
    for start, end, color in [(-90, -30, "#d9534f"), (-30, 30, "#f0ad4e"), (30, 90, "#5cb85c")]:
        ax.add_patch(Wedge((0, 0), 1.0, start, end, facecolor=color, edgecolor="none"))
    ax.add_patch(Wedge((0, 0), 0.85, -90, 90, facecolor="white", edgecolor="white"))
    angle = (v / 100.0) * 180.0 - 90.0
    theta = np.deg2rad(angle)
    ax.plot([0, 0.8*np.cos(theta)], [0, 0.8*np.sin(theta)], lw=3, color="black")
    ax.add_patch(Circle((0, 0), 0.05, color="black"))
    ax.text(0, -0.20, f"{v:.1f}%", ha="center", va="center", fontsize=14)
    ax.set_xlim(-1.1, 1.1); ax.set_ylim(-0.3, 1.1); ax.axis("off"); ax.set_title(title, fontsize=10)
    fname = f"gauge_filete_gasa_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
    out_path = os.path.join(REPORTS_DIR, fname)
    plt.savefig(out_path, bbox_inches="tight"); plt.close(fig)
    return out_path, aspect

def _plot_downtime_bars_horizontal(df_cause: pd.DataFrame) -> tuple[str | None, float]:
    if df_cause is None or df_cause.empty:
        return None, 0.0
    df = df_cause.sort_values("horas", ascending=False, kind="stable").copy()
    labels = df["cause"].astype(str).tolist()
    horas = df["horas"].tolist()
    fig_w, fig_h = 6.4, max(2.2, 0.35 * max(1, len(labels)) + 1.0)
    aspect = fig_h / fig_w
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    y = np.arange(len(labels))
    ax.barh(y, horas); ax.set_yticks(y); ax.set_yticklabels(labels)
    ax.invert_yaxis(); ax.set_xlabel("Horas"); ax.set_title("Causas de tiempo perdido (Filete Gasa)")
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    fname = f"barras_causas_filete_gasa_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
    out_path = os.path.join(REPORTS_DIR, fname)
    plt.tight_layout(); plt.savefig(out_path, bbox_inches="tight"); plt.close(fig)
    return out_path, aspect

def _plot_turno_bars_and_puestos(df_turno: pd.DataFrame, title: str = "Productividad por turno y # puestos (CAG)") -> tuple[str | None, float]:
    if df_turno is None or df_turno.empty:
        return None, 0.0
    df = df_turno.copy()
    try: df = df.sort_values("Turno")
    except Exception: pass
    xlabels = df["Turno"].astype(str).tolist()
    prod = df["prod"].astype(float).tolist()
    puestos = df["puestos"].astype(float).tolist()
    fig_w, fig_h = 6.0, 3.4
    aspect = fig_h / fig_w
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    x = np.arange(max(1, len(xlabels)))
    ax.bar(x, prod); ax.set_xticks(x); ax.set_xticklabels(xlabels, rotation=0)
    ax.set_ylabel("Productividad (%)"); ax.set_title(title)
    ax2 = ax.twinx()
    ax2.plot(x, puestos, marker="o", linewidth=2, color="#d9534f", markerfacecolor="#d9534f", zorder=5)
    ax2.set_ylabel("# Puestos")
    for xi, yi in zip(x, puestos):
        ax2.annotate(f"{yi:.1f}", (xi, yi), textcoords="offset points", xytext=(0, 6),
                     ha="center", fontsize=8, color="#d9534f")
    if len(puestos) > 0:
        ymin = max(0.0, min(puestos) - 1.0); ymax = max(puestos) + 1.0
        ax2.set_ylim(ymin, ymax)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    fname = f"turnos_prod_puestos_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
    out_path = os.path.join(REPORTS_DIR, fname)
    plt.tight_layout(); plt.savefig(out_path, bbox_inches="tight"); plt.close(fig)
    return out_path, aspect

# ────────────────────────────────────────────────────────────────────────────────
# PDF helpers
# ────────────────────────────────────────────────────────────────────────────────
def _render_units_row(pdf: FPDF, units: dict):
    labels = ["Filete_Gasa", "Filete_Leno", "Planas", "Cortadoras", "Auto_7"]
    nice = {"Filete_Gasa": "Filete_Gasa", "Filete_Leno": "Filete_Leno", "Planas": "Planas", "Cortadoras": "Cortadoras", "Auto_7": "Auto 7"}
    page_w = pdf.w - 2 * pdf.l_margin
    col_w = page_w / len(labels)
    pdf.set_font("Helvetica", "B", 14); pdf.cell(0, 8, _sanitize_pdf_text("Unidades producidas por línea"), ln=1); pdf.ln(1)
    pdf.set_font("Helvetica", "B", 11)
    for k in labels: pdf.cell(col_w, 7, _sanitize_pdf_text(nice[k]), align="C", border=0)
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 12)
    for k in labels: pdf.cell(col_w, 8, _sanitize_pdf_text(_fmt_int(units.get(k, 0))), align="C", border=0)
    pdf.ln(10)

def _render_table_refs(pdf: FPDF, df: pd.DataFrame, x: float, y: float, w: float, max_rows: int = 16) -> float:
    pdf.set_xy(x, y); pdf.set_font("Helvetica", "B", 13)
    pdf.cell(w, 7, _sanitize_pdf_text("Detalle por referencia (CAG)"), ln=1)
    if df is None or df.empty:
        pdf.set_font("Helvetica", "", 10); pdf.set_xy(x, pdf.get_y())
        pdf.multi_cell(w, 6, _sanitize_pdf_text("Sin datos para el periodo."), border=1); return 6 + 8
    df2 = df.copy(); extra_rows = 0
    if len(df2) > max_rows:
        extra = df2.iloc[max_rows:].copy()
        agg_tc = float(pd.to_numeric(extra["T.corrida"], errors="coerce").fillna(0).sum())
        agg_tp = float(pd.to_numeric(extra["t.perdido"], errors="coerce").fillna(0).sum())
        agg_qty = float(pd.to_numeric(extra["Cantidad"], errors="coerce").fillna(0).sum())
        weights = (pd.to_numeric(extra["T.corrida"], errors="coerce").fillna(0) + pd.to_numeric(extra["t.perdido"], errors="coerce").fillna(0))
        weighted_prod = (pd.to_numeric(extra["%_prod"], errors="coerce").fillna(0) * weights).sum()
        denom_w = weights.sum(); agg_prod = float(weighted_prod / denom_w) if denom_w > 0 else 0.0
        agg_row = {"referencia": "Otros", "Cantidad": agg_qty, "T.corrida": agg_tc, "t.perdido": agg_tp, "#_puestos": (agg_tc + agg_tp) / 8.0, "%_prod": agg_prod}
        df2 = pd.concat([df2.iloc[:max_rows].copy(), pd.DataFrame([agg_row])], ignore_index=True); extra_rows = len(extra)
    headers = ["referencia", "Cantidad", "T.corrida", "t.perdido", "#_puestos", "%_prod"]
    rel = [0.28, 0.14, 0.16, 0.16, 0.14, 0.12]; widths = [w * r for r in rel]
    pdf.set_xy(x, pdf.get_y()); pdf.set_font("Helvetica", "B", 9)
    for h, width in zip(["Referencia", "Cantidad", "T.corrida", "t.perdido", "# puestos", "% prod"], widths):
        pdf.cell(width, 6, _sanitize_pdf_text(h), border=1, align="C")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 9); start_y = pdf.get_y()
    for _, r in df2.iterrows():
        row = [str(r.get("referencia", "")), _fmt_int(r.get("Cantidad", 0)), _fmt_float(r.get("T.corrida", 0), 2),
               _fmt_float(r.get("t.perdido", 0), 2), _fmt_float(r.get("#_puestos", 0), 2), _fmt_pct(r.get("%_prod", 0))]
        pdf.set_x(x)
        for text, width in zip(row, widths): pdf.cell(width, 6, _sanitize_pdf_text(text), border=1, align="C")
        pdf.ln(6)
    used_h = pdf.get_y() - start_y + 6 + 7
    if extra_rows > 0:
        pdf.set_x(x); pdf.set_font("Helvetica", "I", 8)
        pdf.cell(w, 5, _sanitize_pdf_text(f"(*) Se agruparon {extra_rows} referencias en 'Otros'."), ln=1); used_h += 5
    return used_h

def _render_table_operario_total(pdf: FPDF, df: pd.DataFrame, x: float, y: float, w: float, max_rows: int = 25) -> float:
    """
    Renderiza la tabla de Operarios TOTALIZADA (sin columna 'Referencia').
    Muestra TODOS los operarios (no agrupa en 'Otros'), con salto de página y
    encabezado repetido en cada página. El parámetro max_rows se ignora.
    """
    def _draw_header():
        pdf.set_font("Helvetica", "B", 9)
        for h, width in zip(["Operario", "Cantidad", "T.corrida", "T.perdido", "% prod", "Pérdida"], widths):
            pdf.cell(width, 6, _sanitize_pdf_text(h), border=1, align="C")
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 9)

    # Título
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(w, 7, _sanitize_pdf_text("Detalle por operario (CAG)"), ln=1)

    if df is None or df.empty:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_xy(x, pdf.get_y())
        pdf.multi_cell(w, 6, _sanitize_pdf_text("Sin datos para el periodo."), border=1)
        return 6 + 8

    # Anchos de columnas (Operario más ancho)
    rel = [0.38, 0.12, 0.14, 0.14, 0.10, 0.12]
    widths = [w * r for r in rel]
    row_h = 6
    bottom_y = pdf.h - pdf.b_margin

    # Encabezado inicial
    _draw_header()

    start_y = pdf.get_y()

    # Filas (TODAS)
    for _, r in df.iterrows():
        # ¿Cabe la siguiente fila? Si no, nueva página y encabezado
        if pdf.get_y() + row_h > bottom_y:
            pdf.add_page()
            pdf.set_xy(x, pdf.get_y())
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(w, 7, _sanitize_pdf_text("Detalle por operario (CAG) (cont.)"), ln=1)
            _draw_header()

        operario = str(r.get("Operario", ""))

        pdf.set_x(x)
        # Operario (recortado a ancho)
        pdf.cell(widths[0], row_h, _truncate_to_width(pdf, operario, widths[0]-2), border=1, align="L")

        # Resto de columnas
        pdf.cell(widths[1], row_h, _sanitize_pdf_text(_fmt_int(r.get("Cantidad", 0))),     border=1, align="C")
        pdf.cell(widths[2], row_h, _sanitize_pdf_text(_fmt_float(r.get("T.corrida", 0), 2)), border=1, align="C")
        pdf.cell(widths[3], row_h, _sanitize_pdf_text(_fmt_float(r.get("T.perdido", 0), 2)), border=1, align="C")
        pdf.cell(widths[4], row_h, _sanitize_pdf_text(_fmt_pct(r.get("Productividad", 0))), border=1, align="C")
        pdf.cell(widths[5], row_h, _sanitize_pdf_text(_fmt_int(r.get("Perdida", 0))),       border=1, align="C")
        pdf.ln(row_h)

    # Altura usada en la primera página (no es crítica para el flujo actual)
    used_h = pdf.get_y() - start_y + 6 + 7
    return used_h

def _render_signature(pdf: FPDF):
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 8, _sanitize_pdf_text("Informe Generado por CIPLASBOT - Agente I.A - Creado por Ing. Wilson Calderon"),
             ln=1, align="C")
# ────────────────────────────────────────────────────────────────────────────────
# PDF principal
# ────────────────────────────────────────────────────────────────────────────────
def _build_pdf_fileteado(start: date, end: date, units: dict,
                         prod_gasa: float | None, gauge_info: tuple[str | None, float],
                         bars_info: tuple[str | None, float],
                         df_refs: pd.DataFrame,
                         turno_chart_info: tuple[str | None, float],
                         df_oper_total: pd.DataFrame) -> str:
    title = "ANALISIS PROCESO FILETEADO"

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
    pdf.ln(2)

    _render_units_row(pdf, units)

    pdf.ln(2); pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _sanitize_pdf_text("Filete Gasa"), ln=1)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, _sanitize_pdf_text(f"Productividad del periodo: {_fmt_pct(prod_gasa)}"), ln=1)

    y0 = pdf.get_y(); gap = 6.0
    gauge_path, gauge_ar = gauge_info; bars_path, bars_ar = bars_info
    gauge_w = page_w * 0.34; bars_w = page_w - gauge_w - gap
    max_h = 0.0
    if gauge_path and os.path.exists(gauge_path):
        gauge_h = gauge_w * (gauge_ar if gauge_ar > 0 else 0.7)
        pdf.image(gauge_path, x=pdf.l_margin, y=y0, w=gauge_w); max_h = max(max_h, gauge_h)
    if bars_path and os.path.exists(bars_path):
        bars_h = bars_w * (bars_ar if bars_ar > 0 else 0.45)
        pdf.image(bars_path, x=pdf.l_margin + gauge_w + gap, y=y0, w=bars_w); max_h = max(max_h, bars_h)
    if max_h > 0: pdf.set_y(y0 + max_h + 6)

    pdf.ln(2)
    y1 = pdf.get_y(); left_w = page_w * 0.55; right_w = page_w - left_w - gap
    used_h_table = _render_table_refs(pdf, df_refs, pdf.l_margin, y1, left_w, max_rows=16)
    turno_path, turno_ar = turno_chart_info; used_h_chart = 0.0
    if turno_path and os.path.exists(turno_path):
        chart_h = right_w * (turno_ar if turno_ar > 0 else 0.6)
        pdf.image(turno_path, x=pdf.l_margin + left_w + gap, y=y1 + 9, w=right_w); used_h_chart = chart_h + 9
    pdf.set_y(y1 + max(used_h_table, used_h_chart) + 6)

    y2 = pdf.get_y()
    _render_table_operario_total(pdf, df_oper_total, pdf.l_margin, y2, page_w, max_rows=25)

    # Firma
    _render_signature(pdf)


    fname = f"Analisis_Fileteado_{start.isoformat()}_{end.isoformat()}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)
    pdf.output(out_path)
    return out_path

# ────────────────────────────────────────────────────────────────────────────────
# Flujo / Handler
# ────────────────────────────────────────────────────────────────────────────────
# ────────────────────────────────────────────────────────────────────────────────
# Admin / permisos
# ────────────────────────────────────────────────────────────────────────────────
def _is_admin_phone(phone_raw: str) -> bool:
    """Admin si role == 'Administrador' (case-insensitive) o is_admin true."""
    try:
        with open(SUPERVISORS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f).get("users", [])
    except Exception as e:
        print(f"⚠️ No se pudo leer SUPERVISORS_FILE: {e}")
        return False

    # Normaliza el número entrante
    in_digits = re.sub(r"\D", "", canon_phone_e164_co(phone_raw) or phone_raw)

    for u in users:
        raw = u.get("phone_e164") or u.get("phone") or ""
        dig = re.sub(r"\D", "", canon_phone_e164_co(raw) or raw)
        role = (u.get("role") or "").strip().casefold()
        is_admin_flag = bool(u.get("is_admin"))
        if dig and dig == in_digits and (role == "administrador" or is_admin_flag):
            return True

    return False

def handle_fileteado_message(phone_key: str, text: str) -> bool:
    msg = (text or "").strip()
    low = msg.lower()
    state = sessions.setdefault(phone_key, {}).get("fileteado_state", {})

    # 1) Comando inicial: prepara estado y pide rango
    if low == "informe fileteado":
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

        sessions.setdefault(phone_key, {})["fileteado_state"] = {
            "awaiting_range": True,
            "awaiting_line_choice": False,
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

    # 2) Recibe el rango y pregunta la línea
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

        st = sessions.setdefault(phone_key, {}).setdefault("fileteado_state", {})
        st["awaiting_range"] = False
        st["awaiting_line_choice"] = True
        st["chosen_start"] = start.isoformat()
        st["chosen_end"] = end.isoformat()

        _send_line_menu(phone_key)
        return True

    # 3) Recibe la selección de línea y genera el informe
    if state.get("awaiting_line_choice"):
        choice = LINE_CHOICES.get(low.strip())
        if choice is None:
            _send_line_menu(phone_key)
            return True

        # Recupera rango almacenado
        try:
            start = pd.to_datetime(state.get("chosen_start")).date()
            end   = pd.to_datetime(state.get("chosen_end")).date()
        except Exception:
            sessions[phone_key].pop("fileteado_state", None)
            send_whatsapp_message(phone_key, "⚠️ Hubo un problema con el rango almacenado. Escribe de nuevo *informe fileteado*.")
            return True

        # Carga base y filtra por rango
        df = _download_fileteado_df()
        if df is None or df.empty:
            send_whatsapp_message(phone_key, "❌ No pude leer la base de fileteado en este momento.")
            sessions[phone_key].pop("fileteado_state", None)
            return True

        fecha_col = state.get("fecha_col") or _find_col(df, COLS["fecha"])
        if not fecha_col:
            send_whatsapp_message(phone_key, "ℹ️ No encontré una columna de fecha válida en la base.")
            sessions[phone_key].pop("fileteado_state", None)
            return True

        df_sel = _filter_by_range(df, start, end, fecha_col)

        # ── PLANAS
        if choice == "planas":
            try:
                from workflows.fileteado_planas import build_pdf_planas
                pdf_path, temp_paths = build_pdf_planas(df_sel, start, end)
            except Exception as e:
                print(f"❌ Error generando PLANAS: {e}")
                send_whatsapp_message(phone_key, "❌ Hubo un problema generando el informe PLANAS.")
                return True

            try:
                send_whatsapp_document(phone_key, pdf_path, caption="📄 Análisis proceso fileteado – PLANAS")
            except Exception as e:
                send_whatsapp_message(phone_key, f"❌ No pude enviar el informe PLANAS: {e}")

            for p in [pdf_path, *(temp_paths or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception as ex:
                    print(f"⚠️ No se pudo eliminar '{p}': {ex}")

            sessions[phone_key].pop("fileteado_state", None)
            return True

        # ── LENO
        elif choice == "leno":
            try:
                from workflows.fileteado_leno import build_pdf_leno
                pdf_path, temp_paths = build_pdf_leno(df_sel, start, end)
            except Exception as e:
                print(f"❌ Error generando LENO: {e}")
                send_whatsapp_message(phone_key, "❌ Hubo un problema generando el informe LENO.")
                return True

            try:
                send_whatsapp_document(phone_key, pdf_path, caption="📄 Análisis proceso fileteado – LENO")
            except Exception as e:
                send_whatsapp_message(phone_key, f"❌ No pude enviar el informe LENO: {e}")

            for p in [pdf_path, *(temp_paths or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception as ex:
                    print(f"⚠️ No se pudo eliminar '{p}': {ex}")

            sessions[phone_key].pop("fileteado_state", None)
            return True

        # ── CORTADORAS
        elif choice == "cortadoras":
            try:
                from workflows.fileteado_cortadoras import build_pdf_cortadoras
                pdf_path, temp_paths = build_pdf_cortadoras(df_sel, start, end)
            except Exception as e:
                print(f"❌ Error generando CORTADORAS: {e}")
                send_whatsapp_message(phone_key, "❌ Hubo un problema generando el informe CORTADORAS.")
                return True

            try:
                send_whatsapp_document(phone_key, pdf_path, caption="📄 Análisis proceso fileteado – CORTADORAS")
            except Exception as e:
                send_whatsapp_message(phone_key, f"❌ No pude enviar el informe CORTADORAS: {e}")

            for p in [pdf_path, *(temp_paths or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception as ex:
                    print(f"⚠️ No se pudo eliminar '{p}': {ex}")

            sessions[phone_key].pop("fileteado_state", None)
            return True

        # ── GASA (generación interna)
        elif choice == "gasa":
            try:
                from workflows.fileteado_gasa import build_pdf_gasa
                pdf_path, temp_paths = build_pdf_gasa(df_sel, start, end)
            except Exception as e:
                print(f"❌ Error generando GASA: {e}")
                send_whatsapp_message(phone_key, "❌ Hubo un problema generando el informe GASA.")
                return True

            try:
                send_whatsapp_document(phone_key, pdf_path, caption="📄 Análisis proceso fileteado – GASA")
            except Exception as e:
                send_whatsapp_message(phone_key, f"❌ No pude enviar el informe GASA: {e}")

            for p in [pdf_path, *(temp_paths or [])]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception as ex:
                    print(f"⚠️ No se pudo eliminar '{p}': {ex}")

            sessions[phone_key].pop("fileteado_state", None)
            return True

        else:
            _send_line_menu(phone_key)
            return True

    # No lo manejo
    return False

