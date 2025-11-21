# workflows/admin_line_report.py
# Informe Gerencial (Línea COM1–COM5) para admin/supervisor:
#   • Comando: "informe de desempeño" (también acepta "informe de desempeno")
#   • Solo habilitado para: 573176380061 (admin) y 573168308454 (supervisor área)
#   • Permite elegir fecha o rango; filtra por máquinas COM1..COM5 (columna Maquina)
#   • Envía PDF por WhatsApp y elimina el archivo PDF local si el envío fue exitoso

import os
import re
import json
import unicodedata
from io import BytesIO
from datetime import datetime, date

import numpy as np
import pandas as pd
import requests

# PDF / Gráficos
try:
    from fpdf import FPDF  # fpdf2
except ImportError as e:
    raise RuntimeError("Falta 'fpdf2'. Instálala con: pip install fpdf2") from e

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError as e:
    raise RuntimeError("Falta 'matplotlib'. Instálala con: pip install matplotlib") from e

# Servicios internos del proyecto
from services.session_memory import CONFIG_DIR, SUPERVISORS_FILE, sessions
from services.whatsapp_service import send_whatsapp_message
from services.whatsapp_media import send_whatsapp_document
from services.wa_window_manager import canon_phone_e164_co

# =========================
# ARCHIVOS / RUTAS
# =========================
REPORTS_DIR = os.path.join(CONFIG_DIR, "admin_line_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# Mismas fuentes del módulo de operario
SHEET_XLSX_URL = "https://docs.google.com/spreadsheets/d/1V-9iIVMLf19vuQIoiu53t6k2J2vlu49vUjEMnKS5bLY/export?format=xlsx"
WASTE_SHEET_XLSX_URL = "https://docs.google.com/spreadsheets/d/1QmoSjCOvCz_l-gIbkFbabZtqFSMcPjItyylw_6cAqvc/export?format=xlsx"

# Columnas base (alias admitidos) - hoja principal
BASE_COLS = {
    "name": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre Operario", "Nombre_Operario"],
    "fecha": ["Fecha_Efectiva", "fecha_efectiva", "fecha", "Fecha", "Fecha Registro", "Fecha_Registro"],
    "unidades": ["Cantidad_Completada", "cantidad_completada", "unidades", "cantidad"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_cda", "tpo_corrida"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrstand", "corrida_estandar"],
    "maq": ["Maquina", "Máquina", "maquina", "máquina", "machine", "equipo"],
    "cause": ["Causa_Paro", "causa_paro", "causa", "motivo_paro"],
    "code": ["Codigo_Paro", "codigo_paro", "cod_paro", "codigo", "cod"],
    "kg_producidos": ["Cant_Kg", "cant_kg", "Kg", "kg", "Kg_producidos", "kg_total"]
}

# Columnas desperdicio (alias admitidos) - hoja desperdicio
WASTE_COLS = {
    "fecha": ["Fecha del informe", "fecha_del_informe", "Fecha", "fecha"],
    "name": ["Nombre Operario", "Operario", "Apellidos_Nombres", "apellidos_nombres", "Nombre_Operario"],
    "kg": ["Kg desperdicio", "KG desperdicio", "kg_desperdicio", "Desperdicio kg", "desperdicio_kg", "Kg_Desperdicio"],
}

# =========================
# NORMALIZACIÓN Y COMANDO
# =========================
def _fold(s: str) -> str:
    """Minúsculas, sin tildes, espacios normalizados."""
    s = unicodedata.normalize("NFKD", s or "")
    s = s.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", s).strip().lower()

# === Restricción por solicitante y máquinas permitidas ===
ALLOWED_REQUESTERS = {"573176380061", "573168308454"}  # admin y supervisor del área
ALLOWED_MACHINES = {"COM1", "COM2", "COM3", "COM4", "COM5"}

def _is_allowed_requester(phone_raw: str) -> bool:
    """Solo estos números pueden invocar el informe gerencial por 'informe de desempeño'."""
    d = re.sub(r"\D", "", canon_phone_e164_co(phone_raw) or phone_raw)
    return d in ALLOWED_REQUESTERS

# Trigger del comando: “informe de desempeño” (acepta sin tilde)
COMMAND_RE = re.compile(r"^(informe de desempeno|informe de desempeño)\b", re.IGNORECASE)

def claims_admin_line_report_command(text: str) -> bool:
    return bool(COMMAND_RE.search(_fold(text)))

# =========================
# UTILIDADES
# =========================
def _only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s or "").strip("_")

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
    # (sin infer_datetime_format por deprecado)
    s2 = pd.to_datetime(s, errors="coerce", dayfirst=True)
    if s2.isna().any():
        try:
            snum = pd.to_numeric(s, errors="coerce")
            s_excel = pd.to_datetime(snum, unit="d", origin="1899-12-30", errors="coerce")
            s2 = s2.fillna(s_excel)
        except Exception:
            pass
    return s2

def _sanitize_pdf_text(s: str) -> str:
    if not s:
        return ""
    repl = {
        "\u2013": "-", "\u2014": "-", "\u2015": "-",
        "\u2018": "'", "\u2019": "'", "\u201C": '"', "\u201D": '"',
        "\u2022": "-", "\u00A0": " "
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

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

def _safe_unlink(path: str):
    """Elimina un archivo si existe, sin romper el flujo."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"⚠️ No se pudo eliminar {path}: {e}")

# =========================
# DESCARGA DE DATOS
# =========================
def _download_main_df() -> pd.DataFrame | None:
    try:
        r = requests.get(SHEET_XLSX_URL, timeout=60)
        r.raise_for_status()
        xls = BytesIO(r.content)
        df = pd.read_excel(xls)
        return df
    except Exception as e:
        print(f"❌ Error descargando base principal: {e}")
        return None

def _download_waste_df() -> pd.DataFrame | None:
    try:
        r = requests.get(WASTE_SHEET_XLSX_URL, timeout=60)
        r.raise_for_status()
        xls = BytesIO(r.content)
        df = pd.read_excel(xls)
        return df
    except Exception as e:
        print(f"❌ Error descargando base desperdicio: {e}")
        return None

# =========================
# FECHAS / RANGOS
# =========================
DATE_PAT = re.compile(
    r"(?P<d1>(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4}))(?:\s*[aA]\s*(?P<d2>(\d{4}-\d{2}-\d{2}|\d{2}[/-]\d{2}[/-]\d{4})))?"
)

def _parse_date_text(s: str) -> tuple[date | None, date | None]:
    m = DATE_PAT.search((_fold(s) or "").strip())
    if not m:
        return None, None

    def _to_date(token: str) -> date | None:
        token = token.strip()
        try:
            if "-" in token and token.count("-") == 2 and token[4] == "-":
                return datetime.strptime(token, "%Y-%m-%d").date()
        except Exception:
            pass
        for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(token, fmt).date()
            except Exception:
                continue
        return None

    d1 = _to_date(m.group("d1"))
    d2 = _to_date(m.group("d2")) if m.group("d2") else None
    if d1 and not d2:
        return d1, d1
    if d1 and d2:
        if d2 < d1:
            d1, d2 = d2, d1
        return d1, d2
    return None, None

def _available_range(df: pd.DataFrame) -> tuple[date | None, date | None]:
    fcol = _find_col(df, BASE_COLS["fecha"])
    if not fcol:
        return None, None
    s = _safe_to_datetime(df[fcol]).dropna()
    if s.empty:
        return None, None
    return s.min().date(), s.max().date()

def _filter_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df is None or df.empty:
        return df
    fcol = _find_col(df, BASE_COLS["fecha"])
    if not fcol:
        return df.iloc[0:0].copy()
    s = _safe_to_datetime(df[fcol])
    mask = (s.dt.date >= start) & (s.dt.date <= end)
    out = df.loc[mask].copy()
    out["_fecha"] = s[mask]
    return out

# =========================
# FILTRO DE MÁQUINAS (COM1–COM5)
# =========================
def _filter_to_allowed_machines(df: pd.DataFrame) -> pd.DataFrame:
    """
    Deja únicamente filas cuya columna Maquina (o equivalente) pertenezca a {COM1..COM5}.
    Normaliza removiendo espacios y caracteres no alfanuméricos (COM 1 -> COM1), y mayúsculas.
    """
    if df is None or df.empty:
        return df
    col_maq = _find_col(df, BASE_COLS["maq"])
    if not col_maq:
        # Si no hay columna máquina, no generamos informe (requisito explícito)
        return df.iloc[0:0].copy()

    def _norm_m(x: str) -> str:
        s = re.sub(r"[^A-Za-z0-9]+", "", str(x) or "")
        return s.upper().strip()

    dfx = df.copy()
    dfx["_maq_norm"] = dfx[col_maq].map(_norm_m)
    mask = dfx["_maq_norm"].isin(ALLOWED_MACHINES)
    out = dfx.loc[mask].drop(columns=["_maq_norm"])
    return out

# =========================
# AGREGADOS / KPIs
# =========================
def _kpis_globales(df: pd.DataFrame) -> dict:
    res = {
        "total_unidades": 0,
        "total_tc": 0.0,
        "total_tp": 0.0,
        "total_cs": 0.0,
        "productividad_total_pct": None,
        "velocidad_prom_mpm": None,
        "dias": 0,
        "operarios": 0,
        "maquinas": 0
    }
    if df is None or df.empty:
        return res

    col_u = _find_col(df, BASE_COLS["unidades"])
    col_tc = _find_col(df, BASE_COLS["tc"])
    col_tp = _find_col(df, BASE_COLS["tp"])
    col_cs = _find_col(df, BASE_COLS["cs"])
    col_name = _find_col(df, BASE_COLS["name"])
    col_maq = _find_col(df, BASE_COLS["maq"])
    col_fecha = _find_col(df, BASE_COLS["fecha"])

    if col_u:
        res["total_unidades"] = int(pd.to_numeric(df[col_u], errors="coerce").fillna(0).sum())
    if col_tc:
        res["total_tc"] = float(pd.to_numeric(df[col_tc], errors="coerce").fillna(0).sum())
    if col_tp:
        res["total_tp"] = float(pd.to_numeric(df[col_tp], errors="coerce").fillna(0).sum())
    if col_cs:
        res["total_cs"] = float(pd.to_numeric(df[col_cs], errors="coerce").fillna(0).sum())

    denom = res["total_tc"] + res["total_tp"]
    if denom > 0 and res["total_cs"] > 0:
        res["productividad_total_pct"] = float((res["total_cs"] / denom) * 100.0)

    if res["total_tc"] > 0 and res["total_unidades"] > 0:
        res["velocidad_prom_mpm"] = float(res["total_unidades"] / (res["total_tc"] * 60.0))

    if col_name:
        res["operarios"] = int(df[col_name].astype(str).str.strip().nunique())
    if col_maq:
        res["maquinas"] = int(df[col_maq].astype(str).str.strip().nunique())
    if "_fecha" in df.columns:
        res["dias"] = int(df["_fecha"].dt.date.nunique())
    elif col_fecha:
        res["dias"] = int(_safe_to_datetime(df[col_fecha]).dt.date.nunique())

    return res

def _agg_diario(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df.iloc[0:0].copy()
    col_u = _find_col(df, BASE_COLS["unidades"])
    col_tc = _find_col(df, BASE_COLS["tc"])
    col_tp = _find_col(df, BASE_COLS["tp"])
    col_cs = _find_col(df, BASE_COLS["cs"])
    if not col_u:
        return df.iloc[0:0].copy()

    if "_fecha" not in df.columns:
        fcol = _find_col(df, BASE_COLS["fecha"])
        if not fcol:
            return df.iloc[0:0].copy()
        df = df.copy()
        df["_fecha"] = _safe_to_datetime(df[fcol])

    gb = df.groupby(df["_fecha"].dt.date)
    agg = {col_u: "sum"}
    if col_tc: agg[col_tc] = "sum"
    if col_tp: agg[col_tp] = "sum"
    if col_cs: agg[col_cs] = "sum"
    g = gb.agg(agg).reset_index(names="date")
    g = g.rename(columns={col_u: "unidades"})
    g["day"] = pd.to_datetime(g["date"]).dt.day

    if col_tc and col_tp and col_cs:
        denom = pd.to_numeric(g[col_tc], errors="coerce").fillna(0) + pd.to_numeric(g[col_tp], errors="coerce").fillna(0)
        num = pd.to_numeric(g[col_cs], errors="coerce").fillna(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            g["prod_pct"] = np.where(denom > 0, (num / denom) * 100.0, np.nan)
    else:
        g["prod_pct"] = np.nan

    g["unidades"] = pd.to_numeric(g["unidades"], errors="coerce").fillna(0)
    return g.sort_values("date").reset_index(drop=True)

def _agg_por_operario(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df.iloc[0:0].copy()
    col_name = _find_col(df, BASE_COLS["name"])
    col_u = _find_col(df, BASE_COLS["unidades"])
    col_tc = _find_col(df, BASE_COLS["tc"])
    col_tp = _find_col(df, BASE_COLS["tp"])
    col_cs = _find_col(df, BASE_COLS["cs"])
    if not (col_name and col_u):
        return df.iloc[0:0].copy()

    agg = {col_u: "sum"}
    if col_tc: agg[col_tc] = "sum"
    if col_tp: agg[col_tp] = "sum"
    if col_cs: agg[col_cs] = "sum"
    g = df.groupby(col_name).agg(agg).reset_index()
    g = g.rename(columns={col_name: "operario", col_u: "unidades"})

    if col_tc and col_tp and col_cs:
        denom = pd.to_numeric(g[col_tc], errors="coerce").fillna(0) + pd.to_numeric(g[col_tp], errors="coerce").fillna(0)
        num = pd.to_numeric(g[col_cs], errors="coerce").fillna(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            g["prod_pct"] = np.where(denom > 0, (num / denom) * 100.0, np.nan)
    else:
        g["prod_pct"] = np.nan

    g["unidades"] = pd.to_numeric(g["unidades"], errors="coerce").fillna(0)
    return g.sort_values("unidades", ascending=False, kind="stable").reset_index(drop=True)

def _agg_por_maquina(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df.iloc[0:0].copy()
    col_maq = _find_col(df, BASE_COLS["maq"])
    col_u = _find_col(df, BASE_COLS["unidades"])
    col_tc = _find_col(df, BASE_COLS["tc"])
    col_tp = _find_col(df, BASE_COLS["tp"])
    col_cs = _find_col(df, BASE_COLS["cs"])
    if not (col_maq and col_u):
        return df.iloc[0:0].copy()

    agg = {col_u: "sum"}
    if col_tc: agg[col_tc] = "sum"
    if col_tp: agg[col_tp] = "sum"
    if col_cs: agg[col_cs] = "sum"
    g = df.groupby(col_maq).agg(agg).reset_index()
    g = g.rename(columns={col_maq: "maquina", col_u: "unidades"})

    if col_tc and col_tp and col_cs:
        denom = pd.to_numeric(g[col_tc], errors="coerce").fillna(0) + pd.to_numeric(g[col_tp], errors="coerce").fillna(0)
        num = pd.to_numeric(g[col_cs], errors="coerce").fillna(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            g["prod_pct"] = np.where(denom > 0, (num / denom) * 100.0, np.nan)
    else:
        g["prod_pct"] = np.nan

    g["unidades"] = pd.to_numeric(g["unidades"], errors="coerce").fillna(0)
    return g.sort_values("unidades", ascending=False, kind="stable").reset_index(drop=True)

def _pareto_causas(df: pd.DataFrame) -> pd.DataFrame:
    col_cause = _find_col(df, BASE_COLS["cause"])
    col_tp = _find_col(df, BASE_COLS["tp"])
    if not (col_cause and col_tp):
        return df.iloc[0:0].copy()
    g = df.groupby(col_cause)[col_tp].sum().reset_index()
    g = g.rename(columns={col_cause: "cause", col_tp: "horas"})
    g["horas"] = pd.to_numeric(g["horas"], errors="coerce").fillna(0.0)
    total = g["horas"].sum()
    if total <= 0:
        return g.iloc[0:0].copy()
    g["pct"] = (g["horas"] / total) * 100.0
    g = g.sort_values("horas", ascending=False, kind="stable").reset_index(drop=True)
    g["cum_pct"] = g["pct"].cumsum()
    cutoff_idx = (g["cum_pct"] <= 80.0).sum()
    if cutoff_idx == 0:
        cutoff_idx = 1
    if cutoff_idx < len(g) and g["cum_pct"].iloc[cutoff_idx - 1] < 80.0:
        cutoff_idx += 1
    return g.iloc[:cutoff_idx][["cause", "horas", "pct"]]

def _cambios_referencia_diario(df: pd.DataFrame):
    """Devuelve (metrics_dict, df_diario) para cambios de referencia (por Causa_Paro)."""
    metrics = {"count": 0, "avg_hours": "0"}
    col_cause = _find_col(df, BASE_COLS["cause"])
    col_tp = _find_col(df, BASE_COLS["tp"])
    if not (col_cause and col_tp):
        return metrics, df.iloc[0:0].copy()
    if "_fecha" not in df.columns:
        fcol = _find_col(df, BASE_COLS["fecha"])
        if not fcol:
            return metrics, df.iloc[0:0].copy()
        df = df.copy()
        df["_fecha"] = _safe_to_datetime(df[fcol])

    causes = df[col_cause].astype(str).str.strip().str.casefold()
    mask = causes.str.contains("cambio", na=False) & causes.str.contains("refer", na=False)
    dfx = df[mask].copy()

    count = int(len(dfx))
    total_tp = pd.to_numeric(dfx[col_tp], errors="coerce").fillna(0.0).sum()
    avg_h = (total_tp / count) if count > 0 else 0.0
    metrics["count"] = count
    metrics["avg_hours"] = _fmt_float(avg_h, 2)

    if count == 0:
        return metrics, df.iloc[0:0].copy()

    daily = dfx.groupby(dfx["_fecha"].dt.date).size().reset_index(name="count")
    daily = daily.rename(columns={daily.columns[0]: "date"})
    daily["day"] = pd.to_datetime(daily["date"]).dt.day
    return metrics, daily[["day", "count"]].sort_values("day").reset_index(drop=True)

def _waste_global_y_por_operario(df: pd.DataFrame, dfw: pd.DataFrame | None, start: date, end: date):
    """Retorna (waste_global_dict, waste_by_op_df)"""
    # Producción (kg) desde df filtrado
    col_name = _find_col(df, BASE_COLS["name"])
    col_kg = _find_col(df, BASE_COLS["kg_producidos"])

    prod_by_op = pd.DataFrame(columns=["operario", "kg_produced"])
    total_prod = 0.0
    if col_kg and col_name:
        dfx = df.copy()
        dfx["_kg"] = pd.to_numeric(dfx[col_kg], errors="coerce").fillna(0.0)
        prod_by_op = dfx.groupby(col_name)["_kg"].sum().reset_index()
        prod_by_op = prod_by_op.rename(columns={col_name: "operario", "_kg": "kg_produced"})
        total_prod = float(prod_by_op["kg_produced"].sum())

    # Desperdicio (kg) desde hoja WASTE
    waste_by_op = pd.DataFrame(columns=["operario", "kg_waste"])
    total_waste = 0.0
    if dfw is not None and not dfw.empty:
        w_name = _find_col(dfw, WASTE_COLS["name"])
        w_fecha = _find_col(dfw, WASTE_COLS["fecha"])
        w_kg = _find_col(dfw, WASTE_COLS["kg"])
        if w_name and w_fecha and w_kg:
            dfw2 = dfw.copy()
            s = _safe_to_datetime(dfw2[w_fecha])
            mask = (s.dt.date >= start) & (s.dt.date <= end)
            dfw2 = dfw2.loc[mask].copy()
            dfw2["_kg"] = pd.to_numeric(dfw2[w_kg], errors="coerce").fillna(0.0)
            g = dfw2.groupby(w_name)["_kg"].sum().reset_index()
            waste_by_op = g.rename(columns={w_name: "operario", "_kg": "kg_waste"})
            total_waste = float(waste_by_op["kg_waste"].sum())

    # Merge para % por operario (si existe producción por operario)
    if not prod_by_op.empty:
        waste_by_op = pd.merge(waste_by_op, prod_by_op, on="operario", how="outer")
    else:
        waste_by_op["kg_produced"] = 0.0

    waste_by_op["kg_waste"] = pd.to_numeric(waste_by_op["kg_waste"], errors="coerce").fillna(0.0)
    waste_by_op["kg_produced"] = pd.to_numeric(waste_by_op["kg_produced"], errors="coerce").fillna(0.0)
    denom = waste_by_op["kg_waste"] + waste_by_op["kg_produced"]
    with np.errstate(divide="ignore", invalid="ignore"):
        waste_by_op["pct_waste"] = np.where(denom > 0, (waste_by_op["kg_waste"] / denom) * 100.0, np.nan)
    waste_by_op = waste_by_op.sort_values("kg_waste", ascending=False, kind="stable").reset_index(drop=True)

    global_pct = None
    if (total_waste + total_prod) > 0:
        global_pct = float((total_waste / (total_waste + total_prod)) * 100.0)

    waste_global = {
        "kg_waste": total_waste,
        "kg_produced": total_prod,
        "pct_waste": global_pct
    }
    return waste_global, waste_by_op

# =========================
# PLOTEO
# =========================
def _plot_barras_linea(x, bars, line, title, xlabel, ylabel_left, ylabel_right, cmap_name, filename):
    try:
        n = len(bars)
        fig_w = max(8, min(18, 0.6 * n + 3))
        fig, ax = plt.subplots(figsize=(fig_w, 4.8), dpi=130)
        cmap = plt.get_cmap(cmap_name)
        colors = [cmap(0.55 + 0.4 * (i / max(1, n-1))) for i in range(n)]
        ax.bar(x, bars, width=0.75, color=colors, edgecolor="none")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel_left)
        ax.grid(axis="y", linestyle="--", alpha=0.5)

        ax2 = ax.twinx()
        if line is not None and len(line) == len(bars) and np.isfinite(np.array(line)).any():
            ax2.plot(x, line, marker="o", linewidth=2)
            ax2.set_ylabel(ylabel_right)
            valid = [p for p in line if pd.notna(p)]
            if valid:
                pmax = max(valid)
                ax2.set_ylim(0, max(100.0, pmax * 1.15))
        else:
            ax2.set_ylabel(ylabel_right)

        fig.tight_layout()
        path = os.path.join(REPORTS_DIR, filename)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        print(f"⚠️ plot barras_linea: {e}")
        return None

def _plot_pie(labels, values, title, filename):
    try:
        if not labels or not values or sum(values) <= 0:
            return None
        n = len(labels)
        fig_w = max(8, min(14, 0.45 * n + 7))
        fig, ax = plt.subplots(figsize=(fig_w, 5.2), dpi=130)
        wedges, texts, autotexts = ax.pie(
            values,
            labels=None,
            autopct=lambda p: f"{p:.1f}%",
            startangle=90,
            pctdistance=0.7
        )
        ax.set_title(title)
        ax.axis("equal")
        ax.legend(wedges, labels, title="Causas",
                  loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False)
        plt.subplots_adjust(right=0.78)
        fig.tight_layout()
        path = os.path.join(REPORTS_DIR, filename)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        print(f"⚠️ plot pie: {e}")
        return None

def _plot_line(x, y, title, xlabel, ylabel, filename):
    try:
        fig, ax = plt.subplots(figsize=(10, 4.2), dpi=130)
        ax.plot(x, y, marker="o", linewidth=2)
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(True, linestyle="--", alpha=0.5)
        fig.tight_layout()
        path = os.path.join(REPORTS_DIR, filename)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        print(f"⚠️ plot line: {e}")
        return None

def _plot_barras_simple(labels, values, title, xlabel, ylabel, cmap_name, filename, rotate=45):
    try:
        n = len(labels)
        fig_w = max(8, min(18, 0.6 * n + 3))
        fig, ax = plt.subplots(figsize=(fig_w, 4.8), dpi=130)
        cmap = plt.get_cmap(cmap_name)
        colors = [cmap(0.55 + 0.4 * (i / max(1, n-1))) for i in range(n)]
        x = np.arange(n)
        ax.bar(x, values, width=0.75, color=colors, edgecolor="none")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=rotate, ha="right")
        ax.grid(axis="y", linestyle="--", alpha=0.5)
        fig.tight_layout()
        path = os.path.join(REPORTS_DIR, filename)
        fig.savefig(path, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        print(f"⚠️ plot barras_simple: {e}")
        return None

# =========================
# PDF
# =========================
def _build_pdf_admin(
    rango_txt: str,
    kpis: dict,
    fig_paths: dict,
    por_op: pd.DataFrame,
    por_m: pd.DataFrame,
    pareto: pd.DataFrame,
    cambios_metrics: dict,
    waste_global: dict,
    waste_by_op: pd.DataFrame
) -> str:
    date_str = datetime.now().strftime("%Y-%m-%d")
    fname = f"Informe_gerencial_linea_{_slug(rango_txt)}_{date_str}.pdf"
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
    pdf.cell(page_w / 2, 8, "Área: Impresión RTR", align="R", ln=1)

    # Título
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(0, 70, 140)
    title = f"Informe Gerencial (Línea) — {rango_txt}"
    pdf.cell(0, 12, _sanitize_pdf_text(title), new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.ln(4)

    # KPIs
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 12)
    k1 = f"Unidades totales: {_fmt_int(kpis.get('total_unidades', 0))}"
    k2 = f"Productividad total: {_fmt_float(kpis.get('productividad_total_pct'), 1)}%"
    k3 = f"Velocidad promedio: {_fmt_float(kpis.get('velocidad_prom_mpm'), 2)} m/min"
    k4 = f"Horas (TC+TP): {_fmt_float((kpis.get('total_tc', 0.0) + kpis.get('total_tp', 0.0)), 2)} h"
    k5 = f"Días: {kpis.get('dias', 0)} | Operarios: {kpis.get('operarios', 0)} | Máquinas: {kpis.get('maquinas', 0)}"
    for line in (k1, k2, k3, k4, k5):
        pdf.cell(0, 8, _sanitize_pdf_text(line), ln=1)

    # Gráfico diario
    if fig_paths.get("diaria") and os.path.exists(fig_paths["diaria"]):
        pdf.ln(4)
        pdf.image(fig_paths["diaria"], w=page_w)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.ln(2)
        pdf.cell(0, 6, _sanitize_pdf_text("Barras: Unidades por día | Línea: % productividad (eje derecho)"), ln=1)

    # Velocidad diaria
    if fig_paths.get("vel_diaria") and os.path.exists(fig_paths["vel_diaria"]):
        pdf.ln(6)
        pdf.image(fig_paths["vel_diaria"], w=page_w)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(90, 90, 90)
        pdf.ln(2)
        pdf.cell(0, 6, _sanitize_pdf_text("Velocidad promedio diaria (m/min)"), ln=1)

    # Ranking por operario
    pdf.ln(8)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _sanitize_pdf_text("Resumen por Operario"), ln=1)
    if not por_op.empty:
        if fig_paths.get("op_rank") and os.path.exists(fig_paths["op_rank"]):
            pdf.image(fig_paths["op_rank"], w=page_w)
            pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        top = por_op.head(15)
        for _, r in top.iterrows():
            line = f"• {str(r.get('operario',''))[:30]} — Unidades: { _fmt_int(r.get('unidades',0)) }  | %Prod: { _fmt_float(r.get('prod_pct', np.nan), 1) }"
            pdf.cell(0, 6, _sanitize_pdf_text(line), ln=1)
    else:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(0, 6, _sanitize_pdf_text("No hay datos de operarios en el rango."), ln=1)

    # Ranking por máquina
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _sanitize_pdf_text("Resumen por Máquina"), ln=1)
    if not por_m.empty:
        if fig_paths.get("maq_rank") and os.path.exists(fig_paths["maq_rank"]):
            pdf.image(fig_paths["maq_rank"], w=page_w)
            pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        topm = por_m.head(15)
        for _, r in topm.iterrows():
            line = f"• {str(r.get('maquina',''))[:30]} — Unidades: { _fmt_int(r.get('unidades',0)) }  | %Prod: { _fmt_float(r.get('prod_pct', np.nan), 1) }"
            pdf.cell(0, 6, _sanitize_pdf_text(line), ln=1)
    else:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(0, 6, _sanitize_pdf_text("No hay datos de máquinas en el rango."), ln=1)

    # Pareto de causas
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _sanitize_pdf_text("Causas de Paro (Pareto)"), ln=1)
    if fig_paths.get("pareto") and os.path.exists(fig_paths["pareto"]):
        pdf.image(fig_paths["pareto"], w=page_w*0.92)
    if not pareto.empty:
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        for _, r in pareto.head(10).iterrows():
            line = f"• {str(r.get('cause',''))[:40]} — Horas: {_fmt_float(r.get('horas',0.0),2)}  ({_fmt_float(r.get('pct',0.0),1)}%)"
            pdf.cell(0, 6, _sanitize_pdf_text(line), ln=1)
    else:
        pdf.set_font("Helvetica", "I", 11)
        pdf.cell(0, 6, _sanitize_pdf_text("No hay datos suficientes para Pareto."), ln=1)

    # Cambios de referencia
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _sanitize_pdf_text("Cambios de referencia"), ln=1)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 7, _sanitize_pdf_text(f"# cambios: {cambios_metrics.get('count',0)}"), ln=1)
    pdf.cell(0, 7, _sanitize_pdf_text(f"Promedio por cambio (horas): {cambios_metrics.get('avg_hours','0')}"), ln=1)
    if fig_paths.get("cambios") and os.path.exists(fig_paths["cambios"]):
        pdf.ln(2)
        pdf.image(fig_paths["cambios"], w=page_w)

    # Desperdicio
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _sanitize_pdf_text("Desperdicio"), ln=1)
    pdf.set_font("Helvetica", "", 12)
    kgw = waste_global.get("kg_waste", 0.0)
    kgp = waste_global.get("kg_produced", 0.0)
    pctw = waste_global.get("pct_waste", None)
    pdf.cell(0, 7, _sanitize_pdf_text(f"Kg desperdicio total: {_fmt_float(kgw, 2)} kg"), ln=1)
    pdf.cell(0, 7, _sanitize_pdf_text(f"Kg producidos: {_fmt_float(kgp, 2)} kg"), ln=1)
    if isinstance(pctw, (int, float)) and not (np.isnan(pctw) or np.isinf(pctw)):
        pdf.set_font("Helvetica", "B", 12)
        if float(pctw) > 3.0:
            pdf.set_text_color(200, 0, 0)
        else:
            pdf.set_text_color(0, 128, 0)
        pdf.cell(0, 8, _sanitize_pdf_text(f"% desperdicio global: {_fmt_float(pctw,2)}%"), ln=1)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 12)
    if fig_paths.get("waste_ops") and os.path.exists(fig_paths["waste_ops"]):
        pdf.ln(2)
        pdf.image(fig_paths["waste_ops"], w=page_w)
    if not waste_by_op.empty:
        pdf.ln(3)
        pdf.set_font("Helvetica", "", 11)
        for _, r in waste_by_op.head(12).iterrows():
            line = (f"• {str(r.get('operario',''))[:30]} — "
                    f"Desperdicio: {_fmt_float(r.get('kg_waste',0.0),2)} kg | "
                    f"Prod: {_fmt_float(r.get('kg_produced',0.0),2)} kg | "
                    f"%: {_fmt_float(r.get('pct_waste', np.nan),2)}%")
            pdf.cell(0, 6, _sanitize_pdf_text(line), ln=1)

    # Firma
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 10)
    pdf.multi_cell(0, 6, _sanitize_pdf_text("Generado automáticamente por CiplasBot — Área Impresión RTR."), align="L")

    pdf.output(out_path)
    return out_path

# =========================
# Helpers de estado (espera de rango)
# =========================
def _all_keys(phone_raw: str) -> list[str]:
    """Devuelve variantes de clave para sesión (dígitos y E164)."""
    p = _only_digits(phone_raw)
    e = _only_digits(canon_phone_e164_co(phone_raw) or phone_raw)
    keys = []
    if p: keys.append(p)
    if e and e != p: keys.append(e)
    return keys or [p or e]

def _set_waiting(phone_raw: str, start_av: date, end_av: date):
    payload = {
        "start_av": start_av.isoformat(),
        "end_av": end_av.isoformat(),
        "ts": datetime.now().isoformat(timespec="seconds"),
    }
    for k in _all_keys(phone_raw):
        mem = sessions.setdefault(k, {})
        mem["admin_line_report_waiting"] = payload
        sessions[k] = mem
    print(f"🧭 [admin_line] waiting set for { _all_keys(phone_raw) }: {payload}")

def _clear_waiting(phone_raw: str):
    for k in _all_keys(phone_raw):
        mem = sessions.setdefault(k, {})
        if mem.pop("admin_line_report_waiting", None) is not None:
            print(f"🧭 [admin_line] waiting cleared for {k}")
        sessions[k] = mem

def _get_waiting(phone_raw: str):
    for k in _all_keys(phone_raw):
        w = sessions.get(k, {}).get("admin_line_report_waiting")
        if w:
            return w
    return None

# =========================
# Ejecución del informe para un rango concreto
# =========================
def _run_report_for_range(to_norm: str, start: date, end: date) -> bool:
    print(f"🧭 [admin_line] run report for {to_norm} → {start} a {end}")
    df = _download_main_df()
    if df is None or df.empty:
        send_whatsapp_message(to_norm, "❌ No pude leer la hoja principal para generar el informe.")
        return True

    dfr = _filter_range(df, start, end)
    # 🔎 Filtrar solo COM1..COM5
    dfr = _filter_to_allowed_machines(dfr)

    if dfr is None or dfr.empty:
        send_whatsapp_message(
            to_norm,
            f"ℹ️ No hay registros entre {start.isoformat()} y {end.isoformat()} para máquinas COM1–COM5."
        )
        return True

    # cálculos
    kpis = _kpis_globales(dfr)
    diario = _agg_diario(dfr)
    por_op = _agg_por_operario(dfr)
    por_m = _agg_por_maquina(dfr)
    pareto = _pareto_causas(dfr)
    cambios_metrics, camb_diario = _cambios_referencia_diario(dfr)

    # figuras
    fig_paths = {}

    if not diario.empty:
        fig_paths["diaria"] = _plot_barras_linea(
            diario["day"].tolist(),
            diario["unidades"].tolist(),
            diario["prod_pct"].tolist(),
            "Unidades por día y %Productividad", "Día", "Unidades", "% Productividad",
            "Blues", f"fig_diaria_{_slug(start.isoformat())}_{_slug(end.isoformat())}.png"
        )
        if "tc" in diario.columns and pd.to_numeric(diario["tc"], errors="coerce").fillna(0).sum() > 0:
            vel = (pd.to_numeric(diario["unidades"], errors="coerce").fillna(0) /
                   (pd.to_numeric(diario["tc"], errors="coerce").fillna(0) * 60.0)).replace([np.inf, -np.inf], np.nan)
            fig_paths["vel_diaria"] = _plot_line(diario["day"].tolist(), vel.tolist(),
                                                 "Velocidad promedio diaria", "Día", "m/min",
                                                 f"fig_vel_{_slug(start.isoformat())}_{_slug(end.isoformat())}.png")

    if not por_op.empty:
        fig_paths["op_rank"] = _plot_barras_simple(
            por_op["operario"].astype(str).tolist(),
            pd.to_numeric(por_op["unidades"], errors="coerce").fillna(0).tolist(),
            "Unidades por Operario", "Operario", "Unidades", "Greens",
            f"fig_op_{_slug(start.isoformat())}_{_slug(end.isoformat())}.png"
        )

    if not por_m.empty:
        fig_paths["maq_rank"] = _plot_barras_simple(
            por_m["maquina"].astype(str).tolist(),
            pd.to_numeric(por_m["unidades"], errors="coerce").fillna(0).tolist(),
            "Unidades por Máquina", "Máquina", "Unidades", "Purples",
            f"fig_maq_{_slug(start.isoformat())}_{_slug(end.isoformat())}.png"
        )

    if not pareto.empty:
        fig_paths["pareto"] = _plot_pie(
            pareto["cause"].astype(str).tolist(),
            pd.to_numeric(pareto["horas"], errors="coerce").fillna(0).tolist(),
            "Pareto de Causas (% horas de paro)",
            f"fig_pareto_{_slug(start.isoformat())}_{_slug(end.isoformat())}.png"
        )

    if not camb_diario.empty:
        fig_paths["cambios"] = _plot_barras_simple(
            camb_diario["day"].astype(str).tolist(),
            pd.to_numeric(camb_diario["count"], errors="coerce").fillna(0).tolist(),
            "Cambios de referencia por día", "Día", "Conteo", "Oranges",
            f"fig_cambios_{_slug(start.isoformat())}_{_slug(end.isoformat())}.png",
            rotate=0
        )

    # desperdicio
    dfw = _download_waste_df()
    waste_global, waste_by_op = _waste_global_y_por_operario(dfr, dfw, start, end)
    if not waste_by_op.empty:
        fig_paths["waste_ops"] = _plot_barras_simple(
            waste_by_op["operario"].astype(str).tolist(),
            pd.to_numeric(waste_by_op["kg_waste"], errors="coerce").fillna(0).tolist(),
            "Kg desperdicio por Operario", "Operario", "Kg", "Reds",
            f"fig_waste_{_slug(start.isoformat())}_{_slug(end.isoformat())}.png"
        )

    rango_txt = start.isoformat() if start == end else f"{start.isoformat()} a {end.isoformat()}"
    pdf_path = _build_pdf_admin(
        rango_txt, kpis, fig_paths, por_op, por_m, pareto, cambios_metrics, waste_global, waste_by_op
    )

    caption = f"📄 Informe gerencial (Línea) — {rango_txt}"
    sent_ok = False
    try:
        send_whatsapp_document(to_norm, pdf_path, caption=caption)
        sent_ok = True
    except Exception as e:
        send_whatsapp_message(to_norm, f"❌ Error enviando el informe: {e}")
    finally:
        # limpiar figuras siempre
        for p in (fig_paths or {}).values():
            if p:
                _safe_unlink(p)
        # eliminar el PDF solo si fue enviado correctamente
        if sent_ok:
            _safe_unlink(pdf_path)

    return True

# =========================
# HANDLERS
# =========================
def handle_admin_line_report_request(inbound_phone_raw: str, original_text: str) -> bool:
    """
    Maneja:
      - "informe de desempeño"
      - "informe de desempeño YYYY-MM-DD"
      - "informe de desempeño YYYY-MM-DD a YYYY-MM-DD"
      - Acepta DD/MM/YYYY
    """
    msg_norm = _fold(original_text)
    if not COMMAND_RE.search(msg_norm):
        return False

    # 🔒 Solo admin (573176380061) y supervisor de área (573168308454)
    if not _is_allowed_requester(inbound_phone_raw):
        # No lo consumimos: permite que el handler del INFORME PERSONAL (operario) procese este mismo comando.
        return False

    to_norm = _only_digits(canon_phone_e164_co(inbound_phone_raw) or inbound_phone_raw)

    # Descargar datos para saber rango disponible
    df = _download_main_df()
    if df is None or df.empty:
        send_whatsapp_message(to_norm, "❌ No pude leer la hoja principal para generar el informe.")
        return True

    start_av, end_av = _available_range(df)
    if not start_av or not end_av:
        send_whatsapp_message(to_norm, "ℹ️ La hoja no tiene fechas válidas para construir el informe.")
        return True

    # ¿El mensaje trae fecha/rango?
    start, end = _parse_date_text(original_text)
    if not start and not end:
        rango_txt = f"{start_av.isoformat()} a {end_av.isoformat()}"
        send_whatsapp_message(
            to_norm,
            "📅 Rango disponible: *%s*.\n\n"
            "👉 Responde con una fecha `YYYY-MM-DD` o un rango `YYYY-MM-DD a YYYY-MM-DD` para generar el informe.\n"
            "Ejemplos:\n"
            "• informe de desempeño %s\n"
            "• informe de desempeño %s a %s" % (rango_txt, end_av.isoformat(), start_av.isoformat(), end_av.isoformat())
        )
        _set_waiting(inbound_phone_raw, start_av, end_av)
        return True

    # clamp al rango disponible
    if start < start_av: start = start_av
    if end > end_av: end = end_av

    _clear_waiting(inbound_phone_raw)
    return _run_report_for_range(to_norm, start, end)

def maybe_handle_admin_line_report_followup(inbound_phone_raw: str, original_text: str) -> bool:
    """
    Si el usuario quedó en 'espera de rango' y envía solo una fecha/rango,
    genera el informe sin exigir el prefijo 'informe de desempeño'.
    """
    # Solo continuará si previamente _set_waiting se estableció para este teléfono (lo cual
    # ocurre exclusivamente cuando es un solicitante permitido).
    to_norm = _only_digits(canon_phone_e164_co(inbound_phone_raw) or inbound_phone_raw)
    waiting = _get_waiting(inbound_phone_raw)
    if not waiting:
        return False  # no estamos esperando rango para este número

    # ¿Hay fecha en el texto?
    start, end = _parse_date_text(original_text)
    if not start and not end:
        print("🧭 [admin_line] follow-up: sin fecha detectable, se ignora")
        return False

    # clamp al rango ofrecido
    try:
        start_av = datetime.strptime(waiting["start_av"], "%Y-%m-%d").date()
        end_av = datetime.strptime(waiting["end_av"], "%Y-%m-%d").date()
    except Exception:
        start_av = start
        end_av = end
    if start < start_av: start = start_av
    if end > end_av: end = end_av

    print(f"🧭 [admin_line] follow-up capturado para {to_norm} → {start} a {end}")
    _clear_waiting(inbound_phone_raw)
    return _run_report_for_range(to_norm, start, end)
