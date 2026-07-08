# workflows/fileteado_planas.py
import os
import re
import sqlite3
import matplotlib.pyplot as plt
from datetime import date, datetime

import numpy as np
import pandas as pd
from fpdf import FPDF

from services.session_memory import CONFIG_DIR

REPORTS_DIR = os.path.join(CONFIG_DIR, "fileteado_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

OPERARIOS_DB_FILENAME = "base_conversion_eficiencias_conversion.db"
OPERARIOS_DB_PATHS = (
    os.path.join(CONFIG_DIR, "task", OPERARIOS_DB_FILENAME),
    os.path.join(CONFIG_DIR, "tasks", OPERARIOS_DB_FILENAME),
)

COLS = {
    "articulo": ["Numero_Articulo", "numero_articulo", "Articulo", "articulo", "Numero de articulo"],
    "maquina": ["Maquina", "Máquina", "maquina", "máquina", "Equipo", "equipo"],
    "cantidad": ["Cantidad_Completada", "cantidad_completada", "Cantidad", "cantidad"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_corrida", "tpo_cda"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrida_estandar"],
    "operario": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre_Operario"],
    "causa": ["Causa_Paro", "Causa", "Motivo", "Causa de Paro"]
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

def _starts_with_ci(series: pd.Series, prefix: str) -> pd.Series:
    return series.astype(str).str.upper().str.startswith(prefix.upper())

def _sanitize_pdf_text(s: str) -> str:
    if not s: return ""
    repl = {"•": "-", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "\u00A0": " "}
    for k, v in repl.items(): s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

# --- Gestión de Datos Históricos ---

def _read_efficiency_history_from_sqlite(end_date: date, months: int = 4) -> pd.DataFrame:
    db_path = next((p for p in OPERARIOS_DB_PATHS if os.path.exists(p)), None)
    if not db_path: return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [r[0] for r in cur.fetchall()]
        if not tables: return pd.DataFrame()
        table = tables[0]
        cur = conn.execute(f"PRAGMA table_info(\"{table}\");")
        cols = [r[1] for r in cur.fetchall()]
        norm_map = {_normalize(c): c for c in cols}
        c_nombre, c_area = norm_map.get("nombre"), norm_map.get("area")
        if not (c_nombre and c_area): return pd.DataFrame()
        
        eff_cols = [c for c in cols if "eficiencia" in _normalize(c)]
        select_parts = [f'"{c_nombre}" as Operario', f'"{c_area}" as area'] + [f'"{c}"' for c in eff_cols]
        cols_sql = ", ".join(select_parts)
        
        df = pd.read_sql_query(f"SELECT {cols_sql} FROM \"{table}\" WHERE area = 'fileteado'", conn)
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

# --- Tablas de Procesamiento ---

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

# --- Generación de Gráfica de Pareto ---

def _generate_pareto_tp_planas(df: pd.DataFrame, start_str: str) -> str:
    c_tp = _find_col(df, COLS["tp"])
    c_causa = _find_col(df, COLS["causa"])
    
    if not c_tp or not c_causa: return ""

    df_tp = df.groupby(c_causa)[c_tp].sum().reset_index()
    df_tp = df_tp[df_tp[c_tp] > 0].sort_values(by=c_tp, ascending=False)
    
    if df_tp.empty: return ""

    total_tp = df_tp[c_tp].sum()
    df_tp['cum_pct'] = df_tp[c_tp].cumsum() / total_tp * 100
    
    cutoff = df_tp[df_tp['cum_pct'] <= 80].index
    if len(cutoff) < len(df_tp):
        plot_df = df_tp.iloc[:len(cutoff)+1]
    else:
        plot_df = df_tp

    plt.figure(figsize=(10, 6))
    bars = plt.barh(plot_df[c_causa], plot_df[c_tp], color='#92c5de', edgecolor='#0571b0')
    plt.xlabel('Horas Perdidas')
    plt.title(f'Causas del 80% del Tiempo Perdido - Planas\nTotal Horas: {total_tp:.2f}h')
    plt.gca().invert_yaxis()
    plt.grid(axis='x', linestyle='--', alpha=0.7)

    for bar in bars:
        plt.text(bar.get_width(), bar.get_y() + bar.get_height()/2, 
                 f' {bar.get_width():.2f}h', va='center', fontsize=9)

    plt.tight_layout()
    chart_path = os.path.join(REPORTS_DIR, f"pareto_tp_planas_{start_str}.png")
    plt.savefig(chart_path, dpi=120)
    plt.close()
    return chart_path

# --- Clase Reporte PDF ---

class ReportePlanas(FPDF):
    def __init__(self, start: date, end: date):
        super().__init__(format="Letter")
        self._start, self._end = start, end

    def header(self):
        self.set_font("Arial", "B", 14)
        self.cell(0, 10, "Analisis proceso Planas", 0, 1, "C")
        self.set_font("Arial", "", 9)
        self.cell(0, 5, f"Periodo: {self._start.strftime('%d/%m/%Y')} a {self._end.strftime('%d/%m/%Y')}", 0, 1, "C")
        self.ln(5)

    def set_cell_color(self, value):
        if value < 80: self.set_text_color(200, 0, 0)
        else: self.set_text_color(0, 120, 0)

    def draw_table_maquinas(self, df):
        self.set_font("Arial", "B", 10)
        self.cell(0, 8, " 1. Resumen de Produccion por Maquina", 0, 1, "L")
        self.set_font("Arial", "B", 8)
        cols, w = ["Maquina", "Produccion", "T.corr", "T.perd", "%prod", "%efic"], [30, 30, 30, 30, 35, 35]
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln()
        self.set_font("Arial", "", 8)
        for _, r in df.iterrows():
            self.cell(w[0], 6, str(r["Maquina"]), 1)
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
        self.cell(0, 6, "Conclusion del Proceso:", 0, 1)
        self.set_font("Arial", "", 9)
        ratio_perdida = 1 - (prod_total / efic_total) if efic_total > 0 else 0
        unidades_perdidas = totals["produccion"] * ratio_perdida
        gap_objetivo = 80.0 - prod_total
        msg = (f"El proceso estuvo por debajo del objetivo de productividad (80%) en {gap_objetivo:.2f}% "
               f"y los tiempos perdidos generaron una perdida de {unidades_perdidas:,.0f} unidades.")
        self.multi_cell(0, 5, _sanitize_pdf_text(msg))

    def draw_pareto_section(self, img_path):
        if not img_path or not os.path.exists(img_path): return
        self.add_page()
        self.set_font("Arial", "B", 12)
        self.cell(0, 10, " 4. Analisis de Tiempos Perdidos (Pareto 80/20)", 0, 1, "L")
        self.image(img_path, x=10, y=30, w=190)
        self.ln(110)
        self.set_font("Arial", "I", 8)
        self.multi_cell(0, 5, "Nota: El grafico identifica las causas que concentran el 80% del tiempo improductivo en las maquinas Planas. La reduccion de estos factores especificos tendra el mayor impacto directo en la productividad de la linea.")
        self.ln(5); self.set_font("Arial", "I", 9)
        self.cell(0, 10, "Informe generado por CiplasBot", 0, 1, "C")

# --- Construcción Final ---

def build_pdf_planas(df_range_filtered: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    c_maq = _find_col(df_range_filtered, COLS["maquina"])
    if not c_maq: return "", []
    
    df_filt = df_range_filtered.loc[_starts_with_ci(df_range_filtered[c_maq], "FIPLA")].copy()
    
    if df_filt.empty: return "", []

    df_maq = _prepare_maquina_table(df_filt)
    df_ope = _prepare_operario_table(df_filt, start, end)

    # Generar Pareto para Planas
    chart_path = _generate_pareto_tp_planas(df_filt, start.isoformat())

    sum_cs, sum_tc, sum_tp = df_maq["cor.estandar"].sum(), df_maq["t_corrida"].sum(), df_maq["T.perd"].sum()
    totals = {
        "produccion": df_maq["produccion"].sum(),
        "t_perdido": sum_tp,
        "prod_total": (sum_cs / (sum_tc + sum_tp) * 100) if (sum_tc + sum_tp) > 0 else 0,
        "efic_total": (sum_cs / sum_tc * 100) if sum_tc > 0 else 0
    }

    pdf = ReportePlanas(start, end)
    pdf.add_page()
    if not df_maq.empty: pdf.draw_table_maquinas(df_maq)
    if not df_ope.empty: pdf.draw_table_ops(df_ope)
    pdf.draw_final_summary(totals)

    # Insertar sección de Pareto al final del reporte
    if chart_path:
        pdf.draw_pareto_section(chart_path)

    fname = f"Analisis_Proceso_PLANAS_{start.isoformat()}_{end.isoformat()}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)
    pdf.output(out_path)

    # Limpieza de imagen temporal
    if chart_path and os.path.exists(chart_path):
        try: os.remove(chart_path)
        except: pass

    return out_path, []

def handle_planas_message(_pk, _txt): return False

__all__ = ["COLS", "build_pdf_planas", "handle_planas_message"]