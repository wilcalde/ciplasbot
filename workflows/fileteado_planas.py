# workflows/fileteado_planas.py
import os
import re
import sqlite3
from datetime import datetime, date

import numpy as np
import pandas as pd

# PDF
from fpdf import FPDF
# Gráficos
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Wedge, Circle

from services.session_memory import CONFIG_DIR

# ────────────────────────────────────────────────────────────────────────────────
# Constantes / rutas
# ────────────────────────────────────────────────────────────────────────────────
REPORTS_DIR = os.path.join(CONFIG_DIR, "fileteado_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)
OPERARIOS_DB_FILENAME = "base_conversion_eficiencias_conversion.db"
OPERARIOS_DB_PATH = os.path.join(CONFIG_DIR, "task", OPERARIOS_DB_FILENAME)
OPERARIOS_DB_FALLBACK = os.path.join(CONFIG_DIR, "tasks", OPERARIOS_DB_FILENAME)

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
# Utils
# ────────────────────────────────────────────────────────────────────────────────
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

def _starts_with_ci(series: pd.Series, prefix: str) -> pd.Series:
    return series.astype(str).str.upper().str.startswith(prefix.upper())

def _eq_ci(series: pd.Series, value: str) -> pd.Series:
    return series.astype(str).str.strip().str.casefold() == value.strip().casefold()

def _connect_sqlite(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)

def _quote_identifier(name: str) -> str:
    escaped = name.replace('"', '""')
    return f"\"{escaped}\""

def _inspect_sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
    return [r[0] for r in cur.fetchall()]

def _get_table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.execute(f"PRAGMA table_info({_quote_identifier(table)});")
    return [r[1] for r in cur.fetchall()]

def _month_window_bounds(end: date, months: int = 4) -> tuple[date, date, list[pd.Period]]:
    end_period = pd.Period(end, freq="M")
    start_period = end_period - (months - 1)
    periods = list(pd.period_range(start=start_period, end=end_period, freq="M"))
    start_date = start_period.to_timestamp(how="start").date()
    end_date = end_period.to_timestamp(how="end").date()
    return start_date, end_date, periods

def _resolve_operarios_db_path() -> str | None:
    if os.path.exists(OPERARIOS_DB_PATH):
        return OPERARIOS_DB_PATH
    if os.path.exists(OPERARIOS_DB_FALLBACK):
        return OPERARIOS_DB_FALLBACK
    for root, _dirs, files in os.walk(CONFIG_DIR):
        if OPERARIOS_DB_FILENAME in files:
            return os.path.join(root, OPERARIOS_DB_FILENAME)
    return None

def _detect_efficiency_source(conn: sqlite3.Connection) -> dict | None:
    operario_candidates = [
        "operario",
        "nombre",
        "nombre_operario",
        "apellidos_nombres",
        "apellidos y nombres",
        "operador",
        "empleado",
    ]
    fecha_candidates = ["fecha", "fecha_registro", "fecha_efectiva", "date", "datetime", "timestamp", "mes", "periodo"]
    eficiencia_candidates = [
        "eficiencia",
        "eficiencia_operario",
        "efficiency",
        "rendimiento",
        "productividad",
        "efic",
        "efic_",
        "%efic",
        "porcentaje_eficiencia",
        "porcentaje_efic",
    ]

    best = None
    best_score = -1
    for table in _inspect_sqlite_tables(conn):
        cols = _get_table_columns(conn, table)
        if not cols:
            continue
        norm_map = {_normalize(c): c for c in cols}
        c_operario = next((norm_map.get(_normalize(c)) for c in operario_candidates if _normalize(c) in norm_map), None)
        c_fecha = next((norm_map.get(_normalize(c)) for c in fecha_candidates if _normalize(c) in norm_map), None)
        c_eff = next((norm_map.get(_normalize(c)) for c in eficiencia_candidates if _normalize(c) in norm_map), None)
        if not (c_operario and c_fecha and c_eff):
            continue
        score = 1
        if score > best_score:
            best_score = score
            best = {
                "table": table,
                "operario": c_operario,
                "fecha": c_fecha,
                "eficiencia": c_eff,
            }
    return best

def _detect_efficiency_wide_table(conn: sqlite3.Connection) -> dict | None:
    operario_candidates = [
        "operario",
        "nombre",
        "nombre_operario",
        "apellidos_nombres",
        "apellidos y nombres",
        "operador",
        "empleado",
    ]
    eff_keywords = ("eficiencia", "efic")

    best = None
    best_score = -1
    for table in _inspect_sqlite_tables(conn):
        cols = _get_table_columns(conn, table)
        if not cols:
            continue
        norm_map = {_normalize(c): c for c in cols}
        c_operario = next((norm_map.get(_normalize(c)) for c in operario_candidates if _normalize(c) in norm_map), None)
        if not c_operario:
            continue
        eff_cols = [c for c in cols if any(k in _normalize(c) for k in eff_keywords)]
        if len(eff_cols) < 2:
            continue
        score = len(eff_cols)
        if score > best_score:
            best_score = score
            best = {"table": table, "operario": c_operario, "eff_cols": eff_cols}
    return best

def _parse_month_label(label: str) -> pd.Period | None:
    if not label:
        return None
    text = label.strip().lower()
    month_map = {
        "enero": 1, "ene": 1,
        "febrero": 2, "feb": 2,
        "marzo": 3, "mar": 3,
        "abril": 4, "abr": 4,
        "mayo": 5, "may": 5,
        "junio": 6, "jun": 6,
        "julio": 7, "jul": 7,
        "agosto": 8, "ago": 8, "aug": 8,
        "septiembre": 9, "setiembre": 9, "sep": 9, "sept": 9,
        "octubre": 10, "oct": 10,
        "noviembre": 11, "nov": 11,
        "diciembre": 12, "dic": 12, "dec": 12,
    }
    m = re.search(r"([a-zñ]+)[\\s_-]*([0-9]{2,4})", text)
    if not m:
        return None
    mes_txt = m.group(1)
    year_txt = m.group(2)
    if mes_txt not in month_map:
        return None
    year = int(year_txt)
    if year < 100:
        year += 2000
    month = month_map[mes_txt]
    return pd.Period(f"{year:04d}-{month:02d}", freq="M")

def _read_efficiency_history_from_sqlite(end_date: date, months: int = 4) -> pd.DataFrame:
    db_path = _resolve_operarios_db_path()
    if not db_path:
        return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

    month_names = {
        1: "enero",
        2: "febrero",
        3: "marzo",
        4: "abril",
        5: "mayo",
        6: "junio",
        7: "julio",
        8: "agosto",
        9: "septiembre",
        10: "octubre",
        11: "noviembre",
        12: "diciembre",
    }
    _, _, periods = _month_window_bounds(end_date, months=months)
    eff_cols = [f"eficiencia_{month_names[p.month]}_{p.year}" for p in periods]

    conn = _connect_sqlite(db_path)
    try:
        tables = _inspect_sqlite_tables(conn)
        if not tables:
            return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])
        table = tables[0]
        cols = _get_table_columns(conn, table)
        if not cols:
            return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

        norm_map = {_normalize(c): c for c in cols}
        c_nombre = norm_map.get("nombre")
        c_area = norm_map.get("area")
        if not (c_nombre and c_area):
            return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

        selected_eff_cols = [norm_map.get(_normalize(c)) for c in eff_cols if norm_map.get(_normalize(c))]
        if not selected_eff_cols:
            return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

        query_cols = [c_nombre, c_area, *selected_eff_cols]
        query = f"SELECT {', '.join(_quote_identifier(c) for c in query_cols)} FROM {_quote_identifier(table)}"
        df = pd.read_sql_query(query, conn)
        if df.empty:
            return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

        df[c_area] = df[c_area].astype(str).str.strip().str.casefold()
        df = df[df[c_area] == "fileteado"]
        if df.empty:
            return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

        df = df.rename(columns={c_nombre: "Operario"})
        df["Operario"] = df["Operario"].astype(str).str.strip()
        melt_df = df.melt(id_vars=["Operario"], value_vars=selected_eff_cols,
                          var_name="Mes", value_name="eficiencia_mes")
        melt_df["Mes"] = melt_df["Mes"].astype(str)
        melt_df["eficiencia_mes"] = pd.to_numeric(melt_df["eficiencia_mes"], errors="coerce")
        return melt_df.dropna(subset=["Operario", "Mes"])
    finally:
        conn.close()

def _trend_labeler(end_date: date, months: int = 4):
    df_hist = _read_efficiency_history_from_sqlite(end_date, months=months)
    if df_hist.empty:
        def _label(_: str) -> str:
            return "Sin historial"
        return _label

    _, _, periods = _month_window_bounds(end_date, months=months)
    period_keys = [str(p) for p in periods]
    df_hist = df_hist.copy()
    df_hist["Operario_norm"] = df_hist["Operario"].astype(str).str.strip().str.casefold()
    df_hist["Mes"] = df_hist["Mes"].astype(str)
    df_hist = df_hist.groupby(["Operario_norm", "Mes"], as_index=False)["eficiencia_mes"].mean()

    def _label(nombre: str) -> str:
        op_key = (nombre or "").strip().casefold()
        sub = df_hist[df_hist["Operario_norm"] == op_key]
        if sub.empty:
            return "Sin historial"
        valores = []
        for period in period_keys:
            match = sub[sub["Mes"] == period]["eficiencia_mes"]
            if match.empty:
                return "Datos insuficientes (<4)"
            valores.append(float(match.mean()))
        if len(valores) < months:
            return "Datos insuficientes (<4)"
        primeros_prom = sum(valores[:2]) / 2.0
        ultimos_prom = sum(valores[-2:]) / 2.0
        diferencia = ultimos_prom - primeros_prom
        promedio_4 = sum(valores) / len(valores)
        if diferencia > 0.5:
            return f"Mejora (+{diferencia:.1f} pp, Prom 4m {promedio_4:.1f}%)"
        if diferencia < -0.5:
            return f"Decreciente ({diferencia:.1f} pp, Prom 4m {promedio_4:.1f}%)"
        return f"Neutro ({diferencia:+.1f} pp, Prom 4m {promedio_4:.1f}%)"

    return _label

def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(0)

def _to_num_locale(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(",", ".", regex=False)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)

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
    if not s:
        return ""
    repl = {"•": "-", "–": "-", "—": "-", "―": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "\u00A0": " "}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

def _truncate_to_width(pdf: FPDF, text: str, max_w: float) -> str:
    s = _sanitize_pdf_text(text or "")
    if pdf.get_string_width(s) <= max_w:
        return s
    ell = "..."
    while s and pdf.get_string_width(s + ell) > max_w:
        s = s[:-1]
    return (s + ell) if s else ell

# ────────────────────────────────────────────────────────────────────────────────
# Subset PLANAS (exactamente: Linea FILETEAD + Maquina FIPLA*)
# ────────────────────────────────────────────────────────────────────────────────
def _subset_refs_planas(df: pd.DataFrame) -> pd.DataFrame:
    """Filtra únicamente FILETEAD + máquinas que empiezan por FIPLA (todas las FIPLA*)."""
    if df is None or df.empty:
        return df
    c_lin = _find_col(df, COLS["linea"])
    c_maq = _find_col(df, COLS["maquina"])
    if not (c_lin and c_maq):
        return df.iloc[0:0].copy()
    mask = _eq_ci(df[c_lin], "FILETEAD")
    mask &= _starts_with_ci(df[c_maq], "FIPLA")   # ← TODAS las FIPLA*
    return df.loc[mask].copy()

# ────────────────────────────────────────────────────────────────────────────────
# Métricas PLANAS
# ────────────────────────────────────────────────────────────────────────────────
def _compute_units_by_lines_planas(df: pd.DataFrame) -> dict:
    """
    Resumen para la fila 'Unidades producidas por línea'.
    Aquí Planas = máquinas FIPLA* (todas), consistente con el informe.
    """
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

    # Gasa
    mask_gasa = pd.Series(True, index=df.index)
    if c_linea: mask_gasa &= _eq_ci(df[c_linea], "FILETEAD")
    if c_art:   mask_gasa &= _starts_with_ci(df[c_art], "CAG")
    res["Filete_Gasa"] = int(round(qty[mask_gasa].sum()))

    # Leno (misma lógica que usas en GASA/LENO generales)
    mask_leno = pd.Series(True, index=df.index)
    if c_linea: mask_leno &= _eq_ci(df[c_linea], "FILETEAD")
    if c_art:   mask_leno &= _starts_with_ci(df[c_art], "LEN")
    if c_maq:   mask_leno &= ~_starts_with_ci(df[c_maq], "FIPLA")  # excluye planas
    res["Filete_Leno"] = int(round(qty[mask_leno].sum()))

    # Planas -> FIPLA*
    if c_maq:
        res["Planas"]     = int(round(qty[_starts_with_ci(df[c_maq], "FIPLA")].sum()))
        res["Cortadoras"] = int(round(qty[_starts_with_ci(df[c_maq], "CORT")].sum()))
        res["Auto_7"]     = int(round(qty[_starts_with_ci(df[c_maq], "KOM2000")].sum()))
    return res

def _compute_productivity_filete_planas(df: pd.DataFrame) -> float | None:
    sub = _subset_refs_planas(df)
    if sub.empty:
        return None
    c_tc = _find_col(sub, COLS["tc"])
    c_tp = _find_col(sub, COLS["tp"])
    c_cs = _find_col(sub, COLS["cs"])
    if not (c_tc and c_tp and c_cs):
        return None
    tc = _to_num(sub[c_tc]).sum()
    tp = _to_num(sub[c_tp]).sum()
    cs = _to_num(sub[c_cs]).sum()
    denom = tc + tp
    if denom <= 0:
        return None
    return float((cs / denom) * 100.0)

def _downtime_causes_filete_planas(df: pd.DataFrame) -> pd.DataFrame:
    sub = _subset_refs_planas(df)
    if sub.empty:
        return pd.DataFrame(columns=["cause", "horas"])
    c_tp = _find_col(sub, COLS["tp"])
    c_cause = _find_col(sub, COLS["cause"])
    if not (c_tp and c_cause):
        return pd.DataFrame(columns=["cause", "horas"])
    tmp = sub[[c_cause, c_tp]].copy()
    tmp[c_tp] = _to_num(tmp[c_tp])
    tmp[c_cause] = tmp[c_cause].astype(str).str.strip()
    tmp = tmp[tmp[c_cause] != ""]
    g = tmp.groupby(c_cause, dropna=False)[c_tp].sum().reset_index()
    g = g.rename(columns={c_cause: "cause", c_tp: "horas"})
    g["horas"] = pd.to_numeric(g["horas"], errors="coerce").fillna(0.0)
    g = g[g["horas"] > 0]
    return g.sort_values("horas", ascending=False, kind="stable").reset_index(drop=True)

def _table_by_reference_planas(df: pd.DataFrame) -> pd.DataFrame:
    sub = _subset_refs_planas(df)
    if sub.empty:
        return pd.DataFrame(columns=["referencia", "Cantidad", "T.corrida", "t.perdido", "#_puestos", "%_prod"])
    c_art = _find_col(sub, COLS["articulo"]); c_qty = _find_col(sub, COLS["cantidad"])
    c_tc  = _find_col(sub, COLS["tc"]);       c_tp  = _find_col(sub, COLS["tp"])
    c_cs  = _find_col(sub, COLS["cs"])
    if not (c_art and c_qty and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=["referencia", "Cantidad", "T.corrida", "t.perdido", "#_puestos", "%_prod"])
    g = sub.groupby(c_art).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    g = g.rename(columns={c_art: "referencia", c_qty: "Cantidad", c_tc: "T.corrida",
                          c_tp: "t.perdido", c_cs: "Corrida_Standar_sum"})
    for col in ["Cantidad", "T.corrida", "t.perdido", "Corrida_Standar_sum"]:
        g[col] = pd.to_numeric(g[col], errors="coerce").fillna(0.0)
    g["#_puestos"] = (g["T.corrida"] + g["t.perdido"]) / 8.0
    denom = g["T.corrida"] + g["t.perdido"]
    g["%_prod"] = np.where(denom > 0, (g["Corrida_Standar_sum"] / denom) * 100.0, np.nan)
    g = g.drop(columns=["Corrida_Standar_sum"])
    return g.sort_values("Cantidad", ascending=False, kind="stable").reset_index(drop=True)

def _turno_prod_and_puestos_planas(df: pd.DataFrame) -> pd.DataFrame:
    sub = _subset_refs_planas(df)
    if sub.empty:
        return pd.DataFrame(columns=["Turno", "prod", "puestos"])
    c_turno = _find_col(sub, COLS["turno"]); c_tc = _find_col(sub, COLS["tc"])
    c_tp = _find_col(sub, COLS["tp"]);       c_cs = _find_col(sub, COLS["cs"])
    if not (c_turno and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=["Turno", "prod", "puestos"])
    g = sub.groupby(c_turno).agg({c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    g = g.rename(columns={c_turno: "Turno", c_tc: "tc", c_tp: "tp", c_cs: "cs"})
    g = g.sort_values("Turno")
    g["puestos"] = (_to_num(g["tc"]) + _to_num(g["tp"])) / 8.0
    denom = _to_num(g["tc"]) + _to_num(g["tp"])
    g["prod"] = np.where(denom > 0, (_to_num(g["cs"]) / denom) * 100.0, np.nan)
    return g[["Turno", "prod", "puestos"]]

def _table_operario_total_planas(df: pd.DataFrame) -> pd.DataFrame:
    sub = _subset_refs_planas(df)
    if sub.empty:
        return pd.DataFrame(columns=["Operario", "Cantidad", "T.corrida", "T.perdido", "Productividad", "Perdida"])
    c_op  = _find_col(sub, COLS["operario"]); c_qty = _find_col(sub, COLS["cantidad"])
    c_tc  = _find_col(sub, COLS["tc"]);       c_tp  = _find_col(sub, COLS["tp"])
    c_cs  = _find_col(sub, COLS["cs"])
    if not (c_op and c_qty and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=["Operario", "Cantidad", "T.corrida", "T.perdido", "Productividad", "Perdida"])
    sub = sub.copy(); sub[c_op] = sub[c_op].astype(str).str.strip()
    g = sub.groupby(c_op).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    g = g.rename(columns={c_op: "Operario", c_qty: "Cantidad", c_tc: "T.corrida",
                          c_tp: "T.perdido", c_cs: "Corrida_Standar_sum"})
    for col in ["Cantidad", "T.corrida", "T.perdido", "Corrida_Standar_sum"]:
        g[col] = _to_num_locale(g[col])
    denom = g["T.corrida"] + g["T.perdido"]
    g["Productividad"] = np.where(denom > 0, (g["Corrida_Standar_sum"] / denom) * 100.0, np.nan)
    g["Perdida"] = g["Cantidad"] - (denom * 225.0)
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
    fname = f"gauge_filete_planas_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
    out_path = os.path.join(REPORTS_DIR, fname)
    plt.savefig(out_path, bbox_inches="tight"); plt.close(fig)
    return out_path, aspect

def _plot_downtime_bars_horizontal_planas(df_cause: pd.DataFrame) -> tuple[str | None, float]:
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
    ax.invert_yaxis(); ax.set_xlabel("Horas"); ax.set_title("Causas de tiempo perdido (Filete Planas)")
    ax.grid(axis="x", linestyle="--", alpha=0.4)
    fname = f"barras_causas_filete_planas_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
    out_path = os.path.join(REPORTS_DIR, fname)
    plt.tight_layout(); plt.savefig(out_path, bbox_inches="tight"); plt.close(fig)
    return out_path, aspect

def _plot_turno_bars_and_puestos_planas(df_turno: pd.DataFrame) -> tuple[str | None, float]:
    if df_turno is None or df_turno.empty:
        return None, 0.0
    df = df_turno.copy()
    try:
        df = df.sort_values("Turno")
    except Exception:
        pass
    xlabels = df["Turno"].astype(str).tolist()
    prod = df["prod"].astype(float).tolist()
    puestos = df["puestos"].astype(float).tolist()
    fig_w, fig_h = 6.0, 3.4
    aspect = fig_h / fig_w
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=150)
    x = np.arange(max(1, len(xlabels)))
    ax.bar(x, prod); ax.set_xticks(x); ax.set_xticklabels(xlabels, rotation=0)
    ax.set_ylabel("Productividad (%)"); ax.set_title("Productividad por turno y # puestos (PLN)")
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
    fname = f"turnos_prod_puestos_planas_{datetime.now().strftime('%Y%m%d%H%M%S%f')}.png"
    out_path = os.path.join(REPORTS_DIR, fname)
    plt.tight_layout(); plt.savefig(out_path, bbox_inches="tight"); plt.close(fig)
    return out_path, aspect

# ────────────────────────────────────────────────────────────────────────────────
# Render tablas
# ────────────────────────────────────────────────────────────────────────────────
def _render_units_row(pdf: FPDF, units: dict):
    labels = ["Filete_Gasa", "Filete_Leno", "Planas", "Cortadoras", "Auto_7"]
    nice = {"Filete_Gasa": "Filete_Gasa", "Filete_Leno": "Filete_Leno", "Planas": "Planas", "Cortadoras": "Cortadoras", "Auto_7": "Auto 7"}
    page_w = pdf.w - 2 * pdf.l_margin
    col_w = page_w / len(labels)
    pdf.set_font("Helvetica", "B", 14); pdf.cell(0, 8, _sanitize_pdf_text("Unidades producidas por línea"), ln=1); pdf.ln(1)
    pdf.set_font("Helvetica", "B", 11)
    for k in labels:
        pdf.cell(col_w, 7, _sanitize_pdf_text(nice[k]), align="C", border=0)
    pdf.ln(7)
    pdf.set_font("Helvetica", "", 12)
    for k in labels:
        pdf.cell(col_w, 8, _sanitize_pdf_text(_fmt_int(units.get(k, 0))), align="C", border=0)
    pdf.ln(10)

def _render_table_refs_planas(pdf: FPDF, df: pd.DataFrame, x: float, y: float, w: float, max_rows: int = 16) -> float:
    pdf.set_xy(x, y); pdf.set_font("Helvetica", "B", 13)
    pdf.cell(w, 7, _sanitize_pdf_text("Detalle por referencia (PLN)"), ln=1)
    if df is None or df.empty:
        pdf.set_font("Helvetica", "", 10); pdf.set_xy(x, pdf.get_y())
        pdf.multi_cell(w, 6, _sanitize_pdf_text("Sin datos para el periodo."), border=1)
        return 6 + 8
    df2 = df.copy(); extra_rows = 0
    if len(df2) > max_rows:
        extra = df2.iloc[max_rows:].copy()
        agg_tc = float(pd.to_numeric(extra["T.corrida"], errors="coerce").fillna(0).sum())
        agg_tp = float(pd.to_numeric(extra["t.perdido"], errors="coerce").fillna(0).sum())
        agg_qty = float(pd.to_numeric(extra["Cantidad"], errors="coerce").fillna(0).sum())
        weights = (pd.to_numeric(extra["T.corrida"], errors="coerce").fillna(0) +
                   pd.to_numeric(extra["t.perdido"], errors="coerce").fillna(0))
        weighted_prod = (pd.to_numeric(extra["%_prod"], errors="coerce").fillna(0) * weights).sum()
        denom_w = weights.sum(); agg_prod = float(weighted_prod / denom_w) if denom_w > 0 else 0.0
        agg_row = {"referencia": "Otros", "Cantidad": agg_qty, "T.corrida": agg_tc, "t.perdido": agg_tp,
                   "#_puestos": (agg_tc + agg_tp) / 8.0, "%_prod": agg_prod}
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
        for text, width in zip(row, widths):
            pdf.cell(width, 6, _sanitize_pdf_text(text), border=1, align="C")
        pdf.ln(6)
    used_h = pdf.get_y() - start_y + 6 + 7
    if extra_rows > 0:
        pdf.set_x(x); pdf.set_font("Helvetica", "I", 8)
        pdf.cell(w, 5, _sanitize_pdf_text(f"(*) Se agruparon {extra_rows} referencias en 'Otros'."), ln=1); used_h += 5
    return used_h

def _render_table_operario_total_planas(pdf: FPDF, df: pd.DataFrame, x: float, y: float, w: float) -> float:
    def _draw_header():
        pdf.set_font("Helvetica", "B", 9)
        for h, width in zip(["Operario", "Cantidad", "T.corrida", "T.perdido", "% prod", "Pérdida"], widths):
            pdf.cell(width, 6, _sanitize_pdf_text(h), border=1, align="C")
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 9)

    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(w, 7, _sanitize_pdf_text("Detalle por operario (PLN)"), ln=1)

    if df is None or df.empty:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_xy(x, pdf.get_y())
        pdf.multi_cell(w, 6, _sanitize_pdf_text("Sin datos para el periodo."), border=1)
        return 6 + 8

    rel = [0.38, 0.12, 0.14, 0.14, 0.10, 0.12]
    widths = [w * r for r in rel]
    row_h = 6
    bottom_y = pdf.h - pdf.b_margin

    _draw_header()
    start_y = pdf.get_y()

    for _, r in df.iterrows():
        if pdf.get_y() + row_h > bottom_y:
            pdf.add_page()
            pdf.set_xy(x, pdf.get_y())
            pdf.set_font("Helvetica", "B", 13)
            pdf.cell(w, 7, _sanitize_pdf_text("Detalle por operario (PLN) (cont.)"), ln=1)
            _draw_header()

        operario = str(r.get("Operario", ""))
        pdf.set_x(x)
        pdf.cell(widths[0], row_h, _truncate_to_width(pdf, operario, widths[0]-2), border=1, align="L")
        pdf.cell(widths[1], row_h, _sanitize_pdf_text(_fmt_int(r.get("Cantidad", 0))), border=1, align="C")
        pdf.cell(widths[2], row_h, _sanitize_pdf_text(_fmt_float(r.get("T.corrida", 0), 2)), border=1, align="C")
        pdf.cell(widths[3], row_h, _sanitize_pdf_text(_fmt_float(r.get("T.perdido", 0), 2)), border=1, align="C")
        pdf.cell(widths[4], row_h, _sanitize_pdf_text(_fmt_pct(r.get("Productividad", 0))), border=1, align="C")
        pdf.cell(widths[5], row_h, _sanitize_pdf_text(_fmt_int(r.get("Perdida", 0))), border=1, align="C")
        pdf.ln(row_h)

    used_h = pdf.get_y() - start_y + 6 + 7
    return used_h

def _render_signature(pdf: FPDF):
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 8, _sanitize_pdf_text("Informe Generado por Agente IA CiplasBot"),
             ln=1, align="C")

# ────────────────────────────────────────────────────────────────────────────────
# Nuevo formato PLANAS (similar a GASA/LENO)
# ────────────────────────────────────────────────────────────────────────────────
class ReportePlanas(FPDF):
    def __init__(self, start: date, end: date, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._start_date = start
        self._end_date = end

    def header(self):
        self.set_font("Arial", "B", 16)
        self.cell(0, 10, "Analisis proceso Planas", 0, 1, "C")
        self.set_font("Arial", "", 10)
        rango_txt = (
            f"Rango del informe {self._start_date.strftime('%d/%m/%Y')} "
            f"a {self._end_date.strftime('%d/%m/%Y')}"
        )
        self.cell(0, 6, rango_txt, 0, 1, "C")
        fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.cell(0, 8, f"Fecha y hora de generacion: {fecha_hora}", 0, 1, "R")
        self.ln(3)

    def tabla_maquinas(self, df: pd.DataFrame) -> None:
        self.set_font("Arial", "B", 12)
        self.set_fill_color(230, 230, 230)
        self.cell(0, 10, " 1. Resumen de Produccion por Maquina", 0, 1, "L", True)
        self.ln(2)

        self.set_font("Arial", "B", 8)
        self.set_fill_color(200, 220, 255)
        cols = ["maquina", "produccion", "t_corrida", "T.perd", "cor.estandar", "%productividad", "%eficiencia"]
        widths = [25, 25, 25, 20, 25, 30, 30]

        for i, col in enumerate(cols):
            self.cell(widths[i], 8, col, 1, 0, "C", True)
        self.ln()

        self.set_font("Arial", "", 8)
        for _, row in df.iterrows():
            self.cell(widths[0], 7, str(row["Maquina"]), 1)
            self.cell(widths[1], 7, f"{row['produccion']:,.0f}", 1, 0, "R")
            self.cell(widths[2], 7, f"{row['t_corrida']:.2f}", 1, 0, "R")
            self.cell(widths[3], 7, f"{row['T.perd']:.2f}", 1, 0, "R")
            self.cell(widths[4], 7, f"{row['cor.estandar']:.2f}", 1, 0, "R")
            self.cell(widths[5], 7, f"{row['%productividad']:.2f}%", 1, 0, "R")

            if row["%eficiencia"] >= 80:
                self.set_font("Arial", "B", 8)
            self.cell(widths[6], 7, f"{row['%eficiencia']:.2f}%", 1, 0, "R")
            self.set_font("Arial", "", 8)
            self.ln()
        self.ln(8)

    def tabla_operarios(self, df: pd.DataFrame) -> None:
        self.set_font("Arial", "B", 12)
        self.set_fill_color(235, 241, 222)
        self.cell(0, 10, " 2. Seguimiento Eficiencia operarios", 0, 1, "L", True)
        self.ln(2)

        self.set_font("Arial", "B", 8)
        self.set_fill_color(210, 230, 200)
        cols = ["Nombre", "Produccion", "T. corrida", "T. perdido", "Cor. est.", "% Efic periodo", "Tendencia (4 meses)"]
        widths = [40, 16, 16, 16, 16, 18, 68]

        for i, col in enumerate(cols):
            self.cell(widths[i], 8, col, 1, 0, "C", True)
        self.ln()

        self.set_font("Arial", "", 7)
        for _, row in df.iterrows():
            base_y = self.get_y()
            if row["%eficiencia_mes"] >= 80:
                self.set_fill_color(198, 239, 206)
            elif row["%eficiencia_mes"] >= 75:
                self.set_fill_color(255, 235, 156)
            else:
                self.set_fill_color(255, 255, 255)

            trend_text = row.get("tendencia_label", "")
            if "Mejora" in trend_text:
                self.set_text_color(0, 100, 0)
            elif "Decreciente" in trend_text:
                self.set_text_color(150, 0, 0)
            else:
                self.set_text_color(0, 0, 0)

            line_h = 4
            lines = max(1, int(self.get_string_width(trend_text) / max(1, widths[6] - 2)) + 1)
            row_h = max(6, line_h * lines)

            if base_y + row_h > self.h - self.b_margin:
                self.add_page()
                self.set_font("Arial", "B", 8)
                self.set_fill_color(210, 230, 200)
                for i, col in enumerate(cols):
                    self.cell(widths[i], 8, col, 1, 0, "C", True)
                self.ln()
                self.set_font("Arial", "", 7)
                base_y = self.get_y()

            self.set_xy(self.l_margin, base_y)
            self.cell(widths[0], row_h, str(row["Nombre"])[:45], 1, 0, "L", True)
            self.cell(widths[1], row_h, f"{row['Produccion']:,.0f}", 1, 0, "R", True)
            self.cell(widths[2], row_h, f"{row['Tiempo_corrida']:.2f}", 1, 0, "R", True)
            self.cell(widths[3], row_h, f"{row['T.perdido']:.2f}", 1, 0, "R", True)
            self.cell(widths[4], row_h, f"{row['Cor.estandar']:.2f}", 1, 0, "R", True)
            self.cell(widths[5], row_h, f"{row['%eficiencia_mes']:.2f}%", 1, 0, "R", True)
            self.multi_cell(widths[6], line_h, trend_text, 1, "L", True)
            self.set_text_color(0, 0, 0)
            self.set_y(base_y + row_h)


def _filter_planas_records(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    c_maq = _find_col(df, COLS["maquina"])
    if not c_maq:
        return pd.DataFrame()
    return df.loc[_starts_with_ci(df[c_maq], "FIPLA")].copy()


def _prepare_maquina_table_planas(df_prod: pd.DataFrame) -> pd.DataFrame:
    if df_prod is None or df_prod.empty:
        return pd.DataFrame(columns=[
            "Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar", "%productividad", "%eficiencia"
        ])
    c_maq = _find_col(df_prod, COLS["maquina"])
    c_qty = _find_col(df_prod, COLS["cantidad"])
    c_tc = _find_col(df_prod, COLS["tc"])
    c_tp = _find_col(df_prod, COLS["tp"])
    c_cs = _find_col(df_prod, COLS["cs"])
    if not (c_maq and c_qty and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=[
            "Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar", "%productividad", "%eficiencia"
        ])

    df_maq = df_prod.groupby(c_maq).agg({
        c_qty: "sum",
        c_tc: "sum",
        c_tp: "sum",
        c_cs: "sum",
    }).reset_index()
    df_maq.columns = ["Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar"]

    for col in ["produccion", "t_corrida", "T.perd", "cor.estandar"]:
        df_maq[col] = pd.to_numeric(df_maq[col], errors="coerce").fillna(0.0)

    denom_prod = df_maq["t_corrida"] + df_maq["T.perd"]
    df_maq["%productividad"] = np.where(denom_prod > 0, (df_maq["cor.estandar"] / denom_prod) * 100, 0.0)
    df_maq["%eficiencia"] = np.where(df_maq["t_corrida"] > 0, (df_maq["cor.estandar"] / df_maq["t_corrida"]) * 100, 0.0)
    df_maq.replace([np.inf, -np.inf], 0, inplace=True)
    return df_maq


def _prepare_operario_table_planas(df_prod: pd.DataFrame, end_date: date) -> pd.DataFrame:
    if df_prod is None or df_prod.empty:
        return pd.DataFrame(columns=[
            "Nombre", "Produccion", "Tiempo_corrida", "T.perdido", "Cor.estandar", "%eficiencia_mes"
        ])
    c_op = _find_col(df_prod, COLS["operario"])
    c_qty = _find_col(df_prod, COLS["cantidad"])
    c_tc = _find_col(df_prod, COLS["tc"])
    c_tp = _find_col(df_prod, COLS["tp"])
    c_cs = _find_col(df_prod, COLS["cs"])
    if not (c_op and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=[
            "Nombre", "Produccion", "Tiempo_corrida", "T.perdido", "Cor.estandar", "%eficiencia_mes"
        ])

    agg_map = {c_tc: "sum", c_tp: "sum", c_cs: "sum"}
    if c_qty:
        agg_map[c_qty] = "sum"
    df_ope = df_prod.groupby(c_op).agg(agg_map).reset_index()
    columns = ["Nombre", "Tiempo_corrida", "T.perdido", "Cor.estandar"]
    if c_qty:
        columns.append("Produccion")
    df_ope.columns = columns
    if "Produccion" not in df_ope.columns:
        df_ope["Produccion"] = 0.0
    for col in ["Produccion", "Tiempo_corrida", "T.perdido", "Cor.estandar"]:
        df_ope[col] = pd.to_numeric(df_ope[col], errors="coerce").fillna(0.0)
    df_ope["%eficiencia_mes"] = np.where(
        df_ope["Tiempo_corrida"] > 0,
        (df_ope["Cor.estandar"] / df_ope["Tiempo_corrida"]) * 100,
        0.0,
    )
    df_ope.replace([np.inf, -np.inf], 0, inplace=True)
    df_ope = df_ope.sort_values(by="%eficiencia_mes", ascending=False)
    labeler = _trend_labeler(end_date, months=4)
    df_ope["tendencia_label"] = df_ope["Nombre"].apply(labeler)
    return df_ope


def _render_productivity_note(pdf: FPDF, eficiencia_total: float, prod_total: float) -> None:
    diff_causas = eficiencia_total - prod_total
    restante = 80.0 - eficiencia_total
    pdf.ln(4)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 10)
    pdf.set_text_color(0, 100, 0)
    pdf.cell(0, 6, "Objetivo de productividad 80%.", 0, 1, "L")
    pdf.set_text_color(150, 0, 0)
    pdf.set_font("Arial", "B", 9)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0,
        5,
        f"Las causas de tiempo perdido corresponden a {diff_causas:.1f} puntos de productividad.",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "B", 9)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0,
        5,
        f"El restante {restante:.1f} es por bajo eficiencia del proceso.",
    )
    pdf.set_text_color(0, 0, 0)


def _render_global_data(pdf: FPDF, df_maq: pd.DataFrame) -> None:
    pdf.ln(4)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 12)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(0, 8, " 3. Datos globales (segun rango de fecha)", 0, 1, "L", True)
    pdf.ln(2)

    if df_maq is None or df_maq.empty:
        pdf.set_font("Arial", "", 9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, "Sin datos globales disponibles.")
        return

    totals = {
        "produccion": df_maq["produccion"].sum(),
        "t_corrida": df_maq["t_corrida"].sum(),
        "T.perd": df_maq["T.perd"].sum(),
        "cor.estandar": df_maq["cor.estandar"].sum(),
    }
    denom_prod = totals["t_corrida"] + totals["T.perd"]
    prod_total = (totals["cor.estandar"] / denom_prod) * 100 if denom_prod > 0 else 0.0
    eficiencia_total = (totals["cor.estandar"] / totals["t_corrida"]) * 100 if totals["t_corrida"] > 0 else 0.0

    pdf.set_font("Arial", "", 9)
    rows = [
        ("Produccion", f"{totals['produccion']:,.0f}"),
        ("T.corrida", f"{totals['t_corrida']:.2f}"),
        ("Tiempo Perdido", f"{totals['T.perd']:.2f}"),
        ("Corrida Estandar", f"{totals['cor.estandar']:.2f}"),
        ("%productivida_total", f"{prod_total:.2f}%"),
        ("%eficiencia_total", f"{eficiencia_total:.2f}%"),
    ]
    for label, value in rows:
        pdf.set_x(pdf.l_margin)
        pdf.cell(55, 6, label, 1, 0, "L")
        pdf.cell(0, 6, str(value), 1, 1, "L")


def build_pdf_planas(df_range_filtered: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    df_filt = _filter_planas_records(df_range_filtered)
    df_maq = _prepare_maquina_table_planas(df_filt)
    df_ope = _prepare_operario_table_planas(df_filt, end)

    pdf = ReportePlanas(start, end, format="Letter")
    pdf.add_page()
    if df_maq.empty:
        pdf.set_font("Arial", "B", 11)
        pdf.cell(0, 8, "Sin datos para Planas en el rango seleccionado.", 0, 1, "L")
    else:
        totals = {
            "produccion": df_maq["produccion"].sum(),
            "t_corrida": df_maq["t_corrida"].sum(),
            "T.perd": df_maq["T.perd"].sum(),
            "cor.estandar": df_maq["cor.estandar"].sum(),
        }
        denom_prod = totals["t_corrida"] + totals["T.perd"]
        prod_total = (totals["cor.estandar"] / denom_prod) * 100 if denom_prod > 0 else 0.0
        eficiencia_total = (totals["cor.estandar"] / totals["t_corrida"]) * 100 if totals["t_corrida"] > 0 else 0.0

        _render_productivity_note(pdf, eficiencia_total, prod_total)
        pdf.ln(4)
        pdf.tabla_maquinas(df_maq)
        if not df_ope.empty:
            pdf.tabla_operarios(df_ope)
        _render_global_data(pdf, df_maq)

    _render_signature(pdf)
    fname = f"Analisis_Proceso_PLANAS_{start.isoformat()}_{end.isoformat()}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)
    pdf.output(out_path)
    return out_path, []
