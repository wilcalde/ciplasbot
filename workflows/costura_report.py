# workflows/costura_report.py
import os
import re
import sqlite3
import matplotlib.pyplot as plt
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

def _sanitize_pdf_text(s: str) -> str:
    if not s: return ""
    repl = {"•": "-", "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'", "\u00A0": " "}
    for k, v in repl.items(): s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

# --- Datos y Análisis ---

def _download_costura_df() -> pd.DataFrame:
    """Descarga los datos crudos desde Google Sheets."""
    return pd.read_excel(COSTURA_DATA_URL)

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
        query = f"SELECT {', '.join(select_list)} FROM \"{table}\" WHERE area IN ('botheven', 'costura')"
        
        df = pd.read_sql_query(query, conn)
        df["Operario"] = df["Operario"].astype(str).str.strip().str.casefold()
        return df.melt(id_vars=["Operario", "area"], var_name="Mes", value_name="eficiencia_mes")
    except Exception: return pd.DataFrame()
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
                v = pd.to_numeric(match.iloc[0], errors='coerce')
                if not np.isnan(v):
                    valores.append(v * 100.0 if v <= 1.5 else v)
        val_act = current_eff.get(op_key)
        if val_act is not None: valores.append(float(val_act))
        if len(valores) < 2: return "Datos insuficientes"
        diff = valores[-1] - valores[0]
        prom = sum(valores)/len(valores)
        ult_txt = ",".join([f"{v:.1f}" for v in valores[-3:]])
        base = f"ult({ult_txt})_prom={prom:.1f}"
        return f"{base}_ Mejora" if diff > 0.5 else f"{base}_ Decreciente" if diff < -0.5 else f"{base}_ Neutro"
    return _label

def _prepare_maquina_table(df: pd.DataFrame) -> pd.DataFrame:
    c_maq, c_qty, c_tc, c_tp, c_cs = [_find_col(df, COLS[k]) for k in ["maquina", "cantidad", "tc", "tp", "cs"]]
    
    df_temp = df.copy()
    df_temp[c_maq] = df_temp[c_maq].astype(str).str.strip().str.upper()
    
    res = df_temp.groupby(c_maq).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    res.columns = ["Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar"]
    res["%prod"] = (res["cor.estandar"] / (res["t_corrida"] + res["T.perd"])) * 100
    res["%eficiencia"] = (res["cor.estandar"] / res["t_corrida"]) * 100
    
    res = res.sort_values(by="%prod", ascending=False)
    
    return res.fillna(0)

def _prepare_operario_table(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    c_op, c_tc, c_tp, c_cs, c_qty = [_find_col(df, COLS[k]) for k in ["operario", "tc", "tp", "cs", "cantidad"]]
    res = df.groupby(c_op).agg({c_tc: "sum", c_tp: "sum", c_cs: "sum", c_qty: "sum"}).reset_index()
    res.columns = ["Nombre", "T.corrida", "T.perdido", "Cor.est", "Produccion"]
    res["%_prod"] = (res["Cor.est"] / (res["T.corrida"] + res["T.perdido"])) * 100
    res["%efic"] = (res["Cor.est"] / res["T.corrida"]) * 100
    
    # ORDENAR: 1. Por %_prod DESC | 2. Por %efic DESC (Desempate)
    res = res.sort_values(by=["%_prod", "%efic"], ascending=[False, False]).reset_index(drop=True)
    
    # AÑADIR RANKING
    res.insert(0, "Ranking", res.index + 1)
    res = res.fillna(0)

    if start != end:
        nombres_limpios = res["Nombre"].str.strip().str.casefold()
        eff_map = dict(zip(nombres_limpios, res["%efic"]))
        res["tendencia_label"] = res["Nombre"].apply(lambda n: _trend_labeler(end, eff_map)(n))
    else: 
        res["tendencia_label"] = ""
    return res

# --- Lógica Pareto 80/20 ---

def _generate_pareto_tp(df: pd.DataFrame, start: date) -> str:
    c_tp = _find_col(df, COLS["tp"])
    c_causa = _find_col(df, COLS["causa"])
    if not c_tp or not c_causa: return ""

    df_tp = df.groupby(c_causa)[c_tp].sum().reset_index()
    df_tp = df_tp[df_tp[c_tp] > 0].sort_values(by=c_tp, ascending=False)
    if df_tp.empty: return ""

    total = df_tp[c_tp].sum()
    df_tp['cum_pct'] = df_tp[c_tp].cumsum() / total * 100
    cutoff = df_tp[df_tp['cum_pct'] <= 85].index
    plot_df = df_tp.iloc[:len(cutoff)+1]

    plt.figure(figsize=(10, 6))
    bars = plt.barh(plot_df[c_causa], plot_df[c_tp], color='#ff9999', edgecolor='black')
    plt.xlabel('Horas'); plt.title(f'Analisis Pareto Causas T. Perdido - Costura\nTotal: {total:.1f}h')
    plt.gca().invert_yaxis(); plt.grid(axis='x', linestyle='--', alpha=0.7)
    for bar in bars: plt.text(bar.get_width(), bar.get_y() + bar.get_height()/2, f' {bar.get_width():.1f}h', va='center')
    plt.tight_layout()
    path = os.path.join(REPORTS_DIR, f"pareto_costura_{start}.png")
    plt.savefig(path, dpi=110); plt.close()
    return path

# --- Clase Reporte PDF ---

class ReporteCostura(FPDF):
    def __init__(self, start: date, end: date):
        super().__init__(format="Letter")
        self._start, self._end = start, end

    def header(self):
        self.set_font("Arial", "B", 14); self.cell(0, 10, "Analisis proceso Costura", 0, 1, "C")
        self.set_font("Arial", "", 9); self.cell(0, 5, f"Periodo: {self._start} a {self._end}", 0, 1, "C")
        self.ln(5)

    def set_cell_color(self, value):
        if value < 80: self.set_text_color(200, 0, 0)
        else: self.set_text_color(0, 120, 0)

    def draw_table_maquinas(self, df):
        self.set_font("Arial", "B", 10); self.cell(0, 8, " 1. Resumen de Produccion por Maquina", 0, 1, "L")
        cols, w = ["Maquina", "Produccion", "T.corr", "T.perd", "%prod", "%efic"], [25, 30, 25, 25, 35, 35]
        self.set_font("Arial", "B", 8)
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln(); self.set_font("Arial", "", 8)
        for _, r in df.iterrows():
            self.cell(w[0], 6, str(r["Maquina"]), 1)
            self.cell(w[1], 6, f"{r['produccion']:,.0f}", 1, 0, "R")
            self.cell(w[2], 6, f"{r['t_corrida']:.1f}", 1, 0, "R")
            self.cell(w[3], 6, f"{r['T.perd']:.1f}", 1, 0, "R")
            self.set_cell_color(r["%prod"]); self.cell(w[4], 6, f"{r['%prod']:.1f}%", 1, 0, "R")
            self.set_cell_color(r["%eficiencia"]); self.cell(w[5], 6, f"{r['%eficiencia']:.1f}%", 1, 1, "R")
            self.set_text_color(0,0,0)

    def draw_table_ops(self, df):
        self.ln(5); self.set_font("Arial", "B", 10); self.cell(0, 8, " 2. Seguimiento Eficiencia operarios", 0, 1)
        is_range = self._start != self._end
        
        # Ajuste en los anchos de columna para dar más espacio al Nombre (46 en vez de 42)
        cols = ["#", "Nombre", "Prod", "T.corr", "T.perd", "%_prod", "%efic", "Tendencia"] if is_range else ["#", "Nombre", "Produccion", "T.corrida", "T.perdido", "%_prod", "% efic"]
        w = [8, 46, 16, 13, 13, 15, 15, 64] if is_range else [10, 55, 25, 25, 25, 25, 25]
        
        self.set_font("Arial", "B", 8)
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln(); self.set_font("Arial", "", 7)
        for _, r in df.iterrows():
            # 1. Columna Ranking
            self.cell(w[0], 6, str(r["Ranking"]), 1, 0, "C")
            
            # Limpieza y ajuste de nombre para evitar superposición
            max_len = 28 if is_range else 38
            nombre_str = str(r["Nombre"])[:max_len]
            nombre_pdf = nombre_str.encode('latin-1', 'replace').decode('latin-1')
            
            # --- LÓGICA DE COLOR PARA EL NOMBRE ---
            if r["%_prod"] >= 80:
                self.set_text_color(0, 120, 0)   # Verde
            elif r["%_prod"] >= 75:
                self.set_text_color(0, 0, 0)     # Negro (Rango 75 a 79.9)
            else:
                self.set_text_color(200, 0, 0)   # Rojo (Menor a 75)
                
            # 2. Imprimir Nombre con color y restaurar a negro
            self.cell(w[1], 6, nombre_pdf, 1)
            self.set_text_color(0, 0, 0)
            # --- FIN LÓGICA DE COLOR ---
            
            # 3. Resto de las columnas
            self.cell(w[2], 6, f"{r['Produccion']:,.0f}", 1, 0, "R")
            self.cell(w[3], 6, f"{r['T.corrida']:.1f}", 1, 0, "R")
            self.cell(w[4], 6, f"{r['T.perdido']:.1f}", 1, 0, "R")
            
            # Los porcentajes conservan su propia lógica de color independiente
            self.set_cell_color(r["%_prod"]); self.cell(w[5], 6, f"{r['%_prod']:.1f}%", 1, 0, "R")
            self.set_cell_color(r["%efic"]); self.cell(w[6], 6, f"{r['%efic']:.1f}%", 1, 0, "R")
            self.set_text_color(0,0,0)
            
            if is_range: self.cell(w[7], 6, _sanitize_pdf_text(r["tendencia_label"]), 1)
            self.ln()

    def draw_final_summary(self, totals):
        self.ln(5); self.set_font("Arial", "B", 11); self.cell(0, 8, " 3. Resumen Global", 0, 1)
        rows = [("Total Produccion", f"{totals['produccion']:,.0f}"), ("% Productividad", f"{totals['prod_total']:.2f}%"), ("Total Horas Perdidas", f"{totals['t_perdido']:.2f} h")]
        self.set_font("Arial", "", 9)
        for l, v in rows: self.cell(50, 6, l, 1); self.cell(50, 6, v, 1, 1, "R")

    def draw_pareto_page(self, path):
        if not path or not os.path.exists(path): return
        self.add_page(); self.set_font("Arial", "B", 12); self.cell(0, 10, " 4. Analisis Pareto Tiempos Perdidos", 0, 1)
        self.image(path, x=10, y=30, w=190); self.ln(120)
        self.set_font("Arial", "I", 9); self.multi_cell(0, 5, "Nota: El grafico muestra las causas criticas que acumulan el 80% del tiempo improductivo.")

# --- Función Principal ---

def build_pdf_costura(df_range: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    c_maq = _find_col(df_range, COLS["maquina"])
    if not c_maq: return "", []
    df_f = df_range[df_range[c_maq].astype(str).str.upper().str.startswith("A")].copy()
    
    df_m_t = _prepare_maquina_table(df_f)
    df_o_t = _prepare_operario_table(df_f, start, end)
    chart = _generate_pareto_tp(df_f, start)

    sum_cs, sum_tc, sum_tp = df_m_t["cor.estandar"].sum(), df_m_t["t_corrida"].sum(), df_m_t["T.perd"].sum()
    totals = {"produccion": df_m_t["produccion"].sum(), "t_perdido": sum_tp, "prod_total": (sum_cs / (sum_tc + sum_tp) * 100) if (sum_tc + sum_tp) > 0 else 0, "efic_total": (sum_cs / sum_tc * 100) if sum_tc > 0 else 0}

    pdf = ReporteCostura(start, end); pdf.add_page()
    pdf.draw_table_maquinas(df_m_t); pdf.draw_table_ops(df_o_t); pdf.draw_final_summary(totals); pdf.draw_pareto_page(chart)

    out = os.path.join(REPORTS_DIR, f"Reporte_Costura_{start}_{end}.pdf")
    pdf.output(out)
    if chart and os.path.exists(chart): os.remove(chart)
    return out, []

def handle_costura_message(_pk, _txt): return False

__all__ = ["COLS", "_download_costura_df", "build_pdf_costura", "handle_costura_message"]