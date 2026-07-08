# workflows/extruder_performance_report.py
# Genera el informe de desempeño sección Extruder + Desperdicio + Tabla Referencias
# + Gráfico de Máquinas y Unidades + Gráfico Diario de Desperdicio
# PDF disparado por whatsapp

import os
import re
import json
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import requests
import pytz  # para timestamp en zona horaria local (Bogotá)

# Dependencias de gráficos/PDF
try:
    from fpdf import FPDF  # paquete: fpdf2
except ImportError as e:
    raise RuntimeError("Falta la dependencia 'fpdf2'. Instálala con: pip install fpdf2") from e

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError as e:
    raise RuntimeError("Falta la dependencia 'matplotlib'. Instálala con: pip install matplotlib") from e

# OpenAI para análisis IA
try:
    from openai import OpenAI
    _oa_client = OpenAI()
except Exception:
    _oa_client = None  # fallback si no hay clave

from services.session_memory import CONFIG_DIR
from services.whatsapp_service import send_whatsapp_message
from services.whatsapp_media import send_whatsapp_document
from services.wa_window_manager import canon_phone_e164_co

# =========================
# ARCHIVOS / RUTAS
# =========================
OPERATORS_FILE = os.path.join(CONFIG_DIR, "operators_extruder.json")
REPORTS_DIR = os.path.join(CONFIG_DIR, "extruder_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

PERFORMANCE_REQUESTS_DB = os.path.join(CONFIG_DIR, "extruder_requests_log.jsonl")
TZ = "America/Bogota"

# =========================
# ORÍGENES DE DATOS (EXTRUDER)
# =========================
SHEET_XLSX_URL = "https://docs.google.com/spreadsheets/d/1QulQQRGMANNv8sP17SAgu8Wox4eeri_0zHRb55zFKuY/export?format=xlsx"

# Columnas base (alias admitidos)
BASE_COLS = {
    "name": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre Operario", "Nombre_Operario"],
    "fecha": ["Fecha_Efectiva", "fecha_efectiva", "fecha", "Fecha", "Fecha Registro", "Fecha_Registro"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_cda", "tpo_corrida"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrstand", "corrida_estandar"],
    "cause": ["Causa_Paro", "causa_paro", "causa", "motivo_paro"],
    "unidades": ["Cantidad_Completada", "cantidad_completada", "unidades", "cantidad"],
    "kg_producidos": ["Cant_Kg", "cant_kg", "Kg", "kg", "Kg_producidos", "kg_total"],
    "cant_desp": ["Cant_Desp", "cant_desp", "Desperdicio", "desperdicio"],
    "referencia": ["Descripcion_Articulo", "descripcion_articulo", "articulo", "referencia"],
    "maq": ["Maquina", "Máquina", "maquina", "máquina", "machine", "equipo"]
}

# =========================
# UTILIDADES
# =========================
def _only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s or "").strip("_")

def _first_name_from_fullname(fullname: str) -> str:
    s = (fullname or "").strip()
    if not s: return "operario"
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
        op_digits = _only_digits(op.get("phone_e164", ""))
        if op_digits == inbound_digits:
            return op
    return None

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")

def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty: return None
    for c in candidates:
        if c in df.columns: return c
    norm_map = {_normalize(col): col for col in df.columns}
    for c in candidates:
        key = _normalize(c)
        if key in norm_map: return norm_map[key]
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

def _fmt_float(n, d=2) -> str:
    try:
        return f"{float(n):,.{d}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except Exception:
        return "0"

def _sanitize_pdf_text(s: str) -> str:
    if not s: return ""
    repl = {
        "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2018": "'", "\u2019": "'",
        "\u201C": '"', "\u201D": '"', "\u2022": "-", "\u00A0": " ",
        "✅": "", "⚠️": "", "📈": "", "💪": "", "📊": "", "⚙️": "", "🛑": "", "♻️": "", "📋": "", "🤖": "" # Evita crash en FPDF
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

def _safe_unlink(path: str):
    if not path: return
    try:
        if os.path.exists(path): os.remove(path)
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
        pass

def _log_request(phone_raw: str, operator_name: str, event: str, extra: dict | None = None):
    rec = {
        "ts": _now_bogota_iso(),
        "phone": _only_digits(canon_phone_e164_co(phone_raw) or phone_raw),
        "operator": (operator_name or "").strip(),
        "event": event
    }
    if extra: rec.update(extra)
    _append_jsonl(PERFORMANCE_REQUESTS_DB, rec)

def _download_data() -> pd.DataFrame | None:
    try:
        resp = requests.get(SHEET_XLSX_URL, timeout=60)
        resp.raise_for_status()
        return pd.read_excel(BytesIO(resp.content))
    except Exception as e:
        print(f"❌ Error descargando base principal: {e}")
        return None

# =========================
# FILTROS Y CÁLCULOS
# =========================
def _filter_by_operator(df: pd.DataFrame, target_name: str) -> pd.DataFrame:
    if df is None or df.empty: return df
    name_col = _find_col(df, BASE_COLS["name"])
    if not name_col: return df.iloc[0:0].copy()
    sub = df.copy()
    sub[name_col] = sub[name_col].astype(str)
    mask = sub[name_col].str.strip().str.casefold() == str(target_name).strip().casefold()
    return sub[mask].copy()

def _infer_range_from_df(df: pd.DataFrame) -> tuple[date | None, date | None]:
    if df is None or df.empty: return None, None
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    if not fecha_col: return None, None
    s = _safe_to_datetime(df[fecha_col]).dropna()
    if s.empty: return None, None
    return s.min().date(), s.max().date()

def _compute_metrics_for_operator(df: pd.DataFrame) -> dict:
    metrics = {"fecha_min": "N/D", "fecha_max": "N/D"}
    if df is None or df.empty: return metrics
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    if fecha_col:
        fechas = _safe_to_datetime(df[fecha_col])
        fmin = pd.to_datetime(fechas.min()) if not fechas.isna().all() else None
        fmax = pd.to_datetime(fechas.max()) if not fechas.isna().all() else None
        if pd.notna(fmin): metrics["fecha_min"] = fmin.strftime("%Y-%m-%d")
        if pd.notna(fmax): metrics["fecha_max"] = fmax.strftime("%Y-%m-%d")
    return metrics

def _get_month_year_from_df(df: pd.DataFrame) -> tuple[int | None, int | None]:
    if df is None or df.empty: return None, None
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    if not fecha_col: return None, None
    s_fecha = _safe_to_datetime(df[fecha_col])
    fmax = s_fecha.dropna().max()
    if pd.isna(fmax): return None, None
    return int(fmax.month), int(fmax.year)

def _aggregate_daily_for_month(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, list, dict]:
    """Retorna: productividad diaria, tabla pivote de máquinas (unidades), lista de máquinas, info extra."""
    info = {"month": None, "year": None}
    if df is None or df.empty: return pd.DataFrame(), pd.DataFrame(), [], info

    fecha_col = _find_col(df, BASE_COLS["fecha"])
    uni_col = _find_col(df, BASE_COLS["unidades"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    cs_col = _find_col(df, BASE_COLS["cs"])
    maq_col = _find_col(df, BASE_COLS["maq"])

    if not fecha_col: return pd.DataFrame(), pd.DataFrame(), [], info

    s_fecha = _safe_to_datetime(df[fecha_col])
    fmax = s_fecha.dropna().max()
    if pd.isna(fmax): return pd.DataFrame(), pd.DataFrame(), [], info

    month, year = int(fmax.month), int(fmax.year)
    info["month"], info["year"] = month, year

    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]
    dfm["day"] = dfm["_fecha"].dt.day

    # 1. Productividad Diaria General
    agg_prod = {}
    if tc_col: agg_prod[tc_col] = "sum"
    if tp_col: agg_prod[tp_col] = "sum"
    if cs_col: agg_prod[cs_col] = "sum"

    if agg_prod:
        daily_prod = dfm.groupby("day").agg(agg_prod).reset_index()
        denom = pd.to_numeric(daily_prod.get(tc_col, 0), errors="coerce").fillna(0) + pd.to_numeric(daily_prod.get(tp_col, 0), errors="coerce").fillna(0)
        num = pd.to_numeric(daily_prod.get(cs_col, 0), errors="coerce").fillna(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            daily_prod["prod_pct"] = np.where(denom > 0, (num / denom) * 100.0, np.nan)
    else:
        daily_prod = pd.DataFrame({"day": dfm["day"].unique(), "prod_pct": np.nan})

    daily_prod = daily_prod.sort_values("day")

    # 2. Unidades por Máquina
    machines = []
    pivot_mach = pd.DataFrame()
    if maq_col and uni_col:
        dfm[uni_col] = pd.to_numeric(dfm[uni_col], errors="coerce").fillna(0)
        dfm[maq_col] = dfm[maq_col].astype(str).str.strip().str.upper()
        daily_mach = dfm.groupby(["day", maq_col])[uni_col].sum().reset_index()
        if not daily_mach.empty:
            pivot_mach = daily_mach.pivot(index="day", columns=maq_col, values=uni_col).fillna(0)
            machines = list(pivot_mach.columns)
    elif uni_col:
        dfm[uni_col] = pd.to_numeric(dfm[uni_col], errors="coerce").fillna(0)
        daily_mach = dfm.groupby("day")[uni_col].sum().reset_index()
        if not daily_mach.empty:
            pivot_mach = daily_mach.set_index("day")
            pivot_mach.columns = ["Unidades"]
            machines = ["Unidades"]

    return daily_prod, pivot_mach, machines, info

def _aggregate_daily_waste(df: pd.DataFrame, month: int, year: int) -> pd.DataFrame:
    if df is None or df.empty: return pd.DataFrame()
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    desp_col = _find_col(df, BASE_COLS["cant_desp"])
    kg_col = _find_col(df, BASE_COLS["kg_producidos"])

    if not fecha_col or not desp_col or not kg_col: return pd.DataFrame()

    s_fecha = _safe_to_datetime(df[fecha_col])
    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]
    dfm["day"] = dfm["_fecha"].dt.day

    g = dfm.groupby("day").agg({desp_col: "sum", kg_col: "sum"}).reset_index()
    g = g.rename(columns={desp_col: "kg_desp", kg_col: "kg_prod"})

    kg_d = pd.to_numeric(g["kg_desp"], errors="coerce").fillna(0.0)
    kg_p = pd.to_numeric(g["kg_prod"], errors="coerce").fillna(0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        g["pct_desp"] = np.where(kg_p > 0, (kg_d / kg_p) * 100.0, 0.0)

    return g

def _aggregate_downtime_by_cause_for_month(df: pd.DataFrame, month: int, year: int) -> pd.DataFrame:
    if df is None or df.empty: return df
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    cause_col = _find_col(df, BASE_COLS["cause"])

    if not fecha_col or not tp_col or not cause_col: return df.iloc[0:0].copy()

    s_fecha = _safe_to_datetime(df[fecha_col])
    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    g = dfm.groupby(cause_col)[tp_col].sum().reset_index()
    g = g.rename(columns={cause_col: "cause", tp_col: "horas"})
    g["horas"] = pd.to_numeric(g["horas"], errors="coerce").fillna(0.0)

    total = g["horas"].sum()
    if total <= 0: return g.iloc[0:0].copy()

    g = g.sort_values("horas", ascending=False, kind="stable").reset_index(drop=True)
    g["pct"] = (g["horas"] / total) * 100.0
    g["cum_pct"] = g["pct"].cumsum()
    cutoff_idx = (g["cum_pct"] <= 80.0).sum()
    if cutoff_idx == 0: cutoff_idx = 1
    if g["cum_pct"].iloc[cutoff_idx - 1] < 80.0 and cutoff_idx < len(g): cutoff_idx += 1

    return g.iloc[:cutoff_idx][["cause", "horas", "pct"]]

def _aggregate_changeovers_for_month(df: pd.DataFrame, month: int, year: int) -> tuple[dict, pd.DataFrame]:
    metrics = {"count": 0, "avg_hours": "0"}
    if df is None or df.empty: return metrics, pd.DataFrame()

    fecha_col = _find_col(df, BASE_COLS["fecha"])
    cause_col = _find_col(df, BASE_COLS["cause"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    if not fecha_col or not cause_col or not tp_col: return metrics, pd.DataFrame()

    s_fecha = _safe_to_datetime(df[fecha_col])
    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    causes = dfm[cause_col].astype(str).str.strip().str.casefold()
    mask = causes.str.contains("cambio", na=False) & causes.str.contains("refer", na=False)
    dfchg = dfm[mask].copy()

    count = int(len(dfchg))
    total_tp = pd.to_numeric(dfchg[tp_col], errors="coerce").fillna(0.0).sum()
    avg_h = (total_tp / count) if count > 0 else 0.0

    metrics["count"] = count
    metrics["avg_hours"] = _fmt_float(avg_h, 2)

    if count == 0: return metrics, pd.DataFrame()

    daily = dfchg.groupby(dfchg["_fecha"].dt.date).size().reset_index(name="count")
    if daily.columns[0] != "date": daily = daily.rename(columns={daily.columns[0]: "date"})
    daily["day"] = pd.to_datetime(daily["date"]).dt.day
    return metrics, daily.sort_values("day")[["day", "count"]]

def _compute_overall_kpis(df: pd.DataFrame, month: int, year: int) -> dict:
    res = {"productividad_total_pct": None}
    if df is None or df.empty: return res
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    cs_col = _find_col(df, BASE_COLS["cs"])

    if not fecha_col: return res

    s_fecha = _safe_to_datetime(df[fecha_col])
    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    if cs_col and tc_col and tp_col:
        num = pd.to_numeric(dfm[cs_col], errors="coerce").fillna(0.0).sum()
        denom = pd.to_numeric(dfm[tc_col], errors="coerce").fillna(0.0).sum() + pd.to_numeric(dfm[tp_col], errors="coerce").fillna(0.0).sum()
        res["productividad_total_pct"] = float((num / denom) * 100.0) if denom > 0 else None

    return res

def _sum_kg_produced_in_range(df_op: pd.DataFrame, start: date | None, end: date | None) -> float:
    if df_op is None or df_op.empty or (start is None) or (end is None): return 0.0
    fecha_col = _find_col(df_op, BASE_COLS["fecha"])
    kg_col = _find_col(df_op, BASE_COLS["kg_producidos"])
    if not fecha_col or not kg_col: return 0.0
    s = _safe_to_datetime(df_op[fecha_col])
    mask = (s.dt.date >= start) & (s.dt.date <= end)
    return float(pd.to_numeric(df_op.loc[mask, kg_col], errors="coerce").fillna(0.0).sum())

def _sum_waste_in_range(df_op: pd.DataFrame, start: date | None, end: date | None) -> float:
    if df_op is None or df_op.empty or (start is None) or (end is None): return 0.0
    fecha_col = _find_col(df_op, BASE_COLS["fecha"])
    waste_col = _find_col(df_op, BASE_COLS["cant_desp"])
    if not fecha_col or not waste_col: return 0.0
    s = _safe_to_datetime(df_op[fecha_col])
    mask = (s.dt.date >= start) & (s.dt.date <= end)
    return float(pd.to_numeric(df_op.loc[mask, waste_col], errors="coerce").fillna(0.0).sum())

def _aggregate_references_for_month(df: pd.DataFrame, month: int, year: int) -> pd.DataFrame:
    if df is None or df.empty: return pd.DataFrame()
    
    fecha_col = _find_col(df, BASE_COLS["fecha"])
    ref_col = _find_col(df, BASE_COLS["referencia"])
    kg_col = _find_col(df, BASE_COLS["kg_producidos"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    cs_col = _find_col(df, BASE_COLS["cs"])
    
    if not fecha_col or not ref_col or not kg_col: return pd.DataFrame()

    s_fecha = _safe_to_datetime(df[fecha_col])
    dfm = df.copy()
    dfm["_fecha"] = s_fecha
    dfm = dfm[(dfm["_fecha"].dt.month == month) & (dfm["_fecha"].dt.year == year)]

    agg_dict = {kg_col: "sum"}
    if tc_col: agg_dict[tc_col] = "sum"
    if tp_col: agg_dict[tp_col] = "sum"
    if cs_col: agg_dict[cs_col] = "sum"

    g = dfm.groupby(ref_col).agg(agg_dict).reset_index()
    g = g.rename(columns={ref_col: "referencia", kg_col: "kg_producidos"})
    if tc_col: g = g.rename(columns={tc_col: "tc"})
    if tp_col: g = g.rename(columns={tp_col: "tp"})
    if cs_col: g = g.rename(columns={cs_col: "cs"})

    if "tc" in g.columns and "tp" in g.columns and "cs" in g.columns:
        denom = pd.to_numeric(g["tc"], errors="coerce").fillna(0) + pd.to_numeric(g["tp"], errors="coerce").fillna(0)
        num = pd.to_numeric(g["cs"], errors="coerce").fillna(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            g["prod_pct"] = np.where(denom > 0, (num / denom) * 100.0, np.nan)
    else:
        g["prod_pct"] = np.nan

    g["kg_producidos"] = pd.to_numeric(g["kg_producidos"], errors="coerce").fillna(0)
    g = g.sort_values("kg_producidos", ascending=False, kind="stable").reset_index(drop=True)
    
    cols_to_return = ["referencia", "kg_producidos", "prod_pct"]
    existing_cols = [c for c in cols_to_return if c in g.columns]
    return g[existing_cols]

# =========================
# PLOTEO
# =========================
def _plot_daily_bars_with_productivity_units(daily_prod: pd.DataFrame, pivot_mach: pd.DataFrame, machines: list, operator_name: str, month: int, year: int) -> str | None:
    if daily_prod.empty and pivot_mach.empty: return None
    
    days = daily_prod["day"].to_list() if not daily_prod.empty else pivot_mach.index.to_list()
    prod = daily_prod.get("prod_pct", pd.Series([np.nan]*len(days))).to_list()

    fig, ax = plt.subplots(figsize=(10, 4.5), dpi=130)

    # Barras apiladas por Máquina (Unidades)
    if not pivot_mach.empty:
        bottom = np.zeros(len(pivot_mach.index))
        cmap = plt.get_cmap("Set2")
        for i, mach in enumerate(machines):
            values = pivot_mach[mach].values
            ax.bar(pivot_mach.index, values, bottom=bottom, label=mach, color=cmap(i%8), width=0.8, edgecolor="none")
            bottom += values
        
        ax.legend(loc="upper left", bbox_to_anchor=(1.05, 1), frameon=False, title="Máquinas")

    ax.set_xlabel("Día")
    ax.set_ylabel("Unidades Producidas")
    
    mach_title = f" | {', '.join([str(m) for m in machines])}" if machines else ""
    ax.set_title(f"{operator_name} - Unidades vs Productividad ({year}-{month:02d}){mach_title}", fontsize=11)

    # Línea de Productividad
    ax2 = ax.twinx()
    if np.isfinite(np.array(prod)).any():
        ax2.plot(days, prod, marker="o", color="darkorange", linewidth=2, label="% Prod.")
        ax2.set_ylabel("Productividad (%)")
        valid = [p for p in prod if pd.notna(p)]
        if valid: ax2.set_ylim(0, max(100.0, max(valid) * 1.15))
    else:
        ax2.set_ylabel("Productividad (%)")
        ax2.set_ylim(0, 100)

    ax.grid(axis="y", linestyle="--", alpha=0.5)
    plt.subplots_adjust(right=0.80) # Espacio para la leyenda
    fig.tight_layout()

    img_name = f"grafico_diario_unidades_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

def _plot_daily_waste_chart(daily_waste: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    if daily_waste is None or daily_waste.empty: return None
    days = daily_waste["day"].to_list()
    kg_desp = daily_waste["kg_desp"].to_list()
    pct_desp = daily_waste["pct_desp"].to_list()

    fig, ax = plt.subplots(figsize=(10, 4.0), dpi=130)
    ax.bar(days, kg_desp, color="salmon", width=0.8, edgecolor="none", label="Kg Desperdicio")
    ax.set_xlabel("Día")
    ax.set_ylabel("Kg Desperdicio")
    ax.set_title(f"Desperdicio Diario ({year}-{month:02d})", fontsize=11)

    ax2 = ax.twinx()
    ax2.plot(days, pct_desp, marker="o", color="firebrick", linewidth=2, label="% Desperdicio")
    ax2.set_ylabel("% Desperdicio")
    
    valid_pct = [p for p in pct_desp if pd.notna(p)]
    if any(p > 0 for p in valid_pct):
        ax2.set_ylim(0, max(max(valid_pct) * 1.2, 5.0)) # Mínimo 5% en escala
    else:
        ax2.set_ylim(0, 5)

    ax.grid(axis="y", linestyle="--", alpha=0.5)
    fig.tight_layout()

    img_name = f"grafico_desperdicio_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

def _plot_bar_downtime_by_cause(gcause: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    if gcause is None or gcause.empty: return None
    labels = gcause["cause"].astype(str).tolist()
    horas = gcause["horas"].tolist()

    if sum(horas) <= 0: return None

    fig, ax = plt.subplots(figsize=(10, max(3.5, len(labels) * 0.5)), dpi=130)
    y_pos = np.arange(len(labels))
    ax.barh(y_pos, horas, align='center', color='coral', edgecolor='none')
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels)
    ax.invert_yaxis() 
    
    ax.set_xlabel('Horas Totales')
    ax.set_title(f"Causas de tiempo perdido (Pareto 80% - {year}-{month:02d})")
    ax.grid(axis="x", linestyle="--", alpha=0.5)

    fig.tight_layout()
    img_name = f"grafico_causas_barras_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

def _plot_changeovers_by_day_bar(daily: pd.DataFrame, operator_name: str, month: int, year: int) -> str | None:
    if daily is None or daily.empty: return None
    days = daily["day"].to_list()
    counts = daily["count"].to_list()

    fig, ax = plt.subplots(figsize=(10, 3.5), dpi=130)
    cmap = plt.get_cmap("Purples")
    colors = [cmap(0.55 + 0.4 * (i / max(1, len(days)-1))) for i in range(len(days))]
    ax.bar(days, counts, width=0.8, color=colors, edgecolor="none")

    ax.set_xlabel("Día")
    ax.set_ylabel("# Cambios de referencia")
    ax.set_title(f"Cambios por dia ({year}-{month:02d})")
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    fig.tight_layout()
    img_name = f"grafico_cambios_{_slug(operator_name)}_{year}{month:02d}.png"
    img_path = os.path.join(REPORTS_DIR, img_name)
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

# =========================
# IA: Generación de análisis
# =========================
def _generate_ai_analysis(
    operator_name: str, month: int, year: int,
    kpis: dict, pareto_df: pd.DataFrame, waste_pct: float | None,
    pivot_mach: pd.DataFrame
) -> str:
    val_prod = kpis.get("productividad_total_pct")
    prod_txt = "N/D" if (val_prod is None or pd.isna(val_prod)) else f"{val_prod:.1f}%"
    waste_txt = "N/D" if (waste_pct is None or pd.isna(waste_pct)) else f"{waste_pct:.2f}%"

    var_info = {"cv_pct": "N/D"}
    if not pivot_mach.empty:
        total_units_per_day = pivot_mach.sum(axis=1)
        if len(total_units_per_day) > 0 and total_units_per_day.sum() > 0:
            mean_u = total_units_per_day.mean()
            std_u = total_units_per_day.std(ddof=0)
            if mean_u > 0: var_info["cv_pct"] = round(float((std_u / mean_u) * 100.0), 1)

    top_causas = []
    if pareto_df is not None and not pareto_df.empty:
        for _, r in pareto_df.head(3).iterrows():
            top_causas.append(f"{str(r.get('cause',''))[:35]} ({float(r.get('horas',0.0)):.1f}h)")
    causas_line = ", ".join(top_causas) if top_causas else "Sin paros destacados"

    system_prompt = (
        "Eres CiplasBot, analista de planta. Escribe para OPERARIOS: lenguaje sencillo, motivador y directo. "
        "Debes estructurar tu respuesta EXACTAMENTE con estos 4 títulos (escribelos textualmente):\n"
        "6.1 Resumen\n"
        "6.2 Fortalezas\n"
        "6.3 Por mejorar\n"
        "6.4 Variabilidad\n"
        "Agrega 4-5 emojis pertinentes en todo el texto."
    )

    user_prompt = (
        f"Operario: {operator_name} | Periodo: {year}-{month:02d}\n"
        f"Productividad: {prod_txt} (Meta 80%)\n"
        f"Desperdicio: {waste_txt} (Meta 3%)\n"
        f"Top Paros: {causas_line}\n"
        f"CV (Variabilidad unidades): {var_info['cv_pct']}%\n"
        "Genera los 4 puntos de análisis solicitados."
    )

    try:
        if _oa_client is None: raise RuntimeError("OpenAI no configurado")
        chat = _oa_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.35,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return _sanitize_pdf_text((chat.choices[0].message.content or "").strip())
    except Exception:
        fallback = (
            "6.1 Resumen: Datos recopilados correctamente.\n"
            "6.2 Fortalezas: Producción constante.\n"
            "6.3 Por mejorar: Atacar los tiempos de paro registrados.\n"
            "6.4 Variabilidad: Análisis no disponible temporalmente."
        )
        return _sanitize_pdf_text(fallback)

# =========================
# PDF BUILDER
# =========================
def _build_pdf(
    operator_name: str, metrics: dict,
    chart_daily_units: str | None,
    chart_daily_waste: str | None,
    chart_bar_cause: str | None,
    changes_metrics: dict, chart_changes: str | None,
    waste_section: dict, references_df: pd.DataFrame, ai_text: str | None
) -> str:
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    fname = f"Informe_Extruder_{_slug(operator_name)}_{date_str}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    page_w = pdf.w - 2 * pdf.l_margin

    # 1. Encabezado e info
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(0, 128, 0)
    top_y = pdf.get_y()
    pdf.cell(page_w / 2, 8, "Ciplas S.A.S", align="L")
    pdf.set_xy(pdf.l_margin + page_w / 2, top_y)
    pdf.set_text_color(50, 50, 50)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(page_w / 2, 8, "Área: Extruder", align="R", ln=1)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(0, 70, 140)
    pdf.cell(0, 12, _sanitize_pdf_text(f"Informe de desempeño - {operator_name}"), ln=1)
    
    # 2. Rango de Fecha
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 8, _sanitize_pdf_text(f"Rango del informe: {metrics.get('fecha_min','N/D')} a {metrics.get('fecha_max','N/D')}"), ln=1)

    # 3. Producción (Unidades) y Máquinas
    if chart_daily_units and os.path.exists(chart_daily_units):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(0, 102, 204) # Azul
        pdf.cell(0, 8, _sanitize_pdf_text("Produccion Diaria (Unidades) y Productividad"), ln=1)
        pdf.image(chart_daily_units, w=page_w)
    
    # 4. Desperdicio (Métricas + Gráfico)
    if pdf.get_y() > 200: pdf.add_page()
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(204, 0, 0) # Rojo
    pdf.cell(0, 8, _sanitize_pdf_text("Control de Desperdicio"), ln=1)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, _sanitize_pdf_text(f"Total Desperdicio: {_fmt_float(waste_section.get('kg_waste', 0.0), 2)} Kg"), ln=1)
    
    pct = waste_section.get("pct_waste", None)
    if isinstance(pct, (int, float)) and not (np.isnan(pct) or np.isinf(pct)):
        pdf.set_font("Helvetica", "B", 11)
        if float(pct) > 3.0: pdf.set_text_color(204, 0, 0)
        else: pdf.set_text_color(0, 128, 0)
        pdf.cell(0, 6, _sanitize_pdf_text(f"% Desperdicio Global: {pct:.2f}%"), ln=1)
    
    if chart_daily_waste and os.path.exists(chart_daily_waste):
        pdf.image(chart_daily_waste, w=page_w * 0.95)

    # 5. Causas de Tiempo Perdido (Barras)
    if pdf.get_y() > 220: pdf.add_page()
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(204, 102, 0) # Naranja oscuro
    pdf.cell(0, 8, _sanitize_pdf_text("Causas de Tiempo Perdido (Horas)"), ln=1)
    if chart_bar_cause and os.path.exists(chart_bar_cause):
        pdf.image(chart_bar_cause, w=page_w * 0.95)

    # 6. Cambios de Referencia
    if pdf.get_y() > 220: pdf.add_page()
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(102, 0, 204) # Morado
    pdf.cell(0, 8, _sanitize_pdf_text("Cambios de Referencia"), ln=1)
    
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 6, _sanitize_pdf_text(f"Total acumulado: {changes_metrics.get('count', 0)} cambios | Tiempo promedio: {changes_metrics.get('avg_hours', '0')} horas"), ln=1)
    
    if chart_changes and os.path.exists(chart_changes):
        pdf.image(chart_changes, w=page_w * 0.9)

    # 7. Tabla de Referencias Procesadas
    if references_df is not None and not references_df.empty:
        if pdf.get_y() > 210: pdf.add_page()
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(0, 102, 102) # Verde oscuro
        pdf.cell(0, 8, _sanitize_pdf_text("Referencias Procesadas"), ln=1)
        
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_text_color(0, 0, 0)
        pdf.set_fill_color(220, 230, 241) # Azul claro para cabecera
        
        col_w_ref = page_w * 0.55
        col_w_kg = page_w * 0.20
        col_w_prod = page_w * 0.25
        
        pdf.cell(col_w_ref, 8, "Referencia", border=1, align="C", fill=True)
        pdf.cell(col_w_kg, 8, "Kg Producidos", border=1, align="C", fill=True)
        pdf.cell(col_w_prod, 8, "% Productividad", border=1, align="C", fill=True, ln=1)
        
        pdf.set_font("Helvetica", "", 9)
        for _, row in references_df.iterrows():
            ref_name = str(row.get("referencia", "N/D"))
            if len(ref_name) > 45: ref_name = ref_name[:42] + "..."
            
            kg_val = float(row.get("kg_producidos", 0.0))
            prod_val = row.get("prod_pct")
            
            pdf.cell(col_w_ref, 6, _sanitize_pdf_text(ref_name), border=1, align="L")
            pdf.cell(col_w_kg, 6, f"{kg_val:,.2f}", border=1, align="R")
            if pd.notna(prod_val):
                pdf.cell(col_w_prod, 6, f"{prod_val:.1f}%", border=1, align="R", ln=1)
            else:
                pdf.cell(col_w_prod, 6, "N/D", border=1, align="R", ln=1)

    # 8. Análisis de desempeño IA
    if ai_text:
        if pdf.get_y() > 200: pdf.add_page()
        pdf.ln(8)
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(0, 102, 51) # Turquesa oscuro
        pdf.cell(0, 8, _sanitize_pdf_text("Análisis de Desempeño (IA)"), ln=1)
        
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _sanitize_pdf_text(ai_text), align="L")
        
        pdf.ln(6)
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(100, 100, 100)
        pdf.multi_cell(0, 5, "Generado por CiplasBot Agente I.A. - Desarrollado por Ing. Wilson Calderon.", align="C")

    pdf.output(out_path)
    return out_path

# =========================
# FLUJO PRINCIPAL
# =========================
def handle_extruder_report_request(inbound_phone_raw: str, original_text: str) -> bool:
    msg_norm = (original_text or "").strip().lower()
    
    if msg_norm not in ("informe extruder", "informe extrusion"):
        return False

    to_norm = _only_digits(canon_phone_e164_co(inbound_phone_raw) or inbound_phone_raw)
    op = _find_operator_by_phone(inbound_phone_raw)
    
    if not op:
        send_whatsapp_message(to_norm, "⚠️ No encuentro tu número en la base de operarios de Extrusión.")
        _log_request(inbound_phone_raw, "", "requested", {"ok": False, "reason": "not_in_extruder"})
        return True

    area = op.get("area", "").strip().upper()
    operator_name = (op.get("Apellidos_Nombres") or "").strip()

    # ==========================================
    # NUEVO: INTERCEPTOR GERENCIAL (RBAC)
    # ==========================================
    if area in ["INGENIERO_EXTRUDER", "GERENTE_EXTRUDER", "GERENTE_PLANTA"]:
        from workflows.manager_extruder_report import handle_manager_extruder_report
        return handle_manager_extruder_report(inbound_phone_raw, operator_name, to_norm)
    # ==========================================

    if area != "EXTRUDER":
        send_whatsapp_message(to_norm, f"⚠️ Este comando es exclusivo para Extrusión. Tu área es: *{area}*.")
        _log_request(inbound_phone_raw, operator_name, "requested", {"ok": False, "reason": "wrong_area"})
        return True

    _log_request(inbound_phone_raw, operator_name, "requested")

    friendly = _first_name_from_fullname(operator_name)
    try:
        send_whatsapp_message(to_norm, f"📊 Hola *{friendly}*, generando tu Informe Extruder... ⏳")
    except: pass

    # 1. Datos
    df_all = _download_data()
    df_op = _filter_by_operator(df_all, operator_name) if df_all is not None else None

    metrics = _compute_metrics_for_operator(df_op)
    month, year = _get_month_year_from_df(df_op)
    month, year = month or 1, year or 1900

    # 2. Agrupaciones
    daily_prod, pivot_mach, machines, _ = _aggregate_daily_for_month(df_op)
    daily_waste = _aggregate_daily_waste(df_op, month, year)
    g_cause = _aggregate_downtime_by_cause_for_month(df_op, month, year)
    changes_metrics, daily_changes = _aggregate_changeovers_for_month(df_op, month, year)
    references_df = _aggregate_references_for_month(df_op, month, year)

    # 3. Gráficos
    chart_daily_units = _plot_daily_bars_with_productivity_units(daily_prod, pivot_mach, machines, operator_name, month, year)
    chart_daily_waste = _plot_daily_waste_chart(daily_waste, operator_name, month, year)
    chart_bar_cause = _plot_bar_downtime_by_cause(g_cause, operator_name, month, year)
    chart_changes = _plot_changeovers_by_day_bar(daily_changes, operator_name, month, year)

    # 4. Desperdicio General
    start_date, end_date = _infer_range_from_df(df_op)
    kg_waste = _sum_waste_in_range(df_op, start_date, end_date) if (start_date and end_date) else 0.0
    kg_prod = _sum_kg_produced_in_range(df_op, start_date, end_date) if (start_date and end_date) else 0.0
    
    pct_waste = float((kg_waste / kg_prod) * 100.0) if kg_prod > 0 else None

    waste_section = {
        "kg_waste": kg_waste,
        "kg_prod": kg_prod,
        "pct_waste": pct_waste
    }

    # 5. Inteligencia Artificial
    kpis = _compute_overall_kpis(df_op, month, year)
    ai_text = _generate_ai_analysis(operator_name, month, year, kpis, g_cause, pct_waste, pivot_mach)

    # 6. PDF
    pdf_path = _build_pdf(
        operator_name, metrics,
        chart_daily_units, chart_daily_waste,
        chart_bar_cause, changes_metrics, chart_changes,
        waste_section, references_df, ai_text
    )

    # 7. Envío y Limpieza
    sent_ok = False
    try:
        send_whatsapp_document(to_norm, pdf_path, caption="📄 Aquí tienes tu Informe de Desempeño 📊")
        sent_ok = True
    except Exception as e:
        send_whatsapp_message(to_norm, f"❌ Hubo un error enviando tu informe.")

    _log_request(inbound_phone_raw, operator_name, "sent", {"ok": sent_ok})

    try:
        artifacts = [chart_daily_units, chart_daily_waste, chart_bar_cause, chart_changes]
        for p in artifacts: _safe_unlink(p)
        if sent_ok: _safe_unlink(pdf_path)
    except Exception: pass

    return True