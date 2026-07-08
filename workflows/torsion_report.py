# workflows/torsion_report.py
import os
import re
import sqlite3
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime, date

import numpy as np
import pandas as pd
from fpdf import FPDF

from services.session_memory import CONFIG_DIR

# --- Configuración de Rutas y URL ---
REPORTS_DIR = os.path.join(CONFIG_DIR, "cuerdas_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

TORSION_DATA_URL = (
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
    "centro": ["Centro_Trabajo", "centro_trabajo", "Centro"],
    "articulo": ["Descripcion_Articulo", "Desc_Articulo", "Articulo"],
    "kg": ["Cant_Kg", "Peso_Kg", "Kilogramos"]
}

MESES_ES = {
    1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
    7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
}

# --- Utilidades ---

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")

def _normalize_machine_torsion(value: str) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("rob"):
        suffix = raw[3:].strip()
        return f"ROB {suffix}" if suffix else "ROB"
    return raw.upper()

def _extract_denier(text: str) -> float:
    match = re.search(r"RAF\.(\d+)", str(text or ""), re.IGNORECASE)
    return float(match.group(1)) if match else 0.0

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

# --- Lógica de Historial y Tendencia ---

def _read_efficiency_history_from_sqlite(end_date: date, months: int = 4) -> pd.DataFrame:
    db_path = next((p for p in OPERARIOS_DB_PATHS if os.path.exists(p)), None)
    if not db_path: return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
        if not tables: return pd.DataFrame()
        table = tables[0]
        df = pd.read_sql_query(f"SELECT * FROM \"{table}\"", conn)
        df.columns = [_normalize(c) for c in df.columns]
        c_nombre = next((c for c in df.columns if "nombre" in c or "operario" in c), df.columns[0])
        df[c_nombre] = df[c_nombre].astype(str).str.strip().str.casefold()
        return df, c_nombre
    except Exception: return pd.DataFrame(), ""
    finally: conn.close()

def _trend_labeler(end_date: date, current_eff_map: dict, months: int = 4):
    df_hist, c_nombre = _read_efficiency_history_from_sqlite(end_date, months)
    if df_hist.empty: return lambda _: "Sin historial"
    end_p = pd.Period(end_date, freq="M")
    periods = [end_p - i for i in range(months-1, -1, -1)]
    period_keys = [_normalize(f"eficiencia_{MESES_ES[p.month]}_{p.year}") for p in periods]
    
    def _label(nombre: str) -> str:
        op_key = str(nombre or "").strip().casefold()
        match = df_hist[df_hist[c_nombre] == op_key]
        if match.empty: return "Sin historial"
        valores = []
        for pk in period_keys[:-1]:
            if pk in match.columns:
                val = pd.to_numeric(match[pk], errors='coerce').iloc[0]
                if not pd.isna(val): valores.append(val * 100.0 if val <= 1.5 else val)
        val_act = current_eff_map.get(nombre)
        if val_act is not None: valores.append(float(val_act))
        if len(valores) < 2: return "Datos insuficientes"
        prom = sum(valores) / len(valores)
        ult_txt = ",".join([f"{v:.1f}" for v in valores[-3:]])
        diff = (sum(valores[-2:])/2.0) - (sum(valores[:2])/2.0)
        base = f"ult_3m({ult_txt})_prom={prom:.1f}"
        if diff > 1.5: return f"{base}_ Mejora"
        if diff < -1.5: return f"{base}_ Decreciente"
        return f"{base}_ Neutro"
    return _label

# --- Procesamiento de Tablas ---

def _prepare_maquina_table(df: pd.DataFrame) -> pd.DataFrame:
    c_maq, c_qty, c_tc, c_tp, c_cs = [_find_col(df, COLS[k]) for k in ["maquina", "cantidad", "tc", "tp", "cs"]]
    df_m = df.copy()
    df_m[c_maq] = df_m[c_maq].apply(_normalize_machine_torsion)
    res = df_m.groupby(c_maq).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    res.columns = ["Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar"]
    res["%prod"] = (res["cor.estandar"] / (res["t_corrida"] + res["T.perd"]).replace(0, np.nan)) * 100
    res["%eficiencia"] = (res["cor.estandar"] / res["t_corrida"].replace(0, np.nan)) * 100
    return res.fillna(0)

def _prepare_operario_table(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    c_op, c_tc, c_tp, c_cs, c_qty = [_find_col(df, COLS[k]) for k in ["operario", "tc", "tp", "cs", "cantidad"]]
    res = df.groupby(c_op).agg({c_tc: "sum", c_tp: "sum", c_cs: "sum", c_qty: "sum"}).reset_index()
    res.columns = ["Nombre", "T.corrida", "T.perdido", "Cor.est", "Produccion"]
    res["%prod"] = (res["Cor.est"] / (res["T.corrida"] + res["T.perdido"]).replace(0, np.nan)) * 100
    res["%efic"] = (res["Cor.est"] / res["T.corrida"].replace(0, np.nan)) * 100
    if start != end:
        eff_map = dict(zip(res["Nombre"], res["%efic"]))
        labeler = _trend_labeler(end, eff_map)
        res["Tendencia"] = res["Nombre"].apply(labeler)
    else: res["Tendencia"] = ""
    return res.sort_values(by="%efic", ascending=False).fillna(0)

def _prepare_denier_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    c_art, c_kg, c_tc, c_tp, c_cs, c_maq = [_find_col(df, COLS[k]) for k in ["articulo", "kg", "tc", "tp", "cs", "maquina"]]
    df_d = df.copy()
    df_d["Denier_Val"] = df_d[c_art].apply(_extract_denier)
    total_tc = df_d[c_tc].sum()
    
    # Tabla resumen por Denier (Mantiene detalle individual)
    res_ind = df_d.groupby("Denier_Val").agg({c_kg: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    res_ind.columns = ["Denier", "Kg", "TC", "TP", "CS"]
    res_ind["%TC"] = (res_ind["TC"] / total_tc * 100) if total_tc > 0 else 0
    res_ind["%_prod"] = (res_ind["CS"] / (res_ind["TC"] + res_ind["TP"]).replace(0, np.nan)) * 100
    
    # --- Lógica específica para Mapa de Calor (Agrupamiento solicitado) ---
    def _get_heatmap_group(m):
        norm = _normalize_machine_torsion(m)
        if norm.startswith("ROB"):
            return "ROB" # Agrupa ROB 1 a ROB 10 en "ROB"
        if norm in ["S6", "S8", "S9", "S10", "S11", "S12"]:
            return "Simas" # Agrupa S6 a S12 en "Simas"
        return norm

    df_d["Maq_Heatmap"] = df_d[c_maq].apply(_get_heatmap_group)
    pivot = df_d.pivot_table(index="Denier_Val", columns="Maq_Heatmap", values=c_kg, aggfunc="sum").fillna(0)
    
    plt.figure(figsize=(12, 8))
    sns.heatmap(pivot, annot=True, fmt=".0f", cmap="YlOrRd", cbar_kws={'label': 'Kg Producidos'})
    plt.title("Tendencia de Denier procesados por Maquina (Kg)")
    plt.xlabel("Maquinas")
    plt.ylabel("Denier")
    
    heatmap_path = os.path.join(REPORTS_DIR, "tmp_heatmap_torsion.png")
    plt.savefig(heatmap_path, bbox_inches='tight')
    plt.close()
    
    return res_ind.sort_values(by="Kg", ascending=False).fillna(0), heatmap_path

# --- PDF Class ---

class ReporteTorsion(FPDF):
    def __init__(self, start: date, end: date):
        super().__init__(format="Letter")
        self._start, self._end = start, end

    def header(self):
        self.set_font("Arial", "B", 14)
        self.cell(0, 10, "Analisis Proceso Torsion", 0, 1, "C")
        self.set_font("Arial", "", 10)
        self.cell(0, 5, f"Periodo: {self._start.strftime('%d/%m/%Y')} a {self._end.strftime('%d/%m/%Y')}", 0, 1, "C")
        self.ln(5)

    def draw_table_maquinas(self, df):
        self.set_font("Arial", "B", 10)
        self.cell(0, 8, " 1. Resumen de Produccion por Maquina", 0, 1, "L")
        cols, w = ["Maquina", "Prod(kg)", "T.Corr", "T.Perd", "% Prod", "% Efic"], [25, 25, 25, 25, 45, 45]
        self.set_font("Arial", "B", 8)
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln()
        self.set_font("Arial", "", 8)
        for _, r in df.iterrows():
            self.cell(w[0], 6, str(r["Maquina"]), 1)
            self.cell(w[1], 6, f"{r['produccion']:,.0f}", 1, 0, "R")
            self.cell(w[2], 6, f"{r['t_corrida']:.1f}", 1, 0, "R")
            self.cell(w[3], 6, f"{r['T.perd']:.1f}", 1, 0, "R")
            self.cell(w[4], 6, f"{r['%prod']:.1f}%", 1, 0, "R")
            self.cell(w[5], 6, f"{r['%eficiencia']:.1f}%", 1, 0, "R"); self.ln()
        self.ln(5)

    def draw_table_ops(self, df):
        is_range = self._start != self._end
        self.set_font("Arial", "B", 10)
        self.cell(0, 8, " 2. Seguimiento Eficiencia Operarios", 0, 1, "L")
        if is_range:
            cols, w = ["Nombre", "Prod", "T.Corr", "% Prod", "% Efic", "Tendencia"], [45, 18, 18, 22, 22, 65]
        else:
            cols, w = ["Nombre", "Prod", "T.Corr", "T.Perd", "% Prod", "% Efic"], [60, 25, 25, 25, 25, 30]
        self.set_font("Arial", "B", 8)
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln()
        for _, r in df.iterrows():
            self.set_font("Arial", "", 6.5 if is_range else 8)
            self.cell(w[0], 6, str(r["Nombre"])[:30], 1)
            self.set_font("Arial", "", 7.5 if is_range else 8)
            self.cell(w[1], 6, f"{r['Produccion']:,.0f}", 1, 0, "R")
            self.cell(w[2], 6, f"{r['T.corrida']:.1f}", 1, 0, "R")
            if is_range:
                self.cell(w[3], 6, f"{r['%prod']:.1f}%", 1, 0, "R")
                self.cell(w[4], 6, f"{r['%efic']:.1f}%", 1, 0, "R")
                self.set_font("Arial", "", 6)
                self.cell(w[5], 6, _sanitize_pdf_text(str(r["Tendencia"])), 1, 0, "L")
            else:
                self.cell(w[3], 6, f"{r['T.perdido']:.1f}", 1, 0, "R")
                self.cell(w[4], 6, f"{r['%prod']:.1f}%", 1, 0, "R")
                self.cell(w[5], 6, f"{r['%efic']:.1f}%", 1, 0, "R")
            self.ln()
        self.ln(5)

    def draw_table_denier(self, df):
        self.set_font("Arial", "B", 10)
        self.cell(0, 8, " 3. Analisis Denier Procesados", 0, 1, "L")
        cols, w = ["Denier", "Kg Producidos", "Tiempo Corrida (h)", "% Tiempo Total", "%_prod"], [38, 38, 38, 38, 38]
        self.set_font("Arial", "B", 8)
        for i, c in enumerate(cols): self.cell(w[i], 7, c, 1, 0, "C")
        self.ln()
        self.set_font("Arial", "", 8)
        for _, r in df.iterrows():
            self.cell(w[0], 6, str(int(r["Denier"])) if r["Denier"] > 0 else "N/D", 1, 0, "C")
            self.cell(w[1], 6, f"{r['Kg']:,.1f}", 1, 0, "R")
            self.cell(w[2], 6, f"{r['TC']:.1f}", 1, 0, "R")
            self.cell(w[3], 6, f"{r['%TC']:.1f}%", 1, 0, "R")
            self.cell(w[4], 6, f"{r['%_prod']:.1f}%", 1, 0, "R")
            self.ln()
        self.ln(5)

    def draw_denier_heatmap(self, heatmap_path):
        if heatmap_path and os.path.exists(heatmap_path):
            self.add_page()
            self.set_font("Arial", "B", 10)
            self.cell(0, 8, " 4. Visualizacion de Tendencia Denier vs Maquinas", 0, 1, "L")
            self.image(heatmap_path, x=15, w=180)
            os.remove(heatmap_path)

    def draw_final_summary(self, totals):
        self.set_font("Arial", "B", 11)
        self.cell(0, 8, " 5. Resumen Global y Conclusiones", 0, 1, "L")
        self.set_font("Arial", "", 9)
        rows = [
            ("Total Produccion (Kg)", f"{totals['produccion_kg']:,.1f}"),
            ("% Productividad Total", f"{totals['prod_total']:.2f}%"),
            ("% Eficiencia Total", f"{totals['efic_total']:.2f}%"),
            ("Total Horas Perdidas", f"{totals['t_perdido']:.2f} h")
        ]
        for label, val in rows:
            self.cell(60, 7, label, 1); self.cell(40, 7, val, 1, 1, "R")
        self.ln(5)
        self.multi_cell(0, 5, _sanitize_pdf_text(f"Conclusion: El proceso opero con una eficiencia del {totals['efic_total']:.1f}%."))

# --- Función de Construcción ---

def build_pdf_torsion(df_range: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    c_centro = _find_col(df_range, COLS["centro"])
    df_filt = df_range[df_range[c_centro].astype(str).str.strip().str.upper() == "TORSION"].copy()
    if df_filt.empty: return "", []

    # Procesamiento
    df_maq = _prepare_maquina_table(df_filt)
    df_ope = _prepare_operario_table(df_filt, start, end)
    df_ind, heatmap_path = _prepare_denier_analysis(df_filt)
    
    # Totales
    c_cs, c_tc, c_tp, c_kg = [_find_col(df_filt, COLS[k]) for k in ["cs", "tc", "tp", "kg"]]
    sum_cs, sum_tc, sum_tp = df_filt[c_cs].sum(), df_filt[c_tc].sum(), df_filt[c_tp].sum()
    totals = {
        "produccion_kg": df_filt[c_kg].sum(),
        "t_perdido": sum_tp,
        "prod_total": (sum_cs / (sum_tc + sum_tp) * 100) if (sum_tc + sum_tp) > 0 else 0,
        "efic_total": (sum_cs / sum_tc * 100) if sum_tc > 0 else 0
    }

    # Generación PDF
    pdf = ReporteTorsion(start, end)
    pdf.add_page()
    pdf.draw_table_maquinas(df_maq)
    pdf.draw_table_ops(df_ope)
    pdf.draw_table_denier(df_ind)
    pdf.draw_denier_heatmap(heatmap_path)
    pdf.draw_final_summary(totals)

    out_path = os.path.join(REPORTS_DIR, f"Reporte_Torsion_{start}_{end}.pdf")
    pdf.output(out_path)
    return out_path, []

def _download_torsion_df(): return pd.read_excel(TORSION_DATA_URL)
def handle_torsion_message(_pk, _txt): return False
__all__ = ["_download_torsion_df", "build_pdf_torsion", "handle_torsion_message"]