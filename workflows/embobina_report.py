# workflows/embobina_report.py
import os
import re
import sqlite3
from datetime import datetime, date

import numpy as np
import pandas as pd
from fpdf import FPDF

from services.session_memory import CONFIG_DIR

# --- Configuración de Rutas y URL ---
REPORTS_DIR = os.path.join(CONFIG_DIR, "cuerdas_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# URL compartida para el área de Cuerdas
EMBOBINA_DATA_URL = (
    "https://docs.google.com/spreadsheets/d/17cV1hJyZPsoaowZLGJuyhmKtoeWdDTrdWLUjPpDQInQ/export?format=xlsx"
)

OPERARIOS_DB_FILENAME = "base_conversion_eficiencias_conversion.db"
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
    "centro": ["Centro_Trabajo", "centro_trabajo", "Centro"]
}

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

# --- Utilidades ---

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")

def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty: return None
    for c in candidates:
        if c in df.columns: return c
    norm_map = {_normalize(c): c for c in df.columns}
    for c in candidates:
        key = _normalize(c)
        if key in norm_map: return norm_map[key]
    return None

def _sanitize_pdf_text(s: str) -> str:
    if not s: return ""
    repl = {"•": "-", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "\u00A0": " "}
    for k, v in repl.items(): s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

# --- Lógica de Datos ---

def _download_embobina_df() -> pd.DataFrame:
    return pd.read_excel(EMBOBINA_DATA_URL)

def _read_efficiency_history_from_sqlite(end_date: date, months: int = 4) -> pd.DataFrame:
    db_path = next((p for p in OPERARIOS_DB_PATHS if os.path.exists(p)), None)
    if not db_path: return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if not tables: return pd.DataFrame()
        table = tables[0]
        cur = conn.execute(f"PRAGMA table_info(\"{table}\");")
        cols = [r[1] for r in cur.fetchall()]
        norm_map = {_normalize(c): c for c in cols}
        c_nombre, c_area = norm_map.get("nombre"), norm_map.get("area")
        if not (c_nombre and c_area): return pd.DataFrame()
        
        eff_cols = [c for c in cols if "eficiencia" in _normalize(c)]
        select_list = [f'"{c_nombre}" as Operario', f'"{c_area}" as area'] + [f'"{c}"' for c in eff_cols]
        query = f"SELECT {', '.join(select_list)} FROM \"{table}\""
        
        df = pd.read_sql_query(query, conn)
        df["Operario"] = df["Operario"].astype(str).str.strip().str.casefold()
        return df.melt(id_vars=["Operario", "area"], var_name="Mes", value_name="eficiencia_mes")
    finally: conn.close()

def _trend_labeler(end_date: date, current_eff: dict[str, float], months: int = 4):
    df_hist = _read_efficiency_history_from_sqlite(end_date, months)
    if df_hist.empty: return lambda _: "Sin historial"
    end_p = pd.Period(end_date, freq="M")
    periods = [end_p - i for i in range(months-1, -1, -1)]
    period_keys = [_normalize(f"eficiencia_{MESES_ES[p.month]}_{p.year}") for p in periods]
    
    def _label(nombre: str) -> str:
        op_key = (nombre or "").strip().casefold()
        sub = df_hist[df_hist["Operario"] == op_key]
        if sub.empty: return "Sin historial"
        valores = []
        for pk in period_keys[:-1]:
            match = sub[sub["Mes"].apply(_normalize) == pk]["eficiencia_mes"]
            if not match.empty:
                v = pd.to_numeric(match, errors='coerce').mean()
                valores.append(v * 100.0 if v <= 1.5 else v)
        val_act = current_eff.get(op_key)
        if val_act is not None: valores.append(float(val_act))
        if len(valores) < months: return "Datos insuficientes (<4)"
        diff = (sum(valores[-2:])/2.0) - (sum(valores[:2])/2.0)
        prom = sum(valores)/len(valores)
        ult_txt = ",".join([f"{v:.1f}" for v in valores[-3:]])
        base = f"ult_3m({ult_txt})_prom={prom:.1f}"
        if diff > 0.5: return f"{base}_ Mejora"
        if diff < -0.5: return f"{base}_ Decreciente"
        return f"{base}_ Neutro"
    return _label

# --- Procesamiento de Datos ---

def _prepare_maquina_table(df: pd.DataFrame) -> pd.DataFrame:
    c_maq, c_qty, c_tc, c_tp, c_cs = [_find_col(df, COLS[k]) for k in ["maquina", "cantidad", "tc", "tp", "cs"]]
    res = df.groupby(c_maq).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    res.columns = ["Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar"]
    res["%prod"] = (res["cor.estandar"] / (res["t_corrida"] + res["T.perd"])) * 100
    res["%eficiencia"] = (res["cor.estandar"] / res["t_corrida"]) * 100
    return res.fillna(0)

def _prepare_operario_table(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    c_op, c_tc, c_tp, c_cs, c_qty = [_find_col(df, COLS[k]) for k in ["operario", "tc", "tp", "cs", "cantidad"]]
    res = df.groupby(c_op).agg({c_tc: "sum", c_tp: "sum", c_cs: "sum", c_qty: "sum"}).reset_index()
    res.columns = ["Nombre", "T.corrida", "T.perdido", "Cor.est", "Produccion"]
    res["%_prod"] = (res["Cor.est"] / (res["T.corrida"] + res["T.perdido"])) * 100
    res["%efic"] = (res["Cor.est"] / res["T.corrida"]) * 100
    res = res.sort_values(by="%efic", ascending=False).fillna(0)

    if start != end:
        eff_map = dict(zip(res["Nombre"].str.strip().str.casefold(), res["%efic"]))
        res["tendencia_label"] = res["Nombre"].apply(_trend_labeler(end, eff_map))
    else: res["tendencia_label"] = ""
    return res

# --- Clase de Reporte PDF ---

class ReporteEmbobina(FPDF):
    def __init__(self, start: date, end: date):
        super().__init__(format="Letter")
        self._start, self._end = start, end

    def header(self):
        self.set_font("Arial", "B", 14)
        self.cell(0, 10, "Analisis proceso Embobinado", 0, 1, "C")
        self.set_font("Arial", "", 9)
        self.cell(0, 5, f"Periodo: {self._start.strftime('%d/%m/%Y')} a {self._end.strftime('%d/%m/%Y')}", 0, 1, "C")
        self.ln(5)

    def set_cell_color(self, value):
        if value < 80: self.set_text_color(200, 0, 0)
        else: self.set_text_color(0, 120, 0)

    def draw_table_maquinas(self, df):
        self.set_font("Arial", "B", 10)
        self.cell(0, 8, " 1. Resumen de Produccion por Maquina (Embobina)", 0, 1, "L")
        self.set_font("Arial", "B", 8)
        cols, w = ["Maquina", "Produccion", "T.corr", "T.perd", "%prod", "%efic"], [25, 30, 25, 25, 35, 35]
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln()
        self.set_font("Arial", "", 8)
        for _, r in df.iterrows():
            self.cell(w[0], 6, str(r["Maquina"])[:15], 1)
            self.cell(w[1], 6, f"{r['produccion']:,.0f}", 1, 0, "R")
            self.cell(w[2], 6, f"{r['t_corrida']:.1f}", 1, 0, "R")
            self.cell(w[3], 6, f"{r['T.perd']:.1f}", 1, 0, "R")
            self.set_cell_color(r["%prod"])
            self.cell(w[4], 6, f"{r['%prod']:.1f}%", 1, 0, "R")
            self.set_cell_color(r["%eficiencia"])
            self.cell(w[5], 6, f"{r['%eficiencia']:.1f}%", 1, 0, "R")
            self.set_text_color(0, 0, 0); self.ln()
        self.ln(5)

    def draw_table_ops(self, df):
        is_range = self._start != self._end
        self.set_font("Arial", "B", 10)
        self.cell(0, 8, " 2. Seguimiento Eficiencia operarios", 0, 1, "L")
        if is_range:
            cols, w, f_size = ["Nombre", "Prod", "T.corr", "T.perd", "%_prod", "%efic", "Tendencia"], [45, 18, 14, 14, 18, 18, 63], 6.5
        else:
            cols, w, f_size = ["Nombre", "Produccion", "T.corrida", "T.perdido", "%_prod", "% efic periodo"], [65, 25, 25, 25, 25, 25], 7.5
        self.set_font("Arial", "B", 8)
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln()
        for _, r in df.iterrows():
            self.set_font("Arial", "", f_size)
            self.cell(w[0], 6, str(r["Nombre"])[:35] if is_range else str(r["Nombre"])[:45], 1)
            self.set_font("Arial", "", 7.5)
            self.cell(w[1], 6, f"{r['Produccion']:,.0f}", 1, 0, "R")
            self.cell(w[2], 6, f"{r['T.corrida']:.1f}", 1, 0, "R")
            self.cell(w[3], 6, f"{r['T.perdido']:.1f}", 1, 0, "R")
            self.set_cell_color(r["%_prod"])
            self.cell(w[4], 6, f"{r['%_prod']:.1f}%", 1, 0, "R")
            self.set_cell_color(r["%efic"])
            self.cell(w[5], 6, f"{r['%efic']:.1f}%", 1, 0, "R")
            self.set_text_color(0, 0, 0)
            if is_range:
                self.set_font("Arial", "", 6)
                self.cell(w[6], 6, _sanitize_pdf_text(r["tendencia_label"]), 1)
            self.ln()
        self.ln(5)

    def draw_final_summary(self, totals):
        self.set_font("Arial", "B", 11)
        self.cell(0, 8, " 3. Resumen Global y Conclusiones", 0, 1, "L")
        self.set_font("Arial", "", 9)
        prod_total, efic_total = totals["prod_total"], totals["efic_total"]
        rows = [
            ("Total Produccion", f"{totals['produccion']:,.0f}"),
            ("% Productividad Total", f"{prod_total:.2f}%"),
            ("% Eficiencia Total", f"{efic_total:.2f}%"),
            ("Total Horas Perdidas", f"{totals['t_perdido']:.2f} h")
        ]
        for label, val in rows:
            self.cell(50, 6, label, 1)
            if "%" in val: self.set_cell_color(float(val.replace("%","")))
            self.cell(50, 6, val, 1, 1, "R"); self.set_text_color(0, 0, 0)
        
        self.ln(4); self.set_font("Arial", "B", 10)
        self.cell(0, 6, "Conclusion del Proceso Embobina:", 0, 1)
        self.set_font("Arial", "", 9)
        
        ratio_perdida = 1 - (prod_total / efic_total) if efic_total > 0 else 0
        unidades_perdidas = totals["produccion"] * ratio_perdida
        gap_obj = 80.0 - prod_total
        msg = (f"El proceso de embobinado se encuentra en {prod_total:.2f}% de productividad. "
               f"Se estima una perdida de {unidades_perdidas:,.0f} unidades por paros y tiempos muertos.")
        self.multi_cell(0, 5, _sanitize_pdf_text(msg))
        self.ln(5); self.set_font("Arial", "I", 9)
        self.cell(0, 10, "Informe generado por CiplasBot - Area Cuerdas", 0, 1, "C")

# --- Función Principal ---

def build_pdf_embobina(df_range: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    c_centro = _find_col(df_range, COLS["centro"])
    if not c_centro: return "", []
    
    # FILTRO ESPECIFICO: EMBOBINA
    df_filt = df_range[df_range[c_centro].astype(str).str.strip() == "EMBOBINA"].copy()
    
    if df_filt.empty: return "", []

    df_maq = _prepare_maquina_table(df_filt)
    df_ope = _prepare_operario_table(df_filt, start, end)

    sum_cs, sum_tc, sum_tp = df_maq["cor.estandar"].sum(), df_maq["t_corrida"].sum(), df_maq["T.perd"].sum()
    totals = {
        "produccion": df_maq["produccion"].sum(),
        "t_perdido": sum_tp,
        "prod_total": (sum_cs / (sum_tc + sum_tp) * 100) if (sum_tc + sum_tp) > 0 else 0,
        "efic_total": (sum_cs / sum_tc * 100) if sum_tc > 0 else 0
    }

    pdf = ReporteEmbobina(start, end)
    pdf.add_page()
    if not df_maq.empty: pdf.draw_table_maquinas(df_maq)
    if not df_ope.empty: pdf.draw_table_ops(df_ope)
    pdf.draw_final_summary(totals)

    out_path = os.path.join(REPORTS_DIR, f"Reporte_Embobina_{start}_{end}.pdf")
    pdf.output(out_path)
    return out_path, []

def handle_embobina_message(_pk, _txt): return False

__all__ = ["_download_embobina_df", "build_pdf_embobina", "handle_embobina_message"]