# workflows/manager_extruder_report.py
# Informe Gerencial Analítico (Extrusión + Recuperado)
# Análisis de IA por sección, tono conversacional e identificación de tendencias.

import os
import re
import json
from io import BytesIO
from datetime import datetime, date

import pandas as pd
import requests
import pytz

try:
    from fpdf import FPDF
except ImportError as e:
    raise RuntimeError("Falta fpdf2. pip install fpdf2") from e

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
except ImportError as e:
    raise RuntimeError("Falta matplotlib") from e

try:
    from openai import OpenAI
    _oa_client = OpenAI()
except Exception:
    _oa_client = None

from services.session_memory import CONFIG_DIR
from services.whatsapp_service import send_whatsapp_message
from services.whatsapp_media import send_whatsapp_document
from services.wa_window_manager import canon_phone_e164_co

REPORTS_DIR = os.path.join(CONFIG_DIR, "manager_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)
TZ = "America/Bogota"

# URLs
EXTRUDER_URL = "https://docs.google.com/spreadsheets/d/1QulQQRGMANNv8sP17SAgu8Wox4eeri_0zHRb55zFKuY/export?format=xlsx"
RECUPERADO_URL = "https://docs.google.com/spreadsheets/d/1dCtQnMpb3oVAjl3QYe-xF0mk7WWMUpGrviYrj7LJ3Pg/export?format=xlsx"

BASE_COLS = {
    "name": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre Operario"],
    "fecha": ["Fecha_Efectiva", "fecha_efectiva", "fecha", "Fecha"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_cda", "tpo_corrida"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrstand"],
    "cause": ["Causa_Paro", "causa_paro", "causa", "motivo_paro"],
    "kg_producidos": ["Cant_Kg", "cant_kg", "Kg", "kg", "Kg_producidos"],
    "cant_desp": ["Cant_Desp", "cant_desp", "Desperdicio", "desperdicio"],
    "maq": ["Maquina", "Máquina", "maquina", "machine", "equipo"]
}

# =========================
# UTILIDADES
# =========================
def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", s or "").strip("_")

def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")

def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty: return None
    norm_map = {_normalize(col): col for col in df.columns}
    for c in candidates:
        if _normalize(c) in norm_map: return norm_map[_normalize(c)]
    return None

def _safe_to_datetime(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", dayfirst=True)

def _sanitize_pdf_text(s: str) -> str:
    if not s: return ""
    # Mapeo de emojis a texto para evitar crash en FPDF
    repl = {
        "\u2013": "-", "\u2014": "-", "\u2018": "'", "\u2019": "'", "\u201C": '"', "\u201D": '"',
        "✅": "[OK] ", "⚠️": "[ALERTA] ", "📊": "[DATOS] ", "📉": "[BAJA] ", "📈": "[ALTA] ", 
        "💡": "[IDEA] ", "🔍": "[ANÁLISIS] ", "🏆": "[TOP] ", "🛑": "[PARO] ", "👤": "[OPERARIO] "
    }
    for k, v in repl.items(): s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

def _clean_names(name: str) -> str:
    return " ".join(str(name).split()[:2])

# =========================
# PROCESAMIENTO DE DATOS
# =========================
def _download_area(url: str) -> pd.DataFrame:
    try:
        df = pd.read_excel(BytesIO(requests.get(url, timeout=60).content))
        fecha_col = _find_col(df, BASE_COLS["fecha"])
        if fecha_col:
            df["_fecha"] = _safe_to_datetime(df[fecha_col])
            fmax = df["_fecha"].dropna().max()
            if pd.notna(fmax):
                month, year = fmax.month, fmax.year
                df = df[(df["_fecha"].dt.month == month) & (df["_fecha"].dt.year == year)].copy()
        
        maq_col = _find_col(df, BASE_COLS["maq"])
        if maq_col: df[maq_col] = df[maq_col].astype(str).str.strip().str.upper()
        
        return df
    except Exception as e:
        print(f"❌ Error descargando {url}: {e}")
        return pd.DataFrame()

def _process_area_data(df: pd.DataFrame):
    if df.empty: return None

    kg_col = _find_col(df, BASE_COLS["kg_producidos"])
    desp_col = _find_col(df, BASE_COLS["cant_desp"])
    tc_col = _find_col(df, BASE_COLS["tc"])
    tp_col = _find_col(df, BASE_COLS["tp"])
    cs_col = _find_col(df, BASE_COLS["cs"])
    maq_col = _find_col(df, BASE_COLS["maq"])
    name_col = _find_col(df, BASE_COLS["name"])
    cause_col = _find_col(df, BASE_COLS["cause"])

    for c in [kg_col, desp_col, tc_col, tp_col, cs_col]:
        if c: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    # 1. TABLERO GLOBAL
    tot_kg = df[kg_col].sum() if kg_col else 0
    tot_desp = df[desp_col].sum() if desp_col else 0
    tot_tc = df[tc_col].sum() if tc_col else 0
    tot_tp = df[tp_col].sum() if tp_col else 0
    tot_cs = df[cs_col].sum() if cs_col else 0

    prod_global = (tot_cs / (tot_tc + tot_tp) * 100) if (tot_tc + tot_tp) > 0 else 0
    desp_global = (tot_desp / tot_kg * 100) if tot_kg > 0 else 0

    kpis = {
        "kg": tot_kg, "prod_pct": prod_global, "desp_pct": desp_global, 
        "tc_h": tot_tc, "tp_h": tot_tp
    }

    # 2. TABLA MÁQUINAS
    df_maq = pd.DataFrame()
    if maq_col:
        g = df.groupby(maq_col).agg({kg_col:'sum', tc_col:'sum', tp_col:'sum', cs_col:'sum', desp_col:'sum'}).reset_index()
        g['prod_pct'] = np.where((g[tc_col]+g[tp_col])>0, (g[cs_col]/(g[tc_col]+g[tp_col]))*100, 0)
        g['efi_pct'] = np.where(g[tc_col]>0, (g[cs_col]/g[tc_col])*100, 0)
        g['desp_pct'] = np.where(g[kg_col]>0, (g[desp_col]/g[kg_col])*100, 0)
        df_maq = g.sort_values('prod_pct', ascending=False)
        df_maq = df_maq.rename(columns={maq_col: 'Maquina', kg_col: 'prod_kg', tc_col:'tc', tp_col:'tp'})

    # 3. TABLA CAUSAS PERDIDAS
    df_causes = pd.DataFrame()
    if cause_col and tp_col:
        df_causes = df.groupby(cause_col)[tp_col].sum().reset_index()
        df_causes.columns = ['Causa', 'Horas']
        df_causes = df_causes[df_causes['Horas']>0].sort_values('Horas', ascending=False)

    # 4. TABLA OPERARIOS
    df_ops = pd.DataFrame()
    if name_col:
        go = df.groupby(name_col).agg({cs_col:'sum', tc_col:'sum', tp_col:'sum', desp_col:'sum', kg_col:'sum'}).reset_index()
        go['prod_pct'] = np.where((go[tc_col]+go[tp_col])>0, (go[cs_col]/(go[tc_col]+go[tp_col]))*100, 0)
        go['efi_pct'] = np.where(go[tc_col]>0, (go[cs_col]/go[tc_col])*100, 0)
        go['desp_pct'] = np.where(go[kg_col]>0, (go[desp_col]/go[kg_col])*100, 0)
        go[name_col] = go[name_col].apply(_clean_names)
        df_ops = go.sort_values('prod_pct', ascending=False).rename(columns={name_col: 'Operario'})

    return {
        "raw": df, "kpis": kpis, "maquinas": df_maq, 
        "causas": df_causes, "operarios": df_ops,
        "cols": {"cause": cause_col, "tp": tp_col, "maq": maq_col, "name": name_col}
    }

# =========================
# ARMADO DE CONTEXTO IA (Matemática Pura)
# =========================
def _build_ai_context_for_area(data: dict, area_name: str) -> str:
    if not data: return ""
    
    df_maq = data['maquinas']
    df_ops = data['operarios']
    df_causes = data['causas']
    raw_df = data['raw']
    cols = data['cols']

    ctx = f"\n=== DATOS DEL ÁREA: {area_name} ===\n"

    # 1. Productividad (Top 3 peores máquinas)
    ctx += "1. MÁQUINAS CON BAJA PRODUCTIVIDAD:\n"
    if not df_maq.empty:
        peores_maq = df_maq.sort_values('prod_pct', ascending=True).head(3)
        for _, row in peores_maq.iterrows():
            m_name = row['Maquina']
            prod = row['prod_pct']
            efi = row['efi_pct']
            
            if efi >= 85.0:
                sub = raw_df[raw_df[cols['maq']] == m_name]
                causas = sub.groupby(cols['cause'])[cols['tp']].sum().sort_values(ascending=False).head(2)
                txt_c = ", ".join([f"{k} ({v:.1f}h)" for k,v in causas.items()])
                ctx += f" - Máquina {m_name}: Prod: {prod:.1f}%, Eficiencia: {efi:.1f}%. (Eficiencia >85%). Causa real: TIEMPOS PERDIDOS. Top causas: {txt_c}.\n"
            else:
                sub = raw_df[raw_df[cols['maq']] == m_name]
                go = sub.groupby(cols['name']).agg({BASE_COLS['cs'][0]:'sum', BASE_COLS['tc'][0]:'sum'})
                go['efi'] = np.where(go.iloc[:,1]>0, (go.iloc[:,0]/go.iloc[:,1])*100, 0)
                peores_ops = go.sort_values('efi', ascending=True).head(2)
                txt_o = ", ".join([f"{_clean_names(k)} (Efi: {v['efi']:.1f}%)" for k,v in peores_ops.iterrows()])
                ctx += f" - Máquina {m_name}: Prod: {prod:.1f}%, Eficiencia: {efi:.1f}%. (Eficiencia <85%). Causa real: MICROPARADAS / OPERARIOS. Peores operarios aquí: {txt_o}.\n"

    # 2. Análisis Desperdicio
    ctx += "\n2. MÁQUINAS CON ALTO DESPERDICIO (Meta 3.5%):\n"
    if not df_maq.empty:
        peores_desp = df_maq.sort_values('desp_pct', ascending=False).head(3)
        for _, row in peores_desp.iterrows():
            m_name = row['Maquina']
            d_pct = row['desp_pct']
            if d_pct > 3.5:
                sub = raw_df[raw_df[cols['maq']] == m_name]
                ops = sub.groupby(cols['name']).agg({BASE_COLS['cant_desp'][0]:'sum', BASE_COLS['kg_producidos'][0]:'sum'})
                ops['d_pct'] = np.where(ops.iloc[:,1]>0, (ops.iloc[:,0]/ops.iloc[:,1])*100, 0)
                top_ops = ops.sort_values('d_pct', ascending=False).head(2)
                txt_o = ", ".join([f"{_clean_names(k)} ({v['d_pct']:.1f}%)" for k,v in top_ops.iterrows()])
                ctx += f" - Máquina {m_name}: Desperdicio: {d_pct:.1f}%. Operarios responsables: {txt_o}.\n"

    # 3. Tiempos Perdidos
    ctx += "\n3. TIEMPOS PERDIDOS (Top Causas):\n"
    if not df_causes.empty:
        top_causas = df_causes.head(3)
        for _, row in top_causas.iterrows():
            c_name = row['Causa']
            sub = raw_df[raw_df[cols['cause']] == c_name]
            maqs = sub.groupby(cols['maq'])[cols['tp']].sum().sort_values(ascending=False).head(2)
            txt_m = ", ".join([f"{k} ({v:.1f}h)" for k,v in maqs.items()])
            ctx += f" - Causa '{c_name}' ({row['Horas']:.1f}h total). Afectó más a las máquinas: {txt_m}.\n"

    # 4. Rendimiento Operarios
    ctx += "\n4. ALERTA OPERARIOS (Top 5 más bajos en Productividad):\n"
    if not df_ops.empty:
        peores_ops_total = df_ops[df_ops['prod_pct'] > 0].sort_values('prod_pct', ascending=True).head(5)
        txt_op_all = ", ".join([f"{row['Operario']} ({row['prod_pct']:.1f}%)" for _, row in peores_ops_total.iterrows()])
        ctx += f" - Sugerencia de revisión/reentrenamiento para: {txt_op_all}.\n"

    return ctx

def _generate_area_ai_analysis(ctx: str, area_name: str, manager_name: str, month: int, year: int) -> str:
    partes_nombre = manager_name.split()
    nombre_pila = partes_nombre[1].capitalize() if len(partes_nombre) > 1 else manager_name.capitalize()

    sys_prompt = (
        f"Eres CiplasBot, Asesor de Datos. Escribe un reporte gerencial sobre el área de {area_name} para "
        f"tu gerente, cuyo nombre es {nombre_pila}. Dirígete a ella/él directamente en tono conversacional y profesional.\n"
        "REQUISITOS IMPORTANTES:\n"
        "1. Usa emojis para organizar el texto (usa ✅, ⚠️, 💡, 🔍, 📊, 👤, 🛑).\n"
        "2. Incluye PREGUNTAS DE TENDENCIA analíticas dentro del texto. Ejemplos de tono: "
        f"'{nombre_pila}, ¿has notado que la máquina X tiene alta eficiencia pero baja productividad debido a [causa]?' o "
        f"'{nombre_pila}, he notado que el operario Y tiene mejor desempeño que Z, pero su desperdicio es mayor...'\n"
        "ESTRUCTURA EL REPORTE EN ESTOS 4 PUNTOS:\n"
        "1. Análisis de Productividad vs Eficiencia\n"
        "2. Análisis de Desperdicio\n"
        "3. Análisis de Tiempos Perdidos\n"
        "4. Rendimiento de Operarios\n"
        "Sé conciso, claro, organizado a manera de reporte de hallazgos."
    )
    user_prompt = f"Aquí están los datos del periodo {year}-{month:02d} para el área de {area_name}:\n{ctx}\nRedacta tu reporte conversacional."

    try:
        if _oa_client is None: raise RuntimeError("Sin API Key")
        chat = _oa_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.35,
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}]
        )
        return _sanitize_pdf_text(chat.choices[0].message.content.strip())
    except Exception as e:
        print("Error IA:", e)
        return _sanitize_pdf_text("Análisis IA no disponible temporalmente.")

# =========================
# PLOTEO
# =========================
def _plot_pareto(df_causes: pd.DataFrame, area: str, month: int, year: int) -> str | None:
    if df_causes.empty: return None
    g = df_causes.head(6) 
    
    fig, ax = plt.subplots(figsize=(9, 3.5), dpi=130)
    y_pos = np.arange(len(g))
    ax.barh(y_pos, g["Horas"], align='center', color='crimson')
    ax.set_yticks(y_pos)
    ax.set_yticklabels(g["Causa"].astype(str))
    ax.invert_yaxis()
    ax.set_xlabel('Horas Totales Perdidas')
    ax.set_title(f"Tiempos Perdidos - {area} ({year}-{month:02d})")
    ax.grid(axis="x", linestyle="--", alpha=0.5)

    fig.tight_layout()
    img_path = os.path.join(REPORTS_DIR, f"pareto_{area.lower()}_{year}{month}.png")
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

# =========================
# GENERADOR DE PDF
# =========================
def _add_area_section(pdf: FPDF, area_name: str, data: dict, pareto_img: str, ai_text: str):
    if not data: return
    page_w = pdf.w - 2 * pdf.l_margin

    # Salto de página inteligente para no dejar huecos
    if pdf.get_y() > 230:
        pdf.add_page()
    else:
        pdf.ln(5)
        
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_fill_color(0, 51, 102)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 10, f"  SECCIÓN: {area_name.upper()}  ", ln=1, fill=True)
    pdf.ln(3)

    # 1. TABLERO GLOBAL
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 6, "Tablero de Control Global", ln=1)
    
    k = data['kpis']
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(page_w/2, 6, f" Producción: {k['kg']:,.0f} Kg", border=1)
    pdf.cell(page_w/2, 6, f" Productividad: {k['prod_pct']:.1f}%", border=1, ln=1)
    pdf.cell(page_w/2, 6, f" Desperdicio: {k['desp_pct']:.2f}%", border=1)
    pdf.cell(page_w/2, 6, f" Tiempo Corrida: {k['tc_h']:,.1f} h", border=1, ln=1)
    pdf.cell(page_w/2, 6, f" Horas Perdidas: {k['tp_h']:,.1f} h", border=1, ln=1)
    pdf.ln(5)

    # 2. TABLA MÁQUINAS
    df_maq = data['maquinas']
    if not df_maq.empty:
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, "Detalle por Máquina (Ordenado por Productividad)", ln=1)
        
        pdf.set_fill_color(220, 230, 241)
        pdf.set_font("Helvetica", "B", 8)
        w_m, w_num = 30, 25
        pdf.cell(w_m, 6, "Máquina", border=1, fill=True)
        pdf.cell(w_num, 6, "Prod (Kg)", border=1, fill=True, align="C")
        pdf.cell(w_num, 6, "T. Corrida", border=1, fill=True, align="C")
        pdf.cell(w_num, 6, "T. Perdido", border=1, fill=True, align="C")
        pdf.cell(w_num, 6, "% Desp.", border=1, fill=True, align="C")
        pdf.cell(w_num, 6, "% Prod.", border=1, fill=True, align="C")
        pdf.cell(w_num, 6, "% Eficiencia", border=1, fill=True, align="C", ln=1)

        pdf.set_font("Helvetica", "", 8)
        for _, r in df_maq.iterrows():
            pdf.cell(w_m, 5, str(r['Maquina'])[:15], border=1)
            pdf.cell(w_num, 5, f"{r['prod_kg']:,.0f}", border=1, align="R")
            pdf.cell(w_num, 5, f"{r['tc']:.1f}h", border=1, align="R")
            pdf.cell(w_num, 5, f"{r['tp']:.1f}h", border=1, align="R")
            pdf.cell(w_num, 5, f"{r['desp_pct']:.1f}%", border=1, align="R")
            pdf.cell(w_num, 5, f"{r['prod_pct']:.1f}%", border=1, align="R")
            pdf.cell(w_num, 5, f"{r['efi_pct']:.1f}%", border=1, align="R", ln=1)
        pdf.ln(5)

    # 3. GRÁFICA TIEMPOS PERDIDOS
    if pareto_img and os.path.exists(pareto_img):
        if pdf.get_y() > 200: pdf.add_page()
        pdf.image(pareto_img, w=page_w * 0.95)
        pdf.ln(2)

    # 4. TOP 5 / BOTTOM 5 OPERARIOS
    df_ops = data['operarios']
    if not df_ops.empty:
        if pdf.get_y() > 230: pdf.add_page()
        pdf.set_font("Helvetica", "B", 11)
        pdf.cell(0, 6, "Rendimiento Operarios (% Productividad)", ln=1)
        
        top5 = df_ops.head(5).reset_index(drop=True)
        bot5 = df_ops[df_ops['prod_pct']>0].tail(5).sort_values('prod_pct', ascending=True).reset_index(drop=True)

        pdf.set_fill_color(220, 240, 220)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(page_w/2, 6, "TOP 5 (Mejores)", border=1, align="C", fill=True)
        pdf.set_fill_color(255, 230, 230)
        pdf.cell(page_w/2, 6, "ALERTA: BOTTOM 5 (Más bajos)", border=1, align="C", fill=True, ln=1)

        pdf.set_font("Helvetica", "", 8)
        for i in range(5):
            t_txt = f"{top5.iloc[i]['Operario']} ({top5.iloc[i]['prod_pct']:.1f}%)" if i < len(top5) else ""
            b_txt = f"{bot5.iloc[i]['Operario']} ({bot5.iloc[i]['prod_pct']:.1f}%)" if i < len(bot5) else ""
            pdf.cell(page_w/2, 5, _sanitize_pdf_text(t_txt), border=1)
            pdf.cell(page_w/2, 5, _sanitize_pdf_text(b_txt), border=1, ln=1)
        pdf.ln(8)

    # 5. ANÁLISIS DE IA (Al final de la sección)
    if ai_text:
        if pdf.get_y() > 220: pdf.add_page()
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(0, 102, 51)
        pdf.cell(0, 8, f">> Análisis Estratégico de {area_name.capitalize()} (IA CiplasBot)", ln=1)
        
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _sanitize_pdf_text(ai_text))
        pdf.ln(10) # Espacio extra antes de la siguiente sección


def _build_manager_pdf(name: str, data_ext: dict, data_rec: dict, img_ext: str, img_rec: str, ai_ext: str, ai_rec: str, month: int, year: int) -> str:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # Portada rápida (Título principal)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 15, "INFORME GERENCIAL DE PLANTA", ln=1, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(0, 6, f"Periodo Analizado: {year}-{month:02d}", ln=1, align="C")
    pdf.cell(0, 6, f"Generado para: {name}", ln=1, align="C")
    pdf.ln(5)
    
    # Dibujar las secciones (empalmarán inmediatamente debajo de la portada)
    _add_area_section(pdf, "EXTRUSIÓN", data_ext, img_ext, ai_ext)
    _add_area_section(pdf, "RECUPERADO", data_rec, img_rec, ai_rec)

    out_path = os.path.join(REPORTS_DIR, f"Gerencial_{_slug(name)}_{year}{month:02d}.pdf")
    pdf.output(out_path)
    return out_path

def handle_manager_extruder_report(phone: str, manager_name: str, to_norm: str):
    send_whatsapp_message(to_norm, f"📊 Hola *{manager_name}*. Extrayendo datos de Extrusión y Recuperado para tu Diagnóstico Asistido por IA... ⏳")
    
    df_ext_raw = _download_area(EXTRUDER_URL)
    df_rec_raw = _download_area(RECUPERADO_URL)
    
    if df_ext_raw.empty and df_rec_raw.empty:
        send_whatsapp_message(to_norm, "❌ Error: Bases de datos vacías o inaccesibles.")
        return True
        
    data_ext = _process_area_data(df_ext_raw)
    data_rec = _process_area_data(df_rec_raw)

    month, year = datetime.now().month, datetime.now().year # Fallback
    if data_ext: 
        fmax = pd.to_datetime(df_ext_raw["Fecha_Efectiva"], errors='coerce').dropna().max()
        if pd.notna(fmax): month, year = fmax.month, fmax.year

    # Construimos contextos por separado
    ctx_ext = _build_ai_context_for_area(data_ext, "EXTRUSIÓN")
    ctx_rec = _build_ai_context_for_area(data_rec, "RECUPERADO")

    # Llamamos a la IA dos veces (Una para Extrusión, otra para Recuperado)
    ai_text_ext = _generate_area_ai_analysis(ctx_ext, "EXTRUSIÓN", manager_name, month, year) if data_ext else ""
    ai_text_rec = _generate_area_ai_analysis(ctx_rec, "RECUPERADO", manager_name, month, year) if data_rec else ""

    img_ext = _plot_pareto(data_ext['causas'], "Extrusión", month, year) if data_ext else None
    img_rec = _plot_pareto(data_rec['causas'], "Recuperado", month, year) if data_rec else None
    
    pdf_path = _build_manager_pdf(manager_name, data_ext, data_rec, img_ext, img_rec, ai_text_ext, ai_text_rec, month, year)
    
    try:
        send_whatsapp_document(to_norm, pdf_path, caption="📈 Aquí tienes el Diagnóstico Gerencial interactivo.")
        if img_ext: os.remove(img_ext)
        if img_rec: os.remove(img_rec)
        os.remove(pdf_path)
    except Exception as e:
        send_whatsapp_message(to_norm, "❌ Hubo un error enviando el PDF gerencial.")
    
    return True