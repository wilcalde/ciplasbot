# workflows/costura_report.py
import os
import re
import sqlite3
from datetime import datetime, date

import numpy as np
import pandas as pd

from fpdf import FPDF

from services.session_memory import CONFIG_DIR

REPORTS_DIR = os.path.join(CONFIG_DIR, "fileteado_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

COSTURA_DATA_URL = (
    "https://docs.google.com/spreadsheets/d/1V-9iIVMLf19vuQIoiu53t6k2J2vlu49vUjEMnKS5bLY/export?format=xlsx"
)
OPERARIOS_DB_FILENAME = "unified_database_botheven.db"
OPERARIOS_DB_PATHS = (
    os.path.join(CONFIG_DIR, "task", OPERARIOS_DB_FILENAME),
    os.path.join(CONFIG_DIR, "tasks", OPERARIOS_DB_FILENAME),
)

COLS = {
    "fecha": ["Fecha", "Fecha_Efectiva", "Fecha_Registro", "fecha", "fecha_efectiva"],
    "maquina": ["Maquina", "Máquina", "maquina", "máquina", "Equipo", "equipo"],
    "cantidad": ["Cantidad_Completada", "cantidad_completada", "Cantidad", "cantidad"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_corrida", "tpo_cda"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrida_estandar"],
    "operario": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre_Operario"],
}


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


def _normalize_machine_name(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    match = re.match(r"^[Aa]\s*(\d+)\s*$", raw)
    if match:
        return f"A{match.group(1)}"
    return raw.upper()


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
    corrstand_candidates = ["corrstand", "corrida_standar", "corrida_estandar", "corrida_standar", "corrida_standard"]
    tc_candidates = ["tiempo_corrida", "tpo_corrida", "tc", "tiempo_produccion", "tiempo_corrido"]
    tp_candidates = ["tiempo_perdido", "tpo_perdido", "tp", "tiempo_paro", "tiempo_inactivo"]

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
        c_cs = next((norm_map.get(_normalize(c)) for c in corrstand_candidates if _normalize(c) in norm_map), None)
        c_tc = next((norm_map.get(_normalize(c)) for c in tc_candidates if _normalize(c) in norm_map), None)
        c_tp = next((norm_map.get(_normalize(c)) for c in tp_candidates if _normalize(c) in norm_map), None)

        has_base = c_operario is not None and c_fecha is not None
        has_eff = c_eff is not None
        has_formula = c_cs is not None and c_tc is not None and c_tp is not None
        if not has_base or not (has_eff or has_formula):
            continue
        score = 0
        score += 2 if has_eff else 0
        score += 1 if has_formula else 0
        score += 1 if c_cs is not None else 0
        score += 1 if c_tc is not None else 0
        score += 1 if c_tp is not None else 0
        if score > best_score:
            best_score = score
            best = {
                "table": table,
                "operario": c_operario,
                "fecha": c_fecha,
                "eficiencia": c_eff,
                "corrstand": c_cs,
                "tc": c_tc,
                "tp": c_tp,
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


def _resolve_operarios_db_path() -> str | None:
    for path in OPERARIOS_DB_PATHS:
        if os.path.exists(path):
            return path
    return None


def _read_efficiency_history_from_sqlite(end_date: date, months: int = 4) -> pd.DataFrame:
    db_path = _resolve_operarios_db_path()
    if not db_path:
        return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])
    start_date, end_date_window, _ = _month_window_bounds(end_date, months=months)

    conn = _connect_sqlite(db_path)
    try:
        source = _detect_efficiency_source(conn)
        if source:
            table = source["table"]
            c_operario = source["operario"]
            c_fecha = source["fecha"]
            c_eff = source["eficiencia"]
            c_cs = source["corrstand"]
            c_tc = source["tc"]
            c_tp = source["tp"]
            cols = [c_operario, c_fecha]
            if c_eff:
                cols.append(c_eff)
            if c_cs and c_tc and c_tp:
                cols.extend([c_cs, c_tc, c_tp])
            query = f"SELECT {', '.join(_quote_identifier(c) for c in cols)} FROM {_quote_identifier(table)}"
            df = pd.read_sql_query(query, conn)
            if df.empty:
                return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

            df[c_fecha] = pd.to_datetime(df[c_fecha], errors="coerce")
            df = df[(df[c_fecha].dt.date >= start_date) & (df[c_fecha].dt.date <= end_date_window)]
            if df.empty:
                return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

            if c_eff and c_eff in df.columns:
                df["eficiencia_mes"] = pd.to_numeric(df[c_eff], errors="coerce")
            else:
                tc_vals = pd.to_numeric(df[c_tc], errors="coerce").fillna(0)
                tp_vals = pd.to_numeric(df[c_tp], errors="coerce").fillna(0)
                cs_vals = pd.to_numeric(df[c_cs], errors="coerce").fillna(0)
                denom = tc_vals + tp_vals
                df["eficiencia_mes"] = np.where(denom > 0, (cs_vals / denom) * 100.0, np.nan)
            df["Mes"] = df[c_fecha].dt.to_period("M").astype(str)
            df["Operario"] = df[c_operario].astype(str).str.strip()
            df = df[["Operario", "Mes", "eficiencia_mes"]]
            return df.dropna(subset=["Operario", "Mes"])

        wide = _detect_efficiency_wide_table(conn)
        if wide:
            table = wide["table"]
            c_operario = wide["operario"]
            eff_cols = wide["eff_cols"]
            cols = [c_operario] + eff_cols
            query = f"SELECT {', '.join(_quote_identifier(c) for c in cols)} FROM {_quote_identifier(table)}"
            df = pd.read_sql_query(query, conn)
            if df.empty:
                return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])
            df = df.melt(id_vars=[c_operario], value_vars=eff_cols, var_name="Mes", value_name="eficiencia_mes")
            df = df.rename(columns={c_operario: "Operario"})
            df["Operario"] = df["Operario"].astype(str).str.strip()
            df["Mes"] = df["Mes"].astype(str)
            return df.dropna(subset=["Operario", "Mes"])
    finally:
        conn.close()

    return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

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
    repl = {"•": "-", "–": "-", "—": "-", "―": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "\u00A0": " "}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")


def _download_costura_df() -> pd.DataFrame:
    return pd.read_excel(COSTURA_DATA_URL)


def _filter_costura_records(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    c_maq = _find_col(df, COLS["maquina"])
    if not c_maq:
        return pd.DataFrame()
    return df.loc[_starts_with_ci(df[c_maq], "A")].copy()


def _prepare_maquina_table(df_prod: pd.DataFrame) -> pd.DataFrame:
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

    df_group = df_prod.copy()
    df_group["Maquina_norm"] = df_group[c_maq].astype(str).apply(_normalize_machine_name)

    df_maq = df_group.groupby("Maquina_norm").agg({
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


def _trend_labeler(end_date: date, current_eff: dict[str, float], months: int = 4):
    try:
        df_hist = _read_efficiency_history_from_sqlite(end_date, months=months)
    except Exception:
        df_hist = pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

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
        for period in period_keys[:-1]:
            match = sub[sub["Mes"] == period]["eficiencia_mes"]
            if match.empty:
                return "Datos insuficientes (<4)"
            valores.append(float(match.mean()))
        current_value = current_eff.get(op_key)
        if current_value is None:
            return "Datos insuficientes (<4)"
        valores.append(float(current_value))
        if len(valores) < months:
            return "Datos insuficientes (<4)"
        primeros_prom = sum(valores[:2]) / 2.0
        ultimos_prom = sum(valores[-2:]) / 2.0
        diferencia = ultimos_prom - primeros_prom
        efic_mes = valores[-1]
        promedio_4 = sum(valores) / len(valores)
        if diferencia > 0.5:
            return (
                f"Mejora (+{diferencia:.1f} pp, Efic Mes {efic_mes:.1f}%, "
                f"Prom 4m {promedio_4:.1f}%)"
            )
        if diferencia < -0.5:
            return (
                f"Decreciente ({diferencia:.1f} pp, Efic Mes {efic_mes:.1f}%, "
                f"Prom 4m {promedio_4:.1f}%)"
            )
        return (
            f"Neutro ({diferencia:+.1f} pp, Efic Mes {efic_mes:.1f}%, "
            f"Prom 4m {promedio_4:.1f}%)"
        )

    return _label


def _prepare_operario_table(df_prod: pd.DataFrame, end_date: date) -> pd.DataFrame:
    if df_prod is None or df_prod.empty:
        return pd.DataFrame(columns=[
            "Nombre", "Produccion", "Tiempo_corrida", "T.perdido", "Cor.estandar", "%eficiencia_mes", "tendencia_label"
        ])
    c_op = _find_col(df_prod, COLS["operario"])
    c_qty = _find_col(df_prod, COLS["cantidad"])
    c_tc = _find_col(df_prod, COLS["tc"])
    c_tp = _find_col(df_prod, COLS["tp"])
    c_cs = _find_col(df_prod, COLS["cs"])
    if not (c_op and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=[
            "Nombre", "Produccion", "Tiempo_corrida", "T.perdido", "Cor.estandar", "%eficiencia_mes", "tendencia_label"
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
    eff_map = dict(
        zip(
            df_ope["Nombre"].astype(str).str.strip().str.casefold(),
            df_ope["%eficiencia_mes"].astype(float),
        )
    )
    labeler = _trend_labeler(end_date, eff_map, months=4)
    df_ope["tendencia_label"] = df_ope["Nombre"].apply(labeler)
    return df_ope


def _render_productivity_note(pdf: FPDF, eficiencia_total: float, prod_total: float) -> None:
    diff_causas = eficiencia_total - prod_total
    restante = 80.0 - eficiencia_total
    pdf.ln(4)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 10)
    pdf.set_text_color(0, 100, 0)
    pdf.cell(0, 6, _sanitize_pdf_text("Objetivo de productividad 80%."), 0, 1, "L")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "B", 9)
    pdf.set_text_color(150, 0, 0)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0,
        5,
        _sanitize_pdf_text(
            "Las causas de tiempo perdido corresponden a "
            f"{diff_causas:.1f} puntos de productividad."
        ),
    )
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Arial", "B", 9)
    pdf.set_text_color(0, 0, 0)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(
        0,
        5,
        _sanitize_pdf_text(
            f"El restante {restante:.1f} es por bajo eficiencia del proceso."
        ),
    )
    pdf.set_text_color(0, 0, 0)


def _render_global_data(pdf: FPDF, df_maq: pd.DataFrame) -> tuple[float, float]:
    pdf.ln(4)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 12)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(0, 8, " 3. Datos globales (segun rango de fecha)", 0, 1, "L", True)
    pdf.ln(2)

    if df_maq is None or df_maq.empty:
        pdf.set_font("Arial", "", 9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, _sanitize_pdf_text("Sin datos globales disponibles."))
        return 0.0, 0.0

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
        ("Produccion", _fmt_int(totals["produccion"])),
        ("T.corrida", _fmt_float(totals["t_corrida"])),
        ("Tiempo Perdido", _fmt_float(totals["T.perd"])),
        ("Corrida Estandar", _fmt_float(totals["cor.estandar"])),
        ("%productivida_total", f"{prod_total:.2f}%"),
        ("%eficiencia_total", f"{eficiencia_total:.2f}%"),
    ]
    for label, value in rows:
        pdf.set_x(pdf.l_margin)
        pdf.cell(55, 6, _sanitize_pdf_text(label), 1, 0, "L")
        pdf.cell(0, 6, _sanitize_pdf_text(str(value)), 1, 1, "L")
    return prod_total, eficiencia_total


def _render_signature(pdf: FPDF):
    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 8, _sanitize_pdf_text("Informe Generado por Agente IA CiplasBot"),
             ln=1, align="C")


class ReporteCostura(FPDF):
    def __init__(self, start: date, end: date, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._start_date = start
        self._end_date = end

    def header(self):
        self.set_font("Arial", "B", 16)
        self.cell(0, 10, "Analisis proceso Costura", 0, 1, "C")
        rango_txt = (
            f"Rango del informe {self._start_date.strftime('%d/%m/%Y')} "
            f"a {self._end_date.strftime('%d/%m/%Y')}"
        )
        self.set_font("Arial", "", 10)
        self.cell(0, 6, rango_txt, 0, 1, "C")
        self.set_font("Arial", "", 10)
        fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.cell(0, 10, f"Fecha y hora de generacion: {fecha_hora}", 0, 1, "R")
        self.ln(5)

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
        self.ln(10)

    def tabla_operarios(self, df: pd.DataFrame) -> None:
        self.set_font("Arial", "B", 12)
        self.set_fill_color(235, 241, 222)
        self.cell(0, 10, " Seguimiento Eficiencia operarios", 0, 1, "L", True)
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

            trend_text = _sanitize_pdf_text(row["tendencia_label"])
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


def build_pdf_costura(df_range_filtered: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    df_filt = _filter_costura_records(df_range_filtered)
    df_maq = _prepare_maquina_table(df_filt)
    df_ope = _prepare_operario_table(df_filt, end)

    pdf = ReporteCostura(start, end, format="Letter")
    pdf.add_page()
    if df_maq.empty:
        pdf.set_font("Arial", "B", 11)
        pdf.cell(0, 8, "Sin datos para Costura en el rango seleccionado.", 0, 1, "L")
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
            pdf.set_font("Arial", "I", 8)
            pdf.set_text_color(0, 0, 0)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                0,
                5,
                _sanitize_pdf_text(
                    "Nota: La tendencia compara el promedio de eficiencia de los primeros 2 meses "
                    "vs los últimos 2 meses (ventana de 4 meses), calculando (ultimos - anteriores). "
                    "Se incluye el %Efic Mes del último mes."
                ),
            )
        _render_global_data(pdf, df_maq)
        _render_signature(pdf)

    fname = f"Analisis_Proceso_Costura_{start.isoformat()}_{end.isoformat()}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)
    pdf.output(out_path)
    return out_path, []


__all__ = [
    "COLS",
    "_download_costura_df",
    "_find_col",
    "build_pdf_costura",
    "handle_costura_message",
]


def handle_costura_message(_phone_key: str, _text: str) -> bool:
    """
    Handler placeholder for costura messages.
    Actualmente los informes de costura se gestionan via `informe eficiencia`.
    """
    return False
