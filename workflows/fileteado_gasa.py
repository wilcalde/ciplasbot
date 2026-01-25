# workflows/fileteado_gasa.py
import os
import re
import sqlite3
from datetime import date, datetime

import numpy as np
import pandas as pd
from fpdf import FPDF

from services.session_memory import CONFIG_DIR

REPORTS_DIR = os.path.join(CONFIG_DIR, "fileteado_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)
OPERARIOS_DB_PATH = os.path.join(CONFIG_DIR, "task", "unified_database_gasa.db")
OPERARIOS_DB_FALLBACK = os.path.join(CONFIG_DIR, "tasks", "unified_database_gasa.db")

COLS = {
    "articulo": ["Numero_Articulo", "numero_articulo", "Articulo", "articulo", "Numero de articulo"],
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
    conn = _connect_sqlite(db_path)
    try:
        source = _detect_efficiency_source(conn)
        if source:
            cols = [source["operario"], source["fecha"], source["eficiencia"]]
            cols_sql = ", ".join(_quote_identifier(c) for c in cols if c)
            df = pd.read_sql_query(f"SELECT {cols_sql} FROM {_quote_identifier(source['table'])}", conn)
        else:
            wide = _detect_efficiency_wide_table(conn)
            if not wide:
                return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])
            cols_sql = ", ".join(_quote_identifier(c) for c in [wide["operario"], *wide["eff_cols"]])
            df = pd.read_sql_query(f"SELECT {cols_sql} FROM {_quote_identifier(wide['table'])}", conn)
    finally:
        conn.close()

    if source is None:
        df = df.rename(columns={wide["operario"]: "Operario"})
        df["Operario"] = df["Operario"].astype(str).str.strip()
        long_rows = []
        for col in wide["eff_cols"]:
            period = _parse_month_label(col)
            if period is None:
                continue
            series = pd.to_numeric(df[col], errors="coerce")
            for operario, value in zip(df["Operario"], series):
                if pd.isna(value):
                    continue
                long_rows.append({
                    "Operario": operario,
                    "Mes": period,
                    "eficiencia_mes": float(value) * 100.0 if value <= 1.5 else float(value),
                })
        if not long_rows:
            return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])
        df_long = pd.DataFrame(long_rows)
        _, _, periods = _month_window_bounds(end_date, months=months)
        df_long = df_long[df_long["Mes"].isin(periods)]
        df_long["Mes"] = df_long["Mes"].astype(str)
        return df_long

    rename_map = {
        source["operario"]: "Operario",
        source["fecha"]: "Fecha",
        source["eficiencia"]: "Eficiencia",
    }
    df = df.rename(columns=rename_map)
    df["Operario"] = df["Operario"].astype(str).str.strip()
    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df = df.dropna(subset=["Operario", "Fecha", "Eficiencia"])
    start_date, end_date, periods = _month_window_bounds(end_date, months=months)
    df = df[(df["Fecha"].dt.date >= start_date) & (df["Fecha"].dt.date <= end_date)]
    if df.empty:
        return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])

    df["Eficiencia"] = pd.to_numeric(df["Eficiencia"], errors="coerce")
    df = df.dropna(subset=["Eficiencia"])
    if df.empty:
        return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])
    max_eff = df["Eficiencia"].max()
    if max_eff <= 1.5:
        df["Eficiencia"] = df["Eficiencia"] * 100.0
    df["Mes"] = df["Fecha"].dt.to_period("M")
    df = df[df["Mes"].isin(periods)]
    if df.empty:
        return pd.DataFrame(columns=["Operario", "Mes", "eficiencia_mes"])
    grouped = df.groupby(["Operario", "Mes"], as_index=False)["Eficiencia"].mean()
    grouped = grouped.rename(columns={"Eficiencia": "eficiencia_mes"})
    grouped["Mes"] = grouped["Mes"].astype(str)
    grouped["eficiencia_mes"] = pd.to_numeric(grouped["eficiencia_mes"], errors="coerce")
    grouped = grouped.dropna(subset=["eficiencia_mes"])
    return grouped


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


class ReporteGasa(FPDF):
    def __init__(self, start: date, end: date, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._start_date = start
        self._end_date = end

    def header(self):
        self.set_font("Arial", "B", 16)
        self.cell(0, 10, "Analisis proceso Gasa", 0, 1, "C")
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


def _filter_gasa_records(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    c_art = _find_col(df, COLS["articulo"])
    c_maq = _find_col(df, COLS["maquina"])
    if not (c_art and c_maq):
        return pd.DataFrame()
    mask = _starts_with_ci(df[c_art], "CAG") & _starts_with_ci(df[c_maq], "FILET")
    return df.loc[mask].copy()


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


def _prepare_operario_table(df_prod: pd.DataFrame, end_date: date) -> pd.DataFrame:
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
    restante = 85.0 - eficiencia_total
    pdf.ln(4)
    pdf.set_x(pdf.l_margin)
    pdf.set_font("Arial", "B", 10)
    pdf.set_text_color(0, 100, 0)
    pdf.cell(0, 6, "Objetivo de productividad 85%.", 0, 1, "L")
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


def build_pdf_gasa(df_range_filtered: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    df_filt = _filter_gasa_records(df_range_filtered)
    df_maq = _prepare_maquina_table(df_filt)
    df_ope = _prepare_operario_table(df_filt, end)

    pdf = ReporteGasa(start, end, format="Letter")
    pdf.add_page()
    if df_maq.empty:
        pdf.set_font("Arial", "B", 11)
        pdf.cell(0, 8, "Sin datos para Gasa en el rango seleccionado.", 0, 1, "L")
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

    pdf.ln(6)
    pdf.set_font("Helvetica", "I", 9)
    pdf.cell(0, 8, "Informe Generado por Agente IA CiplasBot", 0, 1, "C")

    fname = f"Analisis_Proceso_Gasa_{start.isoformat()}_{end.isoformat()}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)
    pdf.output(out_path)
    return out_path, []
