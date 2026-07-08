# workflows/impresion_report.py
import os
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from datetime import datetime, date
import numpy as np
import pandas as pd
from fpdf import FPDF
from services.session_memory import CONFIG_DIR

REPORTS_DIR = os.path.join(CONFIG_DIR, "cuerdas_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

URL_COMEXI = "https://docs.google.com/spreadsheets/d/1V-9iIVMLf19vuQIoiu53t6k2J2vlu49vUjEMnKS5bLY/export?format=xlsx"
URL_STELFLEX_SATURNO = "https://docs.google.com/spreadsheets/d/18Lbr6UyAnGVl9g7Nx-8FEjXmDEJ35ksv-n0D4ptfZ40/export?format=xlsx"

COLS = {
    "operario": ["Apellidos_Nombres", "Operario", "Nombre_Operario"],
    "articulo": ["Numero_Articulo", "Numero_articulo", "Articulo_ID"],
    "descripcion": ["Descripcion_Articulo", "Descripcion"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tp"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tc"],
    "cs": ["Corrida_Standar", "corrida_estandar", "cs"],
    "causa": ["Causa_Paro", "Causa", "Motivo"],
    "maquina": ["Maquina", "maquina", "Equipo"],
    "cantidad": ["Cantidad_Completada", "cantidad", "Cant_Kg"],
    "centro": ["Centro_Trabajo", "centro_trabajo", "Centro"]
}

def _find_col(df, candidates):
    for c in candidates:
        if c in df.columns: return c
    return None

def _sanitize_text(s):
    if not s: return ""
    s = str(s).replace('ñ', 'n').replace('Ñ', 'N').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ó', 'o').replace('ú', 'u')
    return s.encode("latin-1", "ignore").decode("latin-1")

def download_impresion_df():
    try:
        df1 = pd.read_excel(URL_COMEXI)
        c_centro = _find_col(df1, COLS["centro"])
        df1_filt = df1[df1[c_centro].astype(str).str.strip().str.upper() == "COMEXI"].copy()
        df2 = pd.read_excel(URL_STELFLEX_SATURNO)
        c_maq = _find_col(df2, COLS["maquina"])
        df2_filt = df2[df2[c_maq].astype(str).str.strip().str.upper().isin(["STELFLEX", "SATURNO"])].copy()
        return pd.concat([df1_filt, df2_filt], ignore_index=True)
    except Exception: return pd.DataFrame()

def prepare_maquina_table(df):
    c_maq, c_qty, c_tc, c_tp, c_cs = [_find_col(df, COLS[k]) for k in ["maquina", "cantidad", "tc", "tp", "cs"]]
    if not all([c_maq, c_qty, c_tc, c_tp, c_cs]): return pd.DataFrame()
    res = df.groupby(c_maq).agg({c_qty: "sum", c_tc: "sum", c_tp: "sum", c_cs: "sum"}).reset_index()
    res.columns = ["Maquina", "produccion", "t_corrida", "t_perdido", "cor_estandar"]
    res["%_prod"] = (res["cor_estandar"] / (res["t_corrida"] + res["t_perdido"]).replace(0, np.nan)) * 100
    res["%_eficiencia"] = (res["cor_estandar"] / res["t_corrida"].replace(0, np.nan)) * 100
    return res.fillna(0)

def prepare_ref_detail_table(df):
    c_maq, c_ref, c_qty, c_tp, c_tc, c_cs = [_find_col(df, COLS[k]) for k in ["maquina", "descripcion", "cantidad", "tp", "tc", "cs"]]
    if not all([c_maq, c_ref, c_qty, c_tp]): return pd.DataFrame()
    res = df.groupby([c_maq, c_ref], sort=False).agg({c_qty: "sum", c_tp: "sum", c_tc: "sum", c_cs: "sum"}).reset_index()
    res.columns = ["Maquina", "Referencia", "produccion", "t_perdido", "tc_total", "cs_total"]
    res["%_prod"] = (res["cs_total"] / (res["tc_total"] + res["t_perdido"]).replace(0, np.nan)) * 100
    return res.fillna(0)

def prepare_change_analysis(df):
    c_op, c_art, c_tp = [_find_col(df, COLS[k]) for k in ["operario", "articulo", "tp"]]
    if not all([c_op, c_art, c_tp]): return pd.DataFrame()
    df_f = df[df[c_art].astype(str).str.strip().str.upper() != "ESP00001"].copy()
    res_c = df_f.groupby(c_op)[c_art].nunique().reset_index(name="unicos")
    res_c["num_cambios"] = (res_c["unicos"] - 1).clip(lower=0)
    res_t = df_f.groupby(c_op)[c_tp].sum().reset_index(name="t_perdido_total")
    final = pd.merge(res_c, res_t, on=c_op, how="outer").fillna(0)
    final["promedio"] = final.apply(lambda r: r["t_perdido_total"] / r["num_cambios"] if r["num_cambios"] > 0 else 0, axis=1)
    return final

def generate_batch_heatmap(df_ref, start_date):
    if df_ref.empty: return None
    df_ref = df_ref.copy()
    df_ref['batch_seq'] = df_ref.groupby('Maquina').cumcount() + 1
    pivot_df = df_ref.pivot(index='Maquina', columns='batch_seq', values='produccion')
    
    plt.figure(figsize=(10, 5))
    norm = mcolors.TwoSlopeNorm(vcenter=5000, vmin=0, vmax=max(10000, df_ref['produccion'].max()))
    heatmap = plt.imshow(pivot_df, aspect='auto', cmap='RdYlGn', norm=norm)
    plt.colorbar(heatmap, label='Metros por Lote (Objetivo: 5.000m)')
    
    plt.yticks(range(len(pivot_df.index)), pivot_df.index)
    plt.xticks(range(len(pivot_df.columns)), pivot_df.columns)
    plt.title(f"Tendencia de Tamano de Lote por Maquina\n(Zona Verde = Cumple > 5.000m)")
    plt.xlabel("Secuencia de Lotes Programados")
    plt.ylabel("Maquinas")

    for i in range(len(pivot_df.index)):
        for j in range(len(pivot_df.columns)):
            val = pivot_df.iloc[i, j]
            if not np.isnan(val):
                plt.text(j, i, f'{val/1000:.1f}k', ha='center', va='center', color='black', fontsize=8)

    chart_path = os.path.join(REPORTS_DIR, f"heatmap_{start_date}.png")
    plt.savefig(chart_path, bbox_inches='tight')
    plt.close()
    return chart_path

def build_pdf_impresion(df, start, end):
    if df.empty: return "", []
    df_maq = prepare_maquina_table(df)
    df_ref = prepare_ref_detail_table(df)
    df_op = prepare_change_analysis(df)
    
    c_maq, c_art = _find_col(df, COLS["maquina"]), _find_col(df, COLS["articulo"])
    res_g = df[df[c_art].astype(str).str.upper() != "ESP00001"].groupby(c_maq)[c_art].nunique().reset_index()
    res_g["cambios"] = (res_g[c_art] - 1).clip(lower=0)
    plt.figure(figsize=(8, 4)); plt.bar(res_g[c_maq], res_g["cambios"], color='orange')
    plt.title("Cambios por Maquina (n-1)"); plt.ylabel("Cant")
    chart_path = os.path.join(REPORTS_DIR, f"graf_{start}.png")
    plt.savefig(chart_path, bbox_inches='tight'); plt.close()

    heatmap_path = generate_batch_heatmap(df_ref, start)

    pdf = FPDF(format="Letter"); pdf.add_page()
    pdf.set_font("Arial", "B", 14); pdf.cell(0, 10, _sanitize_text(f"Eficiencia Impresion: {start} a {end}"), 0, 1, "C")
    
    # Tablas existentes (1, 2, 3) se mantienen igual...
    pdf.ln(5); pdf.set_font("Arial", "B", 10); pdf.cell(0, 8, "1. Eficiencia por Maquina", 0, 1)
    for c, w in zip(["Maquina", "Prod(m)", "% Prod", "% Efic"], [45, 45, 45, 45]): pdf.cell(w, 8, c, 1, 0, "C")
    pdf.ln(); pdf.set_font("Arial", "", 8)
    for _, r in df_maq.iterrows():
        pdf.cell(45, 7, _sanitize_text(r["Maquina"]), 1)
        pdf.cell(45, 7, f"{r['produccion']:,.0f}m", 1, 0, "R")
        pdf.cell(45, 7, f"{r['%_prod']:.1f}%", 1, 0, "R")
        pdf.cell(45, 7, f"{r['%_eficiencia']:.1f}%", 1, 1, "R")

    pdf.ln(5); pdf.set_font("Arial", "B", 10); pdf.cell(0, 8, "2. Produccion y Tiempo Perdido por Referencia", 0, 1)
    for c, w in zip(["Maquina", "Referencia", "Prod(m)", "T. Perdido", "% Prod"], [25, 85, 25, 25, 20]): pdf.cell(w, 8, c, 1, 0, "C")
    pdf.ln(); pdf.set_font("Arial", "", 7)
    for _, r in df_ref.iterrows():
        pdf.cell(25, 6, _sanitize_text(r["Maquina"]), 1)
        pdf.cell(85, 6, _sanitize_text(r["Referencia"])[:58], 1)
        pdf.cell(25, 6, f"{r['produccion']:,.0f}", 1, 0, "R")
        pdf.cell(25, 6, f"{r['t_perdido']:.2f}h", 1, 0, "R")
        pdf.cell(20, 6, f"{r['%_prod']:.1f}%", 1, 1, "R")

    pdf.add_page(); pdf.set_font("Arial", "B", 10); pdf.cell(0, 10, "3. Analisis de Cambios por Operario (n-1)", 0, 1)
    for c, w in zip(["Operario", "Unicos", "Cambios", "T. Perdido Tot", "Prom"], [60, 25, 25, 35, 35]): pdf.cell(w, 8, c, 1, 0, "C")
    pdf.ln(); pdf.set_font("Arial", "", 8)
    for _, r in df_op.iterrows():
        pdf.cell(60, 7, _sanitize_text(r.iloc[0])[:35], 1)
        pdf.cell(25, 7, str(int(r["unicos"])), 1, 0, "C")
        pdf.cell(25, 7, str(int(r["num_cambios"])), 1, 0, "C")
        pdf.cell(35, 7, f"{r['t_perdido_total']:.2f}h", 1, 0, "R")
        pdf.cell(35, 7, f"{r['promedio']:.2f}", 1, 1, "R")

    # Página Final: Gráficos, Mapa de Calor y Análisis de Rangos Actualizado
    if os.path.exists(chart_path) or (heatmap_path and os.path.exists(heatmap_path)):
        pdf.add_page()
        if os.path.exists(chart_path):
            pdf.set_font("Arial", "B", 10); pdf.cell(0, 10, "4. Grafica de Cambios", 0, 1)
            pdf.image(chart_path, x=20, w=170); os.remove(chart_path)
        
        if heatmap_path and os.path.exists(heatmap_path):
            pdf.ln(10)
            pdf.set_font("Arial", "B", 10); pdf.cell(0, 10, "5. Mapa de Calor: Tendencia de Lotes (Min: 5.000m)", 0, 1)
            pdf.image(heatmap_path, x=10, w=195); os.remove(heatmap_path)
            
            # --- SECCIÓN ACTUALIZADA: Conteo y Porcentajes ---
            pdf.ln(5)
            pdf.set_font("Arial", "B", 10)
            pdf.cell(0, 8, "Analisis de Cumplimiento de Lotes (Global)", 0, 1)
            
            counts = [
                df_ref[df_ref['produccion'] < 1000].shape[0],
                df_ref[(df_ref['produccion'] >= 1001) & (df_ref['produccion'] <= 3000)].shape[0],
                df_ref[(df_ref['produccion'] >= 3001) & (df_ref['produccion'] <= 5000)].shape[0],
                df_ref[(df_ref['produccion'] >= 5001) & (df_ref['produccion'] <= 10000)].shape[0],
                df_ref[df_ref['produccion'] > 10000].shape[0]
            ]
            total_refs = sum(counts)
            
            # Encabezados de la tabla pequeña
            pdf.set_font("Arial", "B", 9)
            pdf.cell(70, 7, "Rango de Unidades", 1, 0, "C")
            pdf.cell(25, 7, "Refs", 1, 0, "C")
            pdf.cell(25, 7, "% del Total", 1, 1, "C")
            
            pdf.set_font("Arial", "", 9)
            labels = [
                "Menores a 1,000", "Entre 1,001 y 3,000", "Entre 3,001 y 5,000",
                "Entre 5,001 a 10,000", "Mayores a 10,001"
            ]
            
            for label, count in zip(labels, counts):
                pct = (count / total_refs * 100) if total_refs > 0 else 0
                pdf.cell(70, 6, label, 1)
                pdf.cell(25, 6, str(count), 1, 0, "C")
                pdf.cell(25, 6, f"{pct:.1f}%", 1, 1, "C")
    
    out = os.path.join(REPORTS_DIR, f"Reporte_Impresion_{start}.pdf")
    pdf.output(out)
    return out, []