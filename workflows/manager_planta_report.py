# workflows/manager_planta_report.py
# Informe Macro-Gerencial de Planta (Gestión por Excepciones / Hotspots)
# informe gerencial proceso conversion

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
    raise RuntimeError("Falta fpdf2") from e

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

# ==========================================
# URLS DE BASES DE DATOS
# ==========================================
FILETEADO_URL = "https://docs.google.com/spreadsheets/d/1FYLgfQhLvCUtiuxGnn5aQK6aCChoFPmMU-eMa0KAHrg/export?format=xlsx"
COSTURA_URL = "https://docs.google.com/spreadsheets/d/1V-9iIVMLf19vuQIoiu53t6k2J2vlu49vUjEMnKS5bLY/export?format=xlsx"
CUERDAS_URL = "https://docs.google.com/spreadsheets/d/17cV1hJyZPsoaowZLGJuyhmKtoeWdDTrdWLUjPpDQInQ/export?format=xlsx"
IMPRESION_URL = "https://docs.google.com/spreadsheets/d/18Lbr6UyAnGVl9g7Nx-8FEjXmDEJ35ksv-n0D4ptfZ40/export?format=xlsx"

# ==========================================
# CONFIGURACIÓN DE LÍNEAS, METAS, UNIDADES Y FILTROS
# ==========================================
LINES_CONFIG = {
    # ---- FILETEADO ----
    "Gasa": {
        "target_prod": 80.0, "unit": "Unid.", "url": FILETEADO_URL,
        "filters": {"centro_trabajo": {"eq": "FILEGASA"}, "articulo": {"startswith": "CAG"}}
    },
    "Leno": {
        "target_prod": 80.0, "unit": "Unid.", "url": FILETEADO_URL,
        "filters": {"centro_trabajo": {"eq": "FILEGASA"}, "articulo": {"startswith": "LEN"}, "maq": {"startswith": "FILET"}}
    },
    "Planas": {
        "target_prod": 80.0, "unit": "Unid.", "url": FILETEADO_URL,
        "filters": {"centro_trabajo": {"eq": "FILEGASA"}, "maq": {"startswith": "FIPLA"}}
    },
    "Cortadoras": {
        "target_prod": 75.0, "unit": "Unid.", "url": FILETEADO_URL,
        "filters": {"centro_trabajo": {"eq": "CORTGASA"}}
    },
    
    # ---- CUERDAS ----
    "Cableado": {
        "target_prod": 70.0, "unit": "Kg", "url": CUERDAS_URL,
        "filters": {"centro_trabajo": {"eq": "CABLEADO"}}
    },
    "Torsion": {
        "target_prod": 80.0, "unit": "Kg", "url": CUERDAS_URL,
        "filters": {"centro_trabajo": {"eq": "TORSION"}, "maq": {"not_startswith": "S"}}
    },
    "Trenzado": {
        "target_prod": 80.0, "unit": "Kg", "url": CUERDAS_URL,
        "filters": {"centro_trabajo": {"eq": "TRENZADO"}, "maq": {"not_startswith": "TECH"}}
    },
    "Embobina": {
        "target_prod": 80.0, "unit": "Kg", "url": CUERDAS_URL,
        "filters": {"centro_trabajo": {"eq": "EMBOBINA"}, "maq": {"startswith": "RW"}}
    },
    
    # ---- COSTURA E IMPRESIÓN ----
    "Costura": {
        "target_prod": 78.0, "unit": "Unid.", "url": COSTURA_URL,
        "filters": {"centro_trabajo": {"eq": "COSTURA"}, "maq": {"startswith": "A"}}
    },
    "Impresion": {
        "target_prod": 60.0, "unit": "Unid.", "url": IMPRESION_URL,
        "filters": {"maq": {"in_list": ["STELFLEX", "SATURNO"]}}
    },
    "Impresion RTR": {
        "target_prod": 60.0, "unit": "Unid.", "url": COSTURA_URL,
        "filters": {"maq": {"in_list": ["COM1", "COM2", "COM3", "COM4", "COM5"]}}
    }
}

BASE_COLS = {
    "name": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre Operario"],
    "fecha": ["Fecha_Efectiva", "fecha_efectiva", "fecha", "Fecha"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_cda", "tpo_corrida"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrstand"],
    "cause": ["Causa_Paro", "causa_paro", "causa", "motivo_paro"],
    "unidades": ["Cantidad_Completada", "cantidad_completada", "unidades", "cantidad", "Cant_Kg", "Kg"],
    "maq": ["Maquina", "Máquina", "maquina", "machine", "equipo"],
    "centro_trabajo": ["centro_trabajo", "Centro_Trabajo", "CENTRO_TRABAJO", "Centro trabajo"],
    "articulo": ["Numero_Articulo", "numero_articulo", "Articulo", "articulo", "referencia", "Descripcion_Articulo"]
}

# =========================
# UTILIDADES
# =========================
def _slug(s: str) -> str: return re.sub(r"[^A-Za-z0-9_]+", "_", s or "").strip("_")
def _normalize(s: str) -> str: return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")

def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty: return None
    norm_map = {_normalize(col): col for col in df.columns}
    for c in candidates:
        if _normalize(c) in norm_map: return norm_map[_normalize(c)]
    return None

def _sanitize_pdf_text(s: str) -> str:
    if not s: return ""
    repl = {"✅": "[OK] ", "⚠️": "[ALERTA] ", "📊": "[DATOS] ", "📉": "[BAJA] ", "📈": "[ALTA] ", "💡": "[IDEA] ", "🔍": "[ANÁLISIS] ", "🏆": "[TOP] ", "🛑": "[PARO] ", "👤": "[OPERADOR] ", "🏭": "[MÁQUINA] ", "🚀": "[ACCIÓN] "}
    for k, v in repl.items(): s = s.replace(k, v)
    return s.encode("latin-1", "ignore").decode("latin-1")

def _clean_names(name: str) -> str:
    return " ".join(str(name).split()[:2])

# =========================
# EXTRACCIÓN Y CÁLCULO
# =========================
def _apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    df_filtered = df.copy()
    for col_alias, rule in filters.items():
        col_name = _find_col(df_filtered, BASE_COLS.get(col_alias, [col_alias]))
        if not col_name: return pd.DataFrame() 
        
        serie = df_filtered[col_name].astype(str).str.strip().str.upper()
        
        if "eq" in rule:
            df_filtered = df_filtered[serie == rule["eq"].upper()]
        if "startswith" in rule:
            df_filtered = df_filtered[serie.str.startswith(rule["startswith"].upper())]
        if "not_startswith" in rule:
            df_filtered = df_filtered[~serie.str.startswith(rule["not_startswith"].upper())]
        if "in_list" in rule:
            valid_values = [str(v).upper() for v in rule["in_list"]]
            df_filtered = df_filtered[serie.isin(valid_values)]
            
    return df_filtered

def _download_all_lines() -> pd.DataFrame:
    all_data = []
    month, year = datetime.now().month, datetime.now().year
    url_cache = {} 
    
    for line_name, config in LINES_CONFIG.items():
        if "URL_AQUI" in config["url"]: continue
        
        try:
            url = config["url"]
            if url not in url_cache:
                url_cache[url] = pd.read_excel(BytesIO(requests.get(url, timeout=60).content))
            
            df = url_cache[url].copy()
            
            fecha_col = _find_col(df, BASE_COLS["fecha"])
            if fecha_col:
                df["_fecha"] = pd.to_datetime(df[fecha_col], errors="coerce", dayfirst=True)
                fmax = df["_fecha"].dropna().max()
                if pd.notna(fmax):
                    month, year = fmax.month, fmax.year
                    df = df[(df["_fecha"].dt.month == month) & (df["_fecha"].dt.year == year)].copy()
            
            df = _apply_filters(df, config.get("filters", {}))
            if df.empty: continue
            
            df['Linea'] = line_name
            df['Meta_Prod'] = config["target_prod"]
            df['Unidad_Medida'] = config.get("unit", "Unid.")
            
            for std_key, aliases in BASE_COLS.items():
                if std_key in ["centro_trabajo", "articulo"]: continue 
                col_found = _find_col(df, aliases)
                if col_found: df[std_key] = df[col_found]
                else: df[std_key] = 0 if std_key in ['tc', 'tp', 'cs', 'unidades'] else 'N/D'
            
            # UNIFICAR NOMBRES DE MÁQUINAS: Todo a mayúsculas
            if 'maq' in df.columns:
                df['maq'] = df['maq'].astype(str).str.strip().str.upper()
                df['maq'] = df['maq'].replace("NAN", "SIN MAQUINA")

            all_data.append(df[['Linea', 'Meta_Prod', 'Unidad_Medida', 'name', 'fecha', 'tc', 'tp', 'cs', 'cause', 'unidades', 'maq']])
            
        except Exception as e:
            print(f"❌ Error procesando línea {line_name}: {e}")
            
    if not all_data: return pd.DataFrame(), month, year
    
    master_df = pd.concat(all_data, ignore_index=True)
    for c in ['tc', 'tp', 'cs', 'unidades']: 
        master_df[c] = pd.to_numeric(master_df[c], errors='coerce').fillna(0)
    return master_df, month, year

def _build_global_summary(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby(['Linea', 'Meta_Prod', 'Unidad_Medida']).agg({'unidades':'sum', 'tc':'sum', 'tp':'sum', 'cs':'sum'}).reset_index()
    g['prod_pct'] = np.where((g['tc']+g['tp'])>0, (g['cs']/(g['tc']+g['tp']))*100, 0)
    g['efi_pct'] = np.where(g['tc']>0, (g['cs']/g['tc'])*100, 0)
    g['cumple_meta'] = g['prod_pct'] >= g['Meta_Prod']
    return g

def _analyze_line_machines(df_line: pd.DataFrame) -> pd.DataFrame:
    g = df_line.groupby('maq').agg({'unidades':'sum', 'tc':'sum', 'tp':'sum', 'cs':'sum'}).reset_index()
    g['prod_pct'] = np.where((g['tc']+g['tp'])>0, (g['cs']/(g['tc']+g['tp']))*100, 0)
    g['efi_pct'] = np.where(g['tc']>0, (g['cs']/g['tc'])*100, 0)
    return g.sort_values('prod_pct', ascending=False)

# =========================
# FUNCIONES DE INTELIGENCIA ARTIFICIAL
# =========================
def _get_ai_line_diagnosis(line_name: str, meta: float, prod: float, efi: float, math_diag: str, manager_name: str) -> str:
    """Genera un diagnóstico de IA específico y corto para la línea crítica que va debajo de su tabla."""
    # Forzar el nombre a Wilson
    nombre_pila = "Wilson"
    
    sys_prompt = (
        f"Eres CiplasBot. Escribe un diagnóstico de 3 líneas sobre la línea '{line_name}' dirigido directamente al gerente {nombre_pila}.\n"
        "REGLA: Usa la data matemática provista. Si Eficiencia >= Meta, el problema son los Paros (Menciona causas). Si Eficiencia < Meta, el problema son las Microparadas (Menciona a los operarios listados).\n"
        "Sé muy ejecutivo y usa emojis como ⚠️ o 🛑."
    )
    user_prompt = f"Meta: {meta}% | Real: {prod:.1f}% | Eficiencia: {efi:.1f}%\nDiagnóstico Matemático (Usa estos nombres): {math_diag}"

    try:
        if _oa_client is None: return _sanitize_pdf_text(math_diag)
        chat = _oa_client.chat.completions.create(
            model="gpt-4o-mini", temperature=0.3,
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}]
        )
        return _sanitize_pdf_text(chat.choices[0].message.content.strip())
    except:
        return _sanitize_pdf_text(math_diag)

def _get_ai_global_roadmap(df: pd.DataFrame, summary: pd.DataFrame, manager_name: str) -> str:
    """Genera el resumen general estratégico y el mapa de ruta para el final del PDF."""
    # Forzar el nombre a Wilson
    nombre_pila = "Wilson"
    
    cumplen = summary[summary['cumple_meta']]['Linea'].tolist()
    no_cumplen = summary[~summary['cumple_meta']]['Linea'].tolist()
    
    tp_global = df.groupby('cause')['tp'].sum().sort_values(ascending=False).head(3)
    causas_txt = ", ".join([f"{k} ({v:.1f}h)" for k, v in tp_global.items()])

    ctx = (
        f"Líneas OK: {', '.join(cumplen) if cumplen else 'Ninguna'}.\n"
        f"Líneas Críticas: {', '.join(no_cumplen) if no_cumplen else 'Ninguna'}.\n"
        f"Principales agujeros de tiempo de toda la planta: {causas_txt}."
    )

    sys_prompt = (
        f"Eres CiplasBot, Asesor Estratégico. Escribe el cierre del reporte ejecutivo para el Gerente {nombre_pila}.\n"
        "ESTRUCTURA OBLIGATORIA:\n"
        "1. Estado General del Proceso: Resumen rápido de lo que funciona y lo que falla a nivel macro.\n"
        "2. Preguntas de Reflexión: Plantea 2 preguntas estratégicas y orientadas sobre las tendencias que notas (ej. relación entre tiempos muertos en múltiples áreas).\n"
        "3. Mapa de Ruta para Mejorar: Da 3 pasos concretos que el gerente puede tomar mañana mismo para enderezar el rumbo.\n"
        "Usa emojis (🚀, 💡, 🔍, 📊) para separar."
    )
    
    try:
        if _oa_client is None: return _sanitize_pdf_text("Análisis global no disponible.")
        chat = _oa_client.chat.completions.create(
            model="gpt-4o-mini", temperature=0.4,
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": ctx}]
        )
        return _sanitize_pdf_text(chat.choices[0].message.content.strip())
    except:
        return _sanitize_pdf_text("Análisis global no disponible temporalmente.")

def _plot_global_downtime(df: pd.DataFrame, month: int, year: int) -> str | None:
    g = df.groupby('cause')['tp'].sum().sort_values(ascending=False).head(8)
    if g.empty or g.sum() == 0: return None
    
    fig, ax = plt.subplots(figsize=(10, 4), dpi=130)
    y_pos = np.arange(len(g))
    ax.barh(y_pos, g.values, align='center', color='darkred')
    ax.set_yticks(y_pos)
    ax.set_yticklabels(g.index.astype(str))
    ax.invert_yaxis()
    ax.set_xlabel('Horas Totales Perdidas (Toda la Planta)')
    ax.set_title(f"Agujeros Negros de Productividad Transversales ({year}-{month:02d})")
    ax.grid(axis="x", linestyle="--", alpha=0.5)
    fig.tight_layout()
    img_path = os.path.join(REPORTS_DIR, f"pareto_planta_{year}{month}.png")
    fig.savefig(img_path, bbox_inches="tight")
    plt.close(fig)
    return img_path

# =========================
# PDF BUILDER
# =========================
def _build_planta_pdf(df: pd.DataFrame, summary: pd.DataFrame, ai_line_texts: dict, global_ai_text: str, img_pareto: str, month: int, year: int, name: str) -> str:
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    page_w = pdf.w - 2 * pdf.l_margin

    # 1. PORTADA Y RESUMEN
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(0, 51, 102)
    pdf.cell(0, 15, "INFORME MACRO-GERENCIAL DE PLANTA", ln=1, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(50, 50, 50)
    pdf.cell(0, 6, f"Periodo Analizado: {year}-{month:02d} | Generado para: {name}", ln=1, align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 8, "1. Rendimiento Global por Líneas", ln=1)
    
    pdf.set_fill_color(220, 230, 241)
    pdf.set_font("Helvetica", "B", 9)
    w_l, w_num = 35, 25
    pdf.cell(w_l, 6, "Proceso", border=1, fill=True)
    pdf.cell(w_num, 6, "Producción", border=1, fill=True, align="C")
    pdf.cell(w_num, 6, "Meta %", border=1, fill=True, align="C")
    pdf.cell(w_num, 6, "% Prod.", border=1, fill=True, align="C")
    pdf.cell(w_num, 6, "% Efic.", border=1, fill=True, align="C")
    pdf.cell(w_num, 6, "Estado", border=1, fill=True, align="C", ln=1)

    pdf.set_font("Helvetica", "", 9)
    for _, r in summary.iterrows():
        pdf.cell(w_l, 6, str(r['Linea']), border=1)
        pdf.cell(w_num, 6, f"{r['unidades']:,.0f} {r['Unidad_Medida']}", border=1, align="R")
        pdf.cell(w_num, 6, f"{r['Meta_Prod']}%", border=1, align="C")
        pdf.cell(w_num, 6, f"{r['prod_pct']:.1f}%", border=1, align="R")
        pdf.cell(w_num, 6, f"{r['efi_pct']:.1f}%", border=1, align="R")
        
        if r['cumple_meta']:
            pdf.set_text_color(0, 128, 0)
            pdf.cell(w_num, 6, "OK", border=1, align="C", ln=1)
        else:
            pdf.set_text_color(204, 0, 0)
            pdf.cell(w_num, 6, "ALERTA", border=1, align="C", ln=1)
        pdf.set_text_color(0, 0, 0)
    pdf.ln(8)

    # 2. LÍNEAS CRÍTICAS (TABLA + DIAGNÓSTICO IA DEBAJO)
    criticas = summary[~summary['cumple_meta']]
    if not criticas.empty:
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 8, "2. Focos Rojos: Análisis de Máquinas Críticas", ln=1)
        
        for _, r in criticas.iterrows():
            if pdf.get_y() > 200: pdf.add_page()
            line_name = r['Linea']
            unit_label = r['Unidad_Medida']
            
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_fill_color(255, 230, 230)
            pdf.cell(0, 6, f"  Línea: {line_name} (Meta: {r['Meta_Prod']}%)", ln=1, fill=True)
            
            df_line = df[df['Linea'] == line_name]
            df_m = _analyze_line_machines(df_line)
            
            pdf.set_font("Helvetica", "B", 8)
            pdf.cell(30, 5, "Máquina", border=1)
            pdf.cell(25, 5, f"Cant. ({unit_label})", border=1, align="C")
            pdf.cell(25, 5, "T. Corrida", border=1, align="C")
            pdf.cell(25, 5, "T. Perdido", border=1, align="C")
            pdf.cell(25, 5, "% Prod.", border=1, align="C")
            pdf.cell(25, 5, "% Efic.", border=1, align="C", ln=1)

            pdf.set_font("Helvetica", "", 8)
            for _, m in df_m.iterrows():
                pdf.cell(30, 5, str(m['maq'])[:15], border=1)
                pdf.cell(25, 5, f"{m['unidades']:,.0f}", border=1, align="R")
                pdf.cell(25, 5, f"{m['tc']:.1f}h", border=1, align="R")
                pdf.cell(25, 5, f"{m['tp']:.1f}h", border=1, align="R")
                pdf.cell(25, 5, f"{m['prod_pct']:.1f}%", border=1, align="R")
                pdf.cell(25, 5, f"{m['efi_pct']:.1f}%", border=1, align="R", ln=1)
            
            # IMPRIMIR EL DIAGNÓSTICO IA JUSTO DEBAJO DEL CUADRO
            if line_name in ai_line_texts:
                pdf.ln(2)
                pdf.set_font("Helvetica", "I", 9)
                pdf.set_text_color(100, 50, 50) # Tono rojizo para las alertas
                pdf.multi_cell(0, 5, _sanitize_pdf_text(ai_line_texts[line_name]))
                pdf.set_text_color(0, 0, 0)
            pdf.ln(5)

    # 3. MAPA GLOBAL DE TIEMPOS PERDIDOS
    if pdf.get_y() > 180: pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "3. Mapa Transversal de Tiempos Perdidos", ln=1)
    if img_pareto and os.path.exists(img_pareto):
        pdf.image(img_pareto, w=page_w)
        pdf.ln(5)

    # 4. RANKINGS DE OPERARIOS Y ALERTAS (10% debajo de la meta)
    if pdf.get_y() > 140: pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "4. Desempeño del Capital Humano", ln=1)
    
    # Agrupamos operarios trayendo también su Meta_Prod
    ops = df.groupby(['name', 'Linea', 'Meta_Prod']).agg({'cs':'sum', 'tc':'sum', 'tp':'sum'}).reset_index()
    ops['prod_pct'] = np.where((ops['tc']+ops['tp'])>0, (ops['cs']/(ops['tc']+ops['tp']))*100, 0)
    ops = ops[(ops['tc']+ops['tp']) > 1.0] # Eliminar datos fantasma/vacíos
    ops['name'] = ops['name'].apply(_clean_names)
    
    ops_sorted = ops.sort_values('prod_pct', ascending=False)
    top10 = ops_sorted.head(10).reset_index(drop=True)
    bot10 = ops_sorted[ops_sorted['prod_pct']>0].tail(10).sort_values('prod_pct', ascending=True).reset_index(drop=True)

    pdf.set_fill_color(220, 240, 220)
    pdf.set_font("Helvetica", "B", 8)
    pdf.cell(page_w/2, 6, "TOP 10 PLANTA", border=1, align="C", fill=True)
    pdf.set_fill_color(255, 230, 230)
    pdf.cell(page_w/2, 6, "BOTTOM 10 PLANTA", border=1, align="C", fill=True, ln=1)

    pdf.set_font("Helvetica", "", 7)
    for i in range(10):
        t_txt = f"{top10.iloc[i]['name']} | {top10.iloc[i]['Linea'][:6]} | {top10.iloc[i]['prod_pct']:.1f}%" if i < len(top10) else ""
        b_txt = f"{bot10.iloc[i]['name']} | {bot10.iloc[i]['Linea'][:6]} | {bot10.iloc[i]['prod_pct']:.1f}%" if i < len(bot10) else ""
        pdf.cell(page_w/2, 5, _sanitize_pdf_text(t_txt), border=1)
        pdf.cell(page_w/2, 5, _sanitize_pdf_text(b_txt), border=1, ln=1)
    pdf.ln(8)

    # NUEVO: ALERTAS DE OPERARIOS (10% por debajo de su meta de línea)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Atención Prioritaria: Operarios >10% por debajo del objetivo", ln=1)
    
    ops_criticos = ops[ops['prod_pct'] <= (ops['Meta_Prod'] - 10.0)].sort_values(['Linea', 'prod_pct'])
    
    if not ops_criticos.empty:
        pdf.set_fill_color(255, 200, 200)
        pdf.set_font("Helvetica", "B", 8)
        w_op, w_lin, w_mt, w_pr = 60, 40, 25, 25
        pdf.cell(w_op, 5, "Operario", border=1, fill=True)
        pdf.cell(w_lin, 5, "Proceso", border=1, fill=True)
        pdf.cell(w_mt, 5, "Meta Línea", border=1, fill=True, align="C")
        pdf.cell(w_pr, 5, "% Logrado", border=1, fill=True, align="C", ln=1)
        
        pdf.set_font("Helvetica", "", 8)
        for _, r in ops_criticos.iterrows():
            pdf.cell(w_op, 5, _sanitize_pdf_text(r['name']), border=1)
            pdf.cell(w_lin, 5, _sanitize_pdf_text(r['Linea']), border=1)
            pdf.cell(w_mt, 5, f"{r['Meta_Prod']}%", border=1, align="C")
            pdf.cell(w_pr, 5, f"{r['prod_pct']:.1f}%", border=1, align="C", ln=1)
    else:
        pdf.set_font("Helvetica", "I", 9)
        pdf.cell(0, 6, "Excelente: Ningún operario presenta un desvío crítico este mes.", ln=1)
    pdf.ln(10)

    # 5. RESUMEN ESTRATÉGICO FINAL IA
    if global_ai_text:
        if pdf.get_y() > 200: pdf.add_page()
        pdf.set_font("Helvetica", "B", 14)
        pdf.set_text_color(0, 102, 51)
        pdf.cell(0, 8, f">> Mapa de Ruta Estratégico (CiplasBot)", ln=1)
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, _sanitize_pdf_text(global_ai_text))

    out_path = os.path.join(REPORTS_DIR, f"Macro_Gerencial_{_slug(name)}_{year}{month:02d}.pdf")
    pdf.output(out_path)
    return out_path

# =========================
# CONTROLADOR PRINCIPAL
# =========================
def handle_manager_planta_report(phone: str, manager_name: str, to_norm: str):
    send_whatsapp_message(to_norm, f"🌐 Procesando Macro-Data de la Planta y análisis por IA... Esto tomará unos segundos. ⏳")
    
    df, month, year = _download_all_lines()
    if df.empty:
        send_whatsapp_message(to_norm, "❌ Error: No se pudo consolidar la data.")
        return True
        
    summary = _build_global_summary(df)
    
    # Evaluar líneas críticas y generar diagnósticos puntuales
    ai_line_texts = {}
    criticas = summary[~summary['cumple_meta']]
    
    for _, r in criticas.iterrows():
        line_name = r['Linea']
        meta = r['Meta_Prod']
        efi = r['efi_pct']
        prod = r['prod_pct']
        
        df_line = df[df['Linea'] == line_name]
        if efi >= meta:
            causas = df_line.groupby('cause')['tp'].sum().sort_values(ascending=False).head(3)
            txt_c = ", ".join([f"{k} ({v:.1f}h)" for k,v in causas.items()])
            math_diag = f"Eficiencia matemática ({efi:.1f}%) supera la meta. El problema son los Tiempos Perdidos. Causas: {txt_c}."
        else:
            ops = df_line.groupby('name').agg({'cs':'sum', 'tc':'sum'})
            ops['efi'] = np.where(ops['tc']>0, (ops['cs']/ops['tc'])*100, 0)
            peores_ops = ops[ops['tc']>0].sort_values('efi').head(3)
            txt_o = ", ".join([f"{_clean_names(k)} ({v['efi']:.1f}%)" for k,v in peores_ops.iterrows()])
            math_diag = f"Eficiencia matemática ({efi:.1f}%) por debajo de la meta. El problema son Microparadas/Operarios. Peores operarios: {txt_o}."
            
        ai_line_texts[line_name] = _get_ai_line_diagnosis(line_name, meta, prod, efi, math_diag, manager_name)

    # Evaluar contexto global para mapa de ruta
    global_ai_text = _get_ai_global_roadmap(df, summary, manager_name)
    img_pareto = _plot_global_downtime(df, month, year)
    
    pdf_path = _build_planta_pdf(df, summary, ai_line_texts, global_ai_text, img_pareto, month, year, manager_name)
    
    try:
        send_whatsapp_document(to_norm, pdf_path, caption="🏢 Informe Macro-Gerencial de Planta finalizado.")
        if img_pareto: os.remove(img_pareto)
        os.remove(pdf_path)
    except Exception as e:
        send_whatsapp_message(to_norm, "❌ Hubo un error enviando el PDF gerencial.")
    
    return True