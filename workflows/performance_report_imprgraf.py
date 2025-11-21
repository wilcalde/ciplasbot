# workflows/performance_report_imprgraf.py
# Informe de desempeño para Impresión Gráfica (IMPRGRAF) - SIN desperdicio

import os
import re
import json
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import requests
import pytz

try:
    from fpdf import FPDF  # pip install fpdf2
except ImportError as e:
    raise RuntimeError("Falta 'fpdf2'. Instala: pip install fpdf2") from e

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError as e:
    raise RuntimeError("Falta 'matplotlib'. Instala: pip install matplotlib") from e

try:
    from openai import OpenAI
    _oa_client = OpenAI()
except Exception:
    _oa_client = None

from services.session_memory import CONFIG_DIR
# Asegúrate de que estos imports apunten a tus servicios reales
from services.whatsapp_service import send_whatsapp_message
from services.whatsapp_media import send_whatsapp_document
from services.wa_window_manager import canon_phone_e164_co

# =========================
# Config / Rutas
# =========================
OPERATORS_FILE = os.path.join(CONFIG_DIR, "operators_impr_graf.json")
REPORTS_DIR = os.path.join(CONFIG_DIR, "performance_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

PERFORMANCE_REQUESTS_DB = os.path.join(CONFIG_DIR, "performance_requests_log_imprgraf.jsonl")
TZ = "America/Bogota"

# =========================
# Origen de datos (IMPRGRAF)
# =========================
SHEET_XLSX_URL = "https://docs.google.com/spreadsheets/d/18Lbr6UyAnGVl9g7Nx-8FEjXmDEJ35ksv-n0D4ptfZ40//export?format=xlsx"

# Columnas base (alias admitidos)
BASE_COLS = {
    "name": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre Operario", "Nombre_Operario"],
    "fecha": ["Fecha_Efectiva", "fecha_efectiva", "fecha", "Fecha", "Fecha Registro", "Fecha_Registro"],
    "unidades": ["Cantidad_Completada", "cantidad_completada", "unidades", "cantidad"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_cda", "tpo_corrida"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrstand", "corrida_estandar"],
    "maq": ["Maquina", "Máquina", "maquina", "máquina", "machine", "equipo"],
    "cause": ["Causa_Paro", "causa_paro", "causa", "motivo_paro"],
    "code": ["Codigo_Paro", "Código_Paro", "codigo_paro", "cod_paro", "Cod_Paro",
             "Codigo paro", "Cod Paro", "CodigoParo", "codigoparo"],
    "desc_art": ["Descripcion_Articulo", "descripcion_articulo", "Descripción", "Descripcion", "Articulo", "Artículo", "Descripcion Articulo"]
}

# --- Códigos a agrupar como "Cambio de referencia"
CHANGEOVER_CODES = {
    "0102", "0131", "0132", "0133", "0134", "0135", "0137", "0140",
    "0141", "0142", "0143", "0146", "0149", "0159", "0402", "0144", "0153"
}
CREF_LABEL = "Cambio de referencia"

# =========================
# Utilidades
# =========================
def _only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s or "").strip("_")

def _first_name_from_fullname(fullname: str) -> str:
    s = (fullname or "").strip()
    if not s:
        return "operario"
    if "," in s:
        right = s.split(",", 1)[1].strip()
        return right.split()[0] if right else s.split()[0]
    return s.split()[0]

def _load_operators_index() -> dict:
    if not os.path.exists(OPERATORS_FILE):
        return {"operators": []}
    with open(OPERATORS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def _find_operator_by_phone(phone_raw: str):
    data = _load_operators_index()
    inbound_digits = _only_digits(canon_phone_e164_co(phone_raw) or phone_raw)
    for op in data.get("operators", []):
        op_digits = _only_digits(op.get("phone_e164", "") or op.get("phone", ""))
        if op_digits == inbound_digits:
            return op
    return None

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")

def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    norm_map = {_normalize(col): col for col in df.columns}
    for c in candidates:
        key = _normalize(c)
        if key in norm_map:
            return norm_map[key]
    return None

def _safe_to_datetime(s: pd.Series) -> pd.Series:
    s2 = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if s2.isna().any():
        try:
            snum = pd.to_numeric(s, errors="coerce")
            s_excel = pd.to_datetime(snum, unit="d", origin="1899-12-30", errors="coerce")
            s2 = s2.fillna(s_excel)
        except Exception:
            pass
    return s2

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

def _sanitize_pdf_text(s: str) -> str:
    if not s:
        return ""
    repl = {"\u2013":"-","\u2014":"-","\u2015":"-","\u2018":"'","\u2019":"'","\u201C":'"',"\u201D":'"',"\u2022":"-","\u00A0":" "}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

def _safe_unlink(path: str):
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"⚠️ No se pudo eliminar {path}: {e}")

def _now_bogota_iso() -> str:
    try:
        return datetime.now(pytz.timezone(TZ)).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")

def _append_jsonl(path: str, record: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"⚠️ No se pudo escribir en {path}: {e}")

def _log_request(phone_raw: str, operator_name: str, event: str, extra: dict | None = None):
    rec = {
        "ts": _now_bogota_iso(),
        "phone": _only_digits(canon_phone_e164_co(phone_raw) or phone_raw),
        "operator": (operator_name or "").strip(),
        "event": event  # 'requested' | 'sent'
    }
    if extra:
        rec.update(extra)
    _append_jsonl(PERFORMANCE_REQUESTS_DB, rec)

# ---------- Normalizador robusto de códigos ----------
def _norm_paro_code(val) -> str:
    """
    Devuelve el código de paro en 4 dígitos.
    Acepta: 402, '402', '0402', 402.0, '131.0', ' 0131 ', etc.
    """
    try:
        import math
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return ""
    except Exception:
        pass

    s = str(val).strip()
    try:
        f = float(s)
        if f.is_integer():
            return f"{int(f):04d}"
    except Exception:
        pass

    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    if "." in s and digits.endswith("0"):
        digits = digits.rstrip("0")
    return digits.zfill(4)[-4:]

# =========================
# Descarga de datos
# =========================
def _download_data() -> pd.DataFrame | None:
    try:
        url = SHEET_XLSX_URL.replace("//export", "/export")
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        xls = BytesIO(resp.content)
        df = pd.read_excel(xls)  # requiere openpyxl
        return df
    except Exception as e:
        print(f"❌ Error descargando base IMPRGRAF: {e}")
        return None

# =========================
# Filtros y cálculos
# =========================
def _filter_by_operator(df: pd.DataFrame, target_name: str) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    name_col = _find_col(df, BASE_COLS["name"])
    if not name_col:
        return df.iloc[0:0].copy()
    sub = df.copy()
    sub[name_col] = sub[name_col].astype(str)
    mask = sub[name_col].str.strip().str.casefold() == str(target_name).strip().casefold()
    return sub[mask].copy()

def _infer_range_from_df(df: pd.DataFrame) -> tuple[date | None, date | None]:
    if df is None or df.empty:
        return None, None
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    if not fecha_col:
        return None, None
    s = _safe_to_datetime(df[fecha_col]).dropna()
    if s.empty:
        return None, None
    return s.min().date(), s.max().date()

def _compute_metrics_for_operator(df: pd.DataFrame) -> dict:
    metrics = {"fecha_min": "N/D", "fecha_max": "N/D", "unidades": "0", "horas_trabajadas": "0"}
    if df is None or df.empty:
        return metrics
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    unidades_col = _find_col(df, BASE_COLS["unidades"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    if fecha_col:
        fechas = _safe_to_datetime(df[fecha_col])
        fmin = pd.to_datetime(fechas.min()) if not fechas.isna().all() else None
        fmax = pd.to_datetime(fechas.max()) if not fechas.isna().all() else None
        if pd.notna(fmin): metrics["fecha_min"] = fmin.strftime("%Y-%m-%d")
        if pd.notna(fmax): metrics["fecha_max"] = fmax.strftime("%Y-%m-%d")
    if unidades_col:
        metrics["unidades"] = _fmt_int(pd.to_numeric(df[unidades_col], errors="coerce").fillna(0).sum())
    total_h = 0.0
    if tc_col: total_h += pd.to_numeric(df[tc_col], errors="coerce").fillna(0).sum()
    if tp_col: total_h += pd.to_numeric(df[tp_col], errors="coerce").fillna(0).sum()
    metrics["horas_trabajadas"] = _fmt_float(total_h, 2)
    return metrics

def _get_month_year_from_df(df: pd.DataFrame) -> tuple[int | None, int | None]:
    if df is None or df.empty:
        return None, None
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    if not fecha_col:
        return None, None
    s_fecha = _safe_to_datetime(df[fecha_col])
    if s_fecha.dropna().empty:
        return None, None
    fmax = s_fecha.max()
    if pd.isna(fmax):
        return None, None
    return int(fmax.month), int(fmax.year)

def _aggregate_daily_for_month(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    info = {"month": None, "year": None, "can_compute_prod": False}
    if df is None or df.empty:
        return df, info

    fecha_col = _find_col(df, BASE_COLS["fecha"])
    unidades_col = _find_col(df, BASE_COLS["unidades"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    cs_col = _find_col(df, BASE_COLS["cs"])

    if not fecha_col or not unidades_col:
        return df.iloc[0:0].copy(), info

    s_fecha = _safe_to_datetime(df[fecha_col])
    if s_fecha.dropna().empty:
        return df.iloc[0:0].copy(), info

    fmax = s_fecha.max()
    if pd.isna(fmax):
        return df.iloc[0:0].copy(), info

    month, year = int(fmax.month), int(fmax.year)
    info["month"], info["year"] = month, year

    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    agg_dict = {unidades_col: "sum"}
    if tc_col: agg_dict[tc_col] = "sum"
    if tp_col: agg_dict[tp_col] = "sum"
    if cs_col: agg_dict[cs_col] = "sum"

    daily = dfm.groupby(dfm["_fecha"].dt.date).agg(agg_dict).reset_index(names="date")
    daily["day"] = pd.to_datetime(daily["date"]).dt.day
    daily = daily.sort_values("date")

    daily = daily.rename(columns={unidades_col: "unidades"})
    if tc_col: daily = daily.rename(columns={tc_col: "tc"})
    if tp_col: daily = daily.rename(columns={tp_col: "tp"})
    if cs_col: daily = daily.rename(columns={cs_col: "cs"})

    can_prod = (cs_col is not None) and (tc_col is not None) and (tp_col is not None)
    info["can_compute_prod"] = can_prod

    if can_prod:
        denom = pd.to_numeric(daily.get("tc", 0), errors="coerce").fillna(0) + pd.to_numeric(daily.get("tp", 0), errors="coerce").fillna(0)
        num = pd.to_numeric(daily.get("cs", 0), errors="coerce").fillna(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            daily["prod_pct"] = np.where(denom > 0, (num / denom) * 100.0, np.nan)
    else:
        daily["prod_pct"] = np.nan

    daily["unidades"] = pd.to_numeric(daily["unidades"], errors="coerce").fillna(0)
    cols = ["date", "day", "unidades", "prod_pct"]
    if "tc" in daily.columns:
        cols.append("tc")
    return daily[cols], info

def _aggregate_by_machine_for_month(df: pd.DataFrame, month: int | None, year: int | None) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    fecha_col = _find_col(df, BASE_COLS["fecha"])
    unidades_col = _find_col(df, BASE_COLS["unidades"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    cs_col = _find_col(df, BASE_COLS["cs"])
    maq_col = _find_col(df, BASE_COLS["maq"])

    if not fecha_col or not unidades_col or not maq_col:
        return df.iloc[0:0].copy()

    s_fecha = _safe_to_datetime(df[fecha_col])
    if s_fecha.dropna().empty:
        return df.iloc[0:0].copy()

    if month is None or year is None:
        m, y = _get_month_year_from_df(df)
        month = m if month is None else month
        year = y if year is None else year

    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    agg_dict = {unidades_col: "sum"}
    if tc_col: agg_dict[tc_col] = "sum"
    if tp_col: agg_dict[tp_col] = "sum"
    if cs_col: agg_dict[cs_col] = "sum"

    g = dfm.groupby(maq_col).agg(agg_dict).reset_index()
    g = g.rename(columns={maq_col: "maquina", unidades_col: "unidades"})
    if tc_col: g = g.rename(columns={tc_col: "tc"})
    if tp_col: g = g.rename(columns={tp_col: "tp"})
    if cs_col: g = g.rename(columns={cs_col: "cs"})

    if all(col in g.columns for col in ["tc", "tp", "cs"]):
        denom = pd.to_numeric(g["tc"], errors="coerce").fillna(0) + pd.to_numeric(g["tp"], errors="coerce").fillna(0)
        num = pd.to_numeric(g["cs"], errors="coerce").fillna(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            g["prod_pct"] = np.where(denom > 0, (num / denom) * 100.0, np.nan)
    else:
        g["prod_pct"] = np.nan

    g["unidades"] = pd.to_numeric(g["unidades"], errors="coerce").fillna(0)
    g = g.sort_values("unidades", ascending=False, kind="stable").reset_index(drop=True)
    return g[["maquina", "unidades", "prod_pct"]]

def _aggregate_downtime_by_cause_for_month(df: pd.DataFrame, month: int | None, year: int | None) -> pd.DataFrame:
    """Pareto 80% de causas de paro, unificando CÓDIGOS indicados en 'Cambio de referencia' (y también textos que contengan 'cambio'+'refer')."""
    if df is None or df.empty:
        return df

    fecha_col = _find_col(df, BASE_COLS["fecha"])
    tp_col    = _find_col(df, BASE_COLS["tp"])
    cause_col = _find_col(df, BASE_COLS["cause"])
    code_col  = _find_col(df, BASE_COLS.get("code", []))

    if not fecha_col or not tp_col or not cause_col:
        return df.iloc[0:0].copy()

    s_fecha = _safe_to_datetime(df[fecha_col])
    if s_fecha.dropna().empty:
        return df.iloc[0:0].copy()

    if month is None or year is None:
        m, y = _get_month_year_from_df(df)
        month = m if month is None else month
        year  = y if year  is None else year

    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)].copy()

    if code_col:
        code_norm = dfm[code_col].map(_norm_paro_code)
    else:
        code_norm = pd.Series([""] * len(dfm), index=dfm.index)

    cause_txt = dfm[cause_col].astype(str).str.strip()
    cause_low = cause_txt.str.casefold()

    by_code = code_norm.isin(CHANGEOVER_CODES)
    by_text = cause_low.str.contains(r"\bcambio\b", na=False) & cause_low.str.contains("refer", na=False)
    is_changeover = by_code | by_text

    dfm["__cause_mapped"] = np.where(is_changeover, CREF_LABEL, cause_txt)

    g = dfm.groupby("__cause_mapped", dropna=False)[tp_col].sum().reset_index()
    g = g.rename(columns={"__cause_mapped": "cause", tp_col: "horas"})
    g["horas"] = pd.to_numeric(g["horas"], errors="coerce").fillna(0.0)

    total = g["horas"].sum()
    if total <= 0:
        return g.iloc[0:0].copy()

    g = g.sort_values("horas", ascending=False, kind="stable").reset_index(drop=True)
    g["pct"] = (g["horas"] / total) * 100.0
    g["cum_pct"] = g["pct"].cumsum()

    cutoff_idx = (g["cum_pct"] <= 80.0).sum()
    if cutoff_idx == 0:
        cutoff_idx = 1
    if g["cum_pct"].iloc[cutoff_idx - 1] < 80.0 and cutoff_idx < len(g):
        cutoff_idx += 1

    g = g.iloc[:cutoff_idx].copy()
    return g[["cause", "horas", "pct"]]

def _aggregate_daily_speed_for_month(df: pd.DataFrame, month: int | None, year: int | None) -> pd.DataFrame:
    if df is None or df.empty:
        return df

    fecha_col = _find_col(df, BASE_COLS["fecha"])
    unidades_col = _find_col(df, BASE_COLS["unidades"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    if not fecha_col or not unidades_col or not tc_col:
        return df.iloc[0:0].copy()

    s_fecha = _safe_to_datetime(df[fecha_col])
    if s_fecha.dropna().empty:
        return df.iloc[0:0].copy()

    if month is None or year is None:
        m, y = _get_month_year_from_df(df)
        month = m if month is None else month
        year = y if year is None else year

    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    g = dfm.groupby(dfm["_fecha"].dt.date).agg({unidades_col: "sum", tc_col: "sum"}).reset_index(names="date")
    g["day"] = pd.to_datetime(g["date"]).dt.day
    g = g.sort_values("date")
    g = g.rename(columns={unidades_col: "unidades", tc_col: "tc"})
    unidades = pd.to_numeric(g["unidades"], errors="coerce").fillna(0.0)
    tc = pd.to_numeric(g["tc"], errors="coerce").fillna(0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        g["speed_mpm"] = np.where(tc > 0, unidades / (tc * 60.0), np.nan)
    return g[["day", "speed_mpm"]]

# === NUEVO: utils para concatenar únicos y contar referencias
def _unique_join(values, sep=" / "):
    seen = set()
    out = []
    for v in values:
        s = str(v).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return sep.join(out)

def _count_refs_in_cell(cell: str, sep=" | ") -> int:
    parts = [t.strip() for t in str(cell).split(sep)]
    return sum(1 for t in parts if t)

# === NUEVO: Tabla de cambio de referencia consolidada por fecha
def _build_changeover_detail_table(df: pd.DataFrame, month: int, year: int) -> tuple[pd.DataFrame, float, int]:
    """
    Devuelve (tabla, total_horas, n_referencias_tabla) filtrado por mes/año del operador.
    - Elimina 'metros_impresos'
    - Agrupa (totaliza) 'Referencia' por FECHA (concatenando únicas).
    - 'Maquina' también se consolida por fecha (únicas).
    - 'T.cambio_ref' se suma por fecha.
    - El conteo retornado corresponde al TOTAL DE REFERENCIAS (no filas) de la tabla.
    """
    cols = ["Fecha", "Maquina", "Referencia", "T.cambio_ref"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols), 0.0, 0

    fecha_col    = _find_col(df, BASE_COLS["fecha"])
    maq_col      = _find_col(df, BASE_COLS["maq"])
    ref_col      = _find_col(df, BASE_COLS["desc_art"])
    tp_col       = _find_col(df, BASE_COLS["tp"])
    code_col     = _find_col(df, BASE_COLS["code"])
    cause_col    = _find_col(df, BASE_COLS["cause"])

    if not all([fecha_col, maq_col, ref_col, tp_col]) or (not code_col and not cause_col):
        return pd.DataFrame(columns=cols), 0.0, 0

    s_fecha = _safe_to_datetime(df[fecha_col])
    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    if dfm.empty:
        return pd.DataFrame(columns=cols), 0.0, 0

    # Selección por código y/o texto
    by_code = pd.Series(False, index=dfm.index)
    if code_col:
        by_code = dfm[code_col].map(_norm_paro_code).isin(CHANGEOVER_CODES)

    by_text = pd.Series(False, index=dfm.index)
    if cause_col:
        cause_low = dfm[cause_col].astype(str).str.casefold()
        by_text = cause_low.str.contains(r"\bcambio\b", na=False) & cause_low.str.contains("refer", na=False)

    mask = by_code | by_text
    base = dfm[mask].copy()
    if base.empty:
        return pd.DataFrame(columns=cols), 0.0, 0

    # Campos limpios
    base["Fecha"] = pd.to_datetime(base["_fecha"]).dt.strftime("%Y-%m-%d")
    base["Maquina"] = base[maq_col].astype(str)
    base["Referencia"] = base[ref_col].astype(str).str.strip()
    base["T.cambio_ref"] = pd.to_numeric(base[tp_col], errors="coerce").fillna(0.0)

    # Agrupar por FECHA (consolidando máquinas y referencias; sumando horas)
    grp = (
        base.groupby("Fecha", as_index=False)
            .agg({
                "Maquina": lambda s: _unique_join(s, sep=" / "),
                "Referencia": lambda s: _unique_join(s, sep=" | "),
                "T.cambio_ref": "sum"
            })
            .sort_values("Fecha")
    )

    total_time = float(grp["T.cambio_ref"].sum())
    n_refs_total = int(sum(_count_refs_in_cell(x, sep=" | ") for x in grp["Referencia"]))

    grp = grp[["Fecha", "Maquina", "Referencia", "T.cambio_ref"]]
    return grp, total_time, n_refs_total

# =========================
# Ploteo
# =========================
def _plot_daily_bars_with_productivity(daily: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    if daily is None or daily.empty:
        return None
    days = daily["day"].to_list()
    unidades = daily["unidades"].to_list()
    prod = daily["prod_pct"].to_list()

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=130)
    cmap = plt.get_cmap("Blues")
    colors = [cmap(0.55 + 0.4 * (i / max(1, len(days)-1))) for i in range(len(days))]
    ax.bar(days, unidades, width=0.8, color=colors, edgecolor="none")

    ax.set_xlabel("Día")
    ax.set_ylabel("Unidades (Cantidad_Completada)")
    ax.set_title(f"{operator_name} - {year}-{month:02d}")

    ax2 = ax.twinx()
    if np.isfinite(np.array(prod)).any():
        ax2.plot(days, prod, marker="o", linewidth=2)
        ax2.set_ylabel("Productividad (%)")
        valid = [p for p in prod if pd.notna(p)]
        if valid:
            pmax = max(valid)
            ax2.set_ylim(0, max(100.0, pmax * 1.15))
    else:
        ax2.set_ylabel("Productividad (%)")
        ax2.set_ylim(0, 100)

    ax.grid(axis="y", linestyle="--", alpha=0.5)
    fig.tight_layout()

    img_name = f"imprgraf_grafico_diario_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

def _plot_machine_bars_with_productivity(g: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    if g is None or g.empty:
        return None
    labels = g["maquina"].astype(str).tolist()
    unidades = g["unidades"].tolist()
    prod = g["prod_pct"].tolist()

    n = len(labels)
    fig_w = max(8, min(18, 0.6 * n + 3))
    fig, ax = plt.subplots(figsize=(fig_w, 4.8), dpi=130)

    cmap = plt.get_cmap("Greens")
    colors = [cmap(0.55 + 0.4 * (i / max(1, n-1))) for i in range(n)]
    x = np.arange(n)
    ax.bar(x, unidades, width=0.75, color=colors, edgecolor="none")

    ax.set_xlabel("Máquina")
    ax.set_ylabel("Unidades (Cantidad_Completada)")
    ax.set_title(f"{operator_name} - Unidades y Productividad por Máquina ({year}-{month:02d})")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")

    ax2 = ax.twinx()
    if np.isfinite(np.array(prod)).any():
        ax2.plot(x, prod, marker="o", linewidth=2)
        ax2.set_ylabel("Productividad (%)")
        valid = [p for p in prod if pd.notna(p)]
        if valid:
            pmax = max(valid)
            ax2.set_ylim(0, max(100.0, pmax * 1.15))
    else:
        ax2.set_ylabel("Productividad (%)")
        ax2.set_ylim(0, 100)

    ax.grid(axis="y", linestyle="--", alpha=0.5)
    fig.tight_layout()

    img_name = f"imprgraf_grafico_maquinas_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

def _plot_pie_downtime_by_cause(gcause: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    if gcause is None or gcause.empty:
        return None

    labels = gcause["cause"].astype(str).tolist()
    horas = gcause["horas"].tolist()
    if sum(horas) <= 0:
        return None

    n = len(labels)
    fig_w = max(8, min(14, 0.45 * n + 7))
    fig, ax = plt.subplots(figsize=(fig_w, 5.2), dpi=130)

    wedges, _, _ = ax.pie(horas, labels=None, autopct=lambda p: f"{p:.1f}%", startangle=90, pctdistance=0.7)
    ax.set_title(f"{operator_name} - % Horas de paro por causa (Pareto, {year}-{month:02d})")
    ax.axis("equal")
    ax.legend(wedges, labels, title="Causas (Pareto 80%)", loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False)

    plt.subplots_adjust(right=0.78)
    fig.tight_layout()

    img_name = f"imprgraf_grafico_causas_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

def _plot_daily_speed_line(dfspeed: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    if dfspeed is None or dfspeed.empty:
        return None

    days = dfspeed["day"].to_list()
    speed = dfspeed["speed_mpm"].to_list()

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=130)
    ax.plot(days, speed, marker="o", linewidth=2)

    ax.set_xlabel("Día")
    ax.set_ylabel("Velocidad promedio (m/min)")
    ax.set_title(f"{operator_name} - Velocidad promedio diaria ({year}-{month:02d})")
    ax.grid(True, linestyle="--", alpha=0.5)

    fig.tight_layout()
    img_name = f"imprgraf_grafico_velocidad_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

# === NUEVO: barras de HORAS por FECHA usando la TABLA creada ===
def _plot_changeover_hours_by_date(tbl: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    """
    Recibe la tabla agregada (Fecha, Maquina, Referencia, T.cambio_ref) y grafica:
    Eje X = Fecha; Eje Y = T.cambio_ref (horas).
    """
    if tbl is None or tbl.empty:
        return None

    df = tbl.copy()
    df["Fecha_ord"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df = df.sort_values("Fecha_ord")
    fechas = df["Fecha"].tolist()
    horas = pd.to_numeric(df["T.cambio_ref"], errors="coerce").fillna(0.0).tolist()

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=130)
    cmap = plt.get_cmap("Purples")
    colors = [cmap(0.55 + 0.4 * (i / max(1, len(fechas) - 1))) for i in range(len(fechas))]
    x = np.arange(len(fechas))
    ax.bar(x, horas, width=0.8, color=colors, edgecolor="none")

    ax.set_xlabel("Fecha")
    ax.set_ylabel("Horas cambio de referencia")
    ax.set_title(f"{operator_name} - Horas por fecha ({year}-{month:02d})")
    ax.set_xticks(x)
    ax.set_xticklabels(fechas, rotation=45, ha="right")
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    fig.tight_layout()
    img_name = f"imprgraf_grafico_horas_cambios_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

# =========================
# KPIs globales (repuesto)
# =========================
def _compute_overall_kpis(df: pd.DataFrame, month: int | None, year: int | None) -> dict:
    """
    Devuelve:
      - productividad_total_pct = (sum(CORRSTAND) / sum(Tiempo_Corrida + Tiempo_Perdido)) * 100
      - velocidad_prom_mpm = sum(Cantidad_Completada) / (sum(Tiempo_Corrida)*60)
    """
    res = {"productividad_total_pct": None, "velocidad_prom_mpm": None}
    if df is None or df.empty:
        return res

    fecha_col    = _find_col(df, BASE_COLS["fecha"])
    unidades_col = _find_col(df, BASE_COLS["unidades"])
    tc_col       = _find_col(df, BASE_COLS["tc"])
    tp_col       = _find_col(df, BASE_COLS["tp"])
    cs_col       = _find_col(df, BASE_COLS["cs"])
    if not fecha_col:
        return res

    s_fecha = _safe_to_datetime(df[fecha_col])
    if s_fecha.dropna().empty:
        return res

    if month is None or year is None:
        m, y = _get_month_year_from_df(df)
        month = m if month is None else month
        year  = y if year  is None else year

    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    if cs_col and tc_col and tp_col:
        num = pd.to_numeric(dfm[cs_col], errors="coerce").fillna(0.0).sum()
        denom = (
            pd.to_numeric(dfm[tc_col], errors="coerce").fillna(0.0).sum()
            + pd.to_numeric(dfm[tp_col], errors="coerce").fillna(0.0).sum()
        )
        res["productividad_total_pct"] = float((num / denom) * 100.0) if denom > 0 else None

    if unidades_col and tc_col:
        u  = pd.to_numeric(dfm[unidades_col], errors="coerce").fillna(0.0).sum()
        tc = pd.to_numeric(dfm[tc_col], errors="coerce").fillna(0.0).sum()
        res["velocidad_prom_mpm"] = float(u / (tc * 60.0)) if tc > 0 else None

    return res

# =========================
# IA (sin desperdicio)
# =========================
def _generate_ai_analysis(operator_name: str, month: int, year: int, kpis: dict, pareto_df: pd.DataFrame, daily_df: pd.DataFrame | None) -> str:
    objetivo_prod = 60.0
    val_prod = kpis.get("productividad_total_pct")
    prod_txt = "N/D" if (val_prod is None or pd.isna(val_prod)) else f"{val_prod:.1f}%"
    j_prod = None if (val_prod is None or pd.isna(val_prod)) else ("✅ sobre meta" if val_prod >= objetivo_prod else "⚠️ bajo meta")

    var_info = {"cv_pct": None, "min_u": None, "max_u": None, "first_avg": None, "last_avg": None, "trend": None}
    if daily_df is not None and not daily_df.empty and "unidades" in daily_df.columns:
        u = pd.to_numeric(daily_df["unidades"], errors="coerce").fillna(0.0)
        if len(u) > 0 and u.sum() > 0:
            mean_u = float(u.mean())
            std_u = float(u.std(ddof=0))
            cv_pct = float((std_u / mean_u) * 100.0) if mean_u > 0 else None
            var_info["cv_pct"] = cv_pct
            var_info["min_u"] = int(np.nanmin(u))
            var_info["max_u"] = int(np.nanmax(u))
            n = len(u)
            k = max(1, int(round(n * 0.25)))
            first_avg = float(u.iloc[:k].mean())
            last_avg = float(u.iloc[-k:].mean())
            var_info["first_avg"] = first_avg
            var_info["last_avg"] = last_avg
            if last_avg > first_avg * 1.05:
                var_info["trend"] = "📈 al alza"
            elif last_avg < first_avg * 0.95:
                var_info["trend"] = "📉 a la baja"
            else:
                var_info["trend"] = "➖ estable"

    top_causas = []
    if pareto_df is not None and not pareto_df.empty:
        for _, r in pareto_df.head(3).iterrows():
            causa = str(r.get('cause', ''))[:35]
            pct   = float(r.get('pct', 0.0))
            top_causas.append(f"{causa} ({pct:.1f}%)")
    causas_line = ", ".join(top_causas) if top_causas else "sin datos destacados"

    system_prompt = (
        "Eres CiplasBot, coach de planta. Escribe para OPERARIOS: lenguaje sencillo, frases cortas, "
        "sin tecnicismos. Usa 4–6 emojis (💪✅⚠️📈🧰⏱️). "
        "Devuelve viñetas: resumen, fortalezas, por mejorar (acciones), variabilidad/tendencia y cierre con preguntas."
    )

    cv_val = var_info.get("cv_pct")
    cv_str = "N/D" if (cv_val is None or pd.isna(cv_val)) else f"{cv_val:.1f}"

    user_prompt = (
        f"Operario: {operator_name}\n"
        f"Periodo: {year}-{month:02d}\n\n"
        f"KPI productividad: {prod_txt} ({j_prod or 'N/D'}) | Meta 60%\n"
        f"Top causas de paro (Pareto): {causas_line}\n"
        f"Variabilidad: CV={cv_str}%, min={var_info.get('min_u')}, max={var_info.get('max_u')}, tendencia={var_info.get('trend') or 'N/D'}\n\n"
        "Escribe conclusiones claras para el operario con viñetas."
    )

    try:
        if _oa_client is None:
            raise RuntimeError("OpenAI no configurado")
        chat = _oa_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.35,
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": user_prompt}],
        )
        text = (chat.choices[0].message.content or "").strip()
    except Exception:
        resumen = f"Resumen {year}-{month:02d}: Productividad {prod_txt}. "
        if j_prod:
            resumen += f"Estado → Prod: {j_prod}. "
        var_txt = ""
        if cv_val is not None and not pd.isna(cv_val):
            var_txt = f"Variabilidad (CV): {cv_val:.1f}%. Rango {var_info['min_u']}–{var_info['max_u']} u., {var_info['trend']}."
        texto = [
            f"{resumen}💪",
            "Fortalezas: • Arranques a tiempo • Orden del puesto • Calidad estable",
            "Para mejorar: • Alistamientos más ágiles • Atacar 1–2 causas del Pareto • Checklist de herramientas",
            f"Variabilidad: {var_txt}",
            "¿Qué mejorar ya? ¿Qué necesitas del equipo? ¿Qué hábito refuerzas esta semana?"
        ]
        text = "\n".join(texto)

    return _sanitize_pdf_text(text)

# =========================
# PDF
# =========================
def _render_changeover_table_pdf(pdf: FPDF, df: pd.DataFrame, total_h: float, n_refs: int):
    """Imprime tabla debajo del gráfico de torta (sin 'metros_impresos' y con 'Referencia' totalizada por FECHA)."""
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 7, _sanitize_pdf_text("Detalle de cambios de referencia (referencias totalizadas por día)"), ln=1)

    if df is None or df.empty:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, _sanitize_pdf_text("No se registran cambios de referencia en el período."), ln=1)
        pdf.set_text_color(0, 0, 0)
        return

    page_w = pdf.w - 2 * pdf.l_margin
    # Rebalanceo de anchos: suma = 1.00
    w_fecha = page_w * 0.20
    w_maq   = page_w * 0.20
    w_ref   = page_w * 0.45
    w_tcr   = page_w * 0.15

    # Encabezados
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(w_fecha, 7, "Fecha", 1, 0, "C", True)
    pdf.cell(w_maq,   7, "Maquina(s)", 1, 0, "C", True)
    pdf.cell(w_ref,   7, "Referencia(s) totalizadas", 1, 0, "C", True)
    pdf.cell(w_tcr,   7, "T.cambio_ref (h)", 1, 1, "C", True)

    pdf.set_font("Helvetica", "", 9)
    for _, r in df.iterrows():
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 10)
            pdf.set_fill_color(230, 230, 230)
            pdf.cell(w_fecha, 7, "Fecha", 1, 0, "C", True)
            pdf.cell(w_maq,   7, "Maquina(s)", 1, 0, "C", True)
            pdf.cell(w_ref,   7, "Referencia(s) totalizadas", 1, 0, "C", True)
            pdf.cell(w_tcr,   7, "T.cambio_ref (h)", 1, 1, "C", True)
            pdf.set_font("Helvetica", "", 9)

        pdf.cell(w_fecha, 6, _sanitize_pdf_text(str(r["Fecha"])), 1)
        pdf.cell(w_maq,   6, _sanitize_pdf_text(str(r["Maquina"])), 1)

        # Celda multilínea para referencias (puede ser larga)
        x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.multi_cell(w_ref, 6, _sanitize_pdf_text(str(r["Referencia"])), border=1)
        y_after = pdf.get_y()
        h_used = y_after - y0
        pdf.set_xy(x0 + w_ref, y0)

        pdf.cell(w_tcr, h_used, _sanitize_pdf_text(_fmt_float(r["T.cambio_ref"], 2)), 1, 1, "R")

    pdf.set_font("Helvetica", "B", 10)
    prom = (total_h / n_refs) if n_refs else 0.0
    label = "TOTAL tiempo cambio_ref / N° referencias / Promedio (h)"
    pdf.cell(w_fecha + w_maq + w_ref, 7, _sanitize_pdf_text(label), 1, 0, "R")
    pdf.cell(w_tcr, 7, _sanitize_pdf_text(f"{_fmt_float(total_h, 2)}  /  {n_refs}  /  {_fmt_float(prom, 2)}"), 1, 1, "R")

def _build_pdf(
    operator_name: str,
    metrics: dict,
    chart_daily: str | None,
    chart_speed: str | None,
    chart_machines: str | None,
    chart_pie: str | None,
    chart_change_hours: str | None,
    ai_text: str | None,
    tbl_changeovers: pd.DataFrame,
    tot_change_time: float,
    n_refs: int
) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    fname = f"Informe_desempeno_imprgraf_{_slug(operator_name)}_{date_str}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    page_w = pdf.w - 2 * pdf.l_margin

    # Encabezado
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(0, 128, 0)
    top_y = pdf.get_y()
    pdf.cell(page_w / 2, 8, "Ciplas S.A.S", align="L")
    pdf.set_xy(pdf.l_margin + page_w / 2, top_y)
    pdf.set_text_color(50, 50, 50)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(page_w / 2, 8, "Área: Impresión Gráfica (IMPRGRAF)", align="R", ln=1)

    # Título
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(0, 70, 140)
    title = f"Informe de desempeño ({operator_name})"
    pdf.cell(0, 12, _sanitize_pdf_text(title), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(6)

    # Métricas básicas
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, _sanitize_pdf_text(f"Rangos de fecha del informe: {metrics.get('fecha_min','N/D')}  a  {metrics.get('fecha_max','N/D')}"), ln=1)
    pdf.cell(0, 8, _sanitize_pdf_text(f"Unidades producidas: {metrics.get('unidades','0')}"), ln=1)
    pdf.cell(0, 8, _sanitize_pdf_text(f"H. trabajadas / mes: {metrics.get('horas_trabajadas','0')} h"), ln=1)

    # Gráfico diario
    if chart_daily and os.path.exists(chart_daily):
        pdf.ln(4)
        pdf.image(chart_daily, w=page_w)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, _sanitize_pdf_text("Barras: Unidades por día | Línea: % productividad (eje derecho)"), ln=1)
    else:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, _sanitize_pdf_text("No fue posible generar el gráfico diario (no hay datos del mes o faltan columnas)."), ln=1)
        pdf.set_text_color(0, 0, 0)

    # Velocidad
    if chart_speed and os.path.exists(chart_speed):
        pdf.ln(6)
        pdf.image(chart_speed, w=page_w)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, _sanitize_pdf_text("Velocidad promedio diaria (m/min)"), ln=1)
    else:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, _sanitize_pdf_text("No fue posible generar el gráfico de velocidad (faltan columnas o datos)."), ln=1)
        pdf.set_text_color(0, 0, 0)

    # Por máquina
    if chart_machines and os.path.exists(chart_machines):
        pdf.ln(6)
        pdf.image(chart_machines, w=page_w)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, _sanitize_pdf_text("Por máquina - Barras: Unidades totales | Línea: % productividad (eje derecho)"), ln=1)
    else:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, _sanitize_pdf_text("No fue posible generar el gráfico por máquina (no hay datos del mes o faltan columnas)."), ln=1)
        pdf.set_text_color(0, 0, 0)

    # Pareto causas
    if chart_pie and os.path.exists(chart_pie):
        pdf.ln(6)
        pdf.image(chart_pie, w=page_w * 0.9)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, _sanitize_pdf_text("% de horas de paro por causa (Pareto 80% - mes del informe)"), ln=1)
    else:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, _sanitize_pdf_text("No fue posible generar el gráfico de causas de paro (no hay datos o falta Causa_Paro/Tiempo_Perdido)."), ln=1)
        pdf.set_text_color(0, 0, 0)

    # >>> Tabla de detalle justo debajo del torta (incluye total horas, # referencias y promedio)
    _render_changeover_table_pdf(pdf, tbl_changeovers, tot_change_time, n_refs)

    # >>> NUEVA gráfica: HORAS por FECHA usando la tabla creada
    if chart_change_hours and os.path.exists(chart_change_hours):
        pdf.ln(6)
        pdf.image(chart_change_hours, w=page_w)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.cell(0, 6, _sanitize_pdf_text("Horas de cambio de referencia por fecha (fuente: tabla anterior)"), ln=1)
    else:
        pdf.ln(6)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(200, 0, 0)
        pdf.cell(0, 6, _sanitize_pdf_text("No fue posible generar la gráfica de horas por fecha (tabla vacía)."), ln=1)
        pdf.set_text_color(0, 0, 0)

    # Análisis IA
    if ai_text:
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, _sanitize_pdf_text("Análisis de desempeño (IA)"), ln=1)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _sanitize_pdf_text(ai_text), align="L")
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 10)
        firma = "Analisis realizado por CiplasBot Agente I.A. Creado y desarrollado por Ing Wilson Calderon."
        pdf.multi_cell(0, 6, _sanitize_pdf_text(firma), align="L")

    pdf.output(out_path)
    return out_path

# =========================
# Handler principal
# =========================
def handle_performance_report_request_imprgraf(inbound_phone_raw: str, original_text: str) -> bool:
    msg_norm = (original_text or "").strip().lower()
    if msg_norm not in ("informe de desempeño imprgraf", "informe de desempeno imprgraf"):
        return False

    to_norm = _only_digits(canon_phone_e164_co(inbound_phone_raw) or inbound_phone_raw)
    op = _find_operator_by_phone(inbound_phone_raw)
    if not op:
        send_whatsapp_message(
            to_norm,
            "⚠️ No encuentro tu número en *operators_impr_graf.json*. Pide a sistemas que te registren."
        )
        _log_request(inbound_phone_raw, "", "requested", {"ok": False, "reason": "not_in_operators_imprgraf"})
        return True

    operator_name = (op.get("Apellidos_Nombres") or "").strip()
    if not operator_name:
        send_whatsapp_message(to_norm, "⚠️ Tu registro no tiene 'Apellidos_Nombres'. Avísale a sistemas.")
        _log_request(inbound_phone_raw, "", "requested", {"ok": False, "reason": "missing_name"})
        return True

    _log_request(inbound_phone_raw, operator_name, "requested")

    friendly = _first_name_from_fullname(operator_name)
    try:
        send_whatsapp_message(
            to_norm,
            f"👋 Hola *{friendly}*, recibí tu solicitud. Estoy generando tu *Informe IMPRGRAF*... ⏳"
        )
    except Exception:
        pass

    # Datos
    df_all = _download_data()
    df_op = _filter_by_operator(df_all, operator_name) if df_all is not None else None

    if df_all is None:
        send_whatsapp_message(to_norm, "ℹ️ No pude leer la hoja de datos ahora mismo. Generaré el informe con los campos disponibles.")
    elif df_op is not None and df_op.empty:
        send_whatsapp_message(to_norm, f"ℹ️ No encontré registros para **{operator_name}** en la hoja. Generaré el informe con campos vacíos/0.")

    # Métricas y mes/año
    metrics = _compute_metrics_for_operator(df_op)
    daily, info = _aggregate_daily_for_month(df_op)
    month, year = info.get("month") or 1, info.get("year") or 1900

    # Gráficos
    chart_daily = _plot_daily_bars_with_productivity(daily, operator_name, month, year)
    dfspeed = _aggregate_daily_speed_for_month(df_op, month, year)
    chart_speed = _plot_daily_speed_line(dfspeed, operator_name, month, year)
    g_machines = _aggregate_by_machine_for_month(df_op, month, year)
    chart_machines = _plot_machine_bars_with_productivity(g_machines, operator_name, month, year)
    g_cause = _aggregate_downtime_by_cause_for_month(df_op, month, year)
    chart_pie = _plot_pie_downtime_by_cause(g_cause, operator_name, month, year)

    # Cambios de referencia: TABLA y gráfica de HORAS por FECHA desde la TABLA
    tbl_changeovers, tot_change_time, n_refs = _build_changeover_detail_table(df_op, month, year)
    chart_change_hours = _plot_changeover_hours_by_date(tbl_changeovers, operator_name, month, year)

    # KPIs globales y análisis IA
    kpis = _compute_overall_kpis(df_op, month, year)
    ai_text = _generate_ai_analysis(operator_name, month, year, kpis, g_cause, daily)

    # PDF y envío
    pdf_path = _build_pdf(
        operator_name,
        metrics,
        chart_daily,
        chart_speed,
        chart_machines,
        chart_pie,
        chart_change_hours,
        ai_text,
        tbl_changeovers,
        tot_change_time,
        n_refs
    )

    sent_ok = False
    try:
        send_whatsapp_document(to_norm, pdf_path, caption="📄 Informe IMPRGRAF")
        sent_ok = True
    except Exception as e:
        send_whatsapp_message(to_norm, f"❌ Hubo un error enviando tu informe: {e}")

    _log_request(inbound_phone_raw, operator_name, "sent", {"ok": sent_ok})

    # Limpieza
    try:
        for p in [chart_daily, chart_speed, chart_machines, chart_pie, chart_change_hours]:
            _safe_unlink(p)
        if sent_ok:
            _safe_unlink(pdf_path)
    except Exception as e:
        print(f"⚠️ Error durante limpieza de artefactos: {e}")

    return True
