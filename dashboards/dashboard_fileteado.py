import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np
import re
import unicodedata

# 🎨 Estilo oscuro
mpl.style.use('dark_background')
st.set_page_config(layout="wide", page_title="🏠 Dashboard Fileteado", page_icon="🭝")

# 🧼 Estilos CSS
st.markdown("""
    <style>
        html, body, [data-testid="stApp"] { background-color: #000000; color: white; }
        div[data-testid="stSidebar"] { background-color: #1e1e1e; }
        h1, h2, h3, h4, p, label { color: white !important; }
        .styled-table { border-collapse: collapse; margin: 0; font-size: 12px; }
        .styled-table th, .styled-table td { border: 1px solid #444; padding: 4px 8px; text-align: center; }
        .styled-table thead tr { background-color: #333; }
    </style>
""", unsafe_allow_html=True)

# 🏷️ Título
st.markdown("<h1>📌 Dashboard ejecutivo fileteado</h1>", unsafe_allow_html=True)
st.markdown(f"<p>🗓 Informe generado el {datetime.now().strftime('%d/%m/%Y')}</p>", unsafe_allow_html=True)

# ——————————————————————————————————————
# Utilidades de normalización y búsqueda
# ——————————————————————————————————————
def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s.lower()).strip("_")
    return re.sub(r"_+", "_", s)

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [slug(c) for c in df.columns]
    return df

def first_col(df: pd.DataFrame, candidates) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
    return None

def to_numeric_safe(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)

def blues_colors(n: int):
    cmap = plt.cm.Blues
    return [cmap(x) for x in np.linspace(0.35, 0.9, max(n, 1))]

# ——————————————————————————————————————
# Carga de datos
# ——————————————————————————————————————
@st.cache_data(ttl=300)
def load_excel(url: str):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return pd.read_excel(io.BytesIO(r.content), sheet_name=None)

url = "https://docs.google.com/spreadsheets/d/1FYLgfQhLvCUtiuxGnn5aQK6aCChoFPmMU-eMa0KAHrg/export?format=xlsx"

try:
    sheets = load_excel(url)
except Exception as e:
    st.error(f"❌ Error al descargar/leer el archivo: {e}")
    st.stop()

if "Fileteado" not in sheets:
    st.error("❌ No se encontró la hoja 'Fileteado'.")
    st.stop()

df = normalize_columns(sheets["Fileteado"])

# Mapear columnas posibles
col_fecha   = first_col(df, ["fecha_efectiva", "fecha", "fecha_de_registro"])
col_turno   = first_col(df, ["turno"])
col_maquina = first_col(df, ["maquina"])
col_artic   = first_col(df, ["numero_articulo", "numero_de_articulo", "num_articulo", "referencia"])
col_oper    = first_col(df, ["apellidos_nombres", "operario", "nombre", "nombres"])
col_prod    = first_col(df, ["cantidad_completada", "cant_kg", "cantidad_kg", "cantidad", "unidades"])
col_corr    = first_col(df, ["tiempo_corrida", "tiempo_real_de_corrida", "horas_corrida"])
col_causa   = first_col(df, ["causa_paro", "descripcion_razon", "descripcion_causa", "razon"])
col_tperd   = first_col(df, ["tiempo_perdido", "horas_perdidas", "tiempo_paro"])

required = [col_fecha, col_maquina, col_prod]
if any(c is None for c in required):
    st.error(f"❌ Faltan columnas esenciales. Detectadas: fecha={col_fecha}, maquina={col_maquina}, produccion={col_prod}")
    st.stop()

# Tipos y limpieza
df[col_fecha] = pd.to_datetime(df[col_fecha], errors="coerce")
df = df[df[col_fecha].notna()].copy()

if col_turno is None:
    df["turno_norm"] = ""
else:
    df["turno_norm"] = df[col_turno].astype(str).str.upper()

if col_artic is None:
    df["art_norm"] = ""
else:
    df["art_norm"] = df[col_artic].astype(str).str.strip().str.lower()

df["prod_norm"] = to_numeric_safe(df[col_prod])
df["corr_norm"] = to_numeric_safe(df[col_corr]) if col_corr else 0.0
df["tperd_norm"] = to_numeric_safe(df[col_tperd]) if col_tperd else 0.0

if col_causa is None:
    df["causa_norm"] = ""
else:
    df["causa_norm"] = df[col_causa].astype(str)

if col_oper is None:
    df["oper_norm"] = ""
else:
    df["oper_norm"] = df[col_oper].astype(str)

# Clasificación robusta de línea
def clasificar_linea(maquina: str, articulo: str) -> str:
    m = str(maquina).lower()
    a = str(articulo).lower()
    if "koom" in m or ("auto" in m and "7" in m):
        return "Auto7"
    if m.startswith("fipla") or "plana" in m or "fipla" in m:
        return "planas"
    if m.startswith("cort") or "corte" in m:
        return "corte_gasa"
    if "filete" in m or "filet" in m:
        if a.startswith("cag"):
            return "filete_gasa"
        if a.startswith("len"):
            return "filete_leno"
        return "filete_otras"
    return "otros"

df["linea"] = [clasificar_linea(m, a) for m, a in zip(df[col_maquina], df["art_norm"])]

# Filtrar artículo “ESP00001” si existe esa columna
if col_artic:
    df = df[df["art_norm"] != "esp00001"].copy()

# ——————————————————————————————————————
# Filtros UI
# ——————————————————————————————————————
c1, c2, c3, c4 = st.columns([2, 1, 1, 1])

with c1:
    fechas_unicas = sorted(pd.to_datetime(df[col_fecha].dt.date.unique()))
    if not fechas_unicas:
        st.info("No hay fechas válidas.")
        st.stop()
    date_sel = st.date_input("🔕 Selecciona fecha o rango:", value=(fechas_unicas[0], fechas_unicas[-1]))
    f_ini, f_fin = date_sel if isinstance(date_sel, tuple) else (date_sel, date_sel)

with c2:
    linea_sel = st.selectbox("🏠 Línea de producción:", ["Todas"] + sorted(df["linea"].unique()))

with c3:
    turno_sel = st.selectbox("👨‍💼 Turno:", ["Todos"] + sorted(df["turno_norm"].dropna().unique()))

with c4:
    op_list = ["Todos"] + sorted(df["oper_norm"].dropna().unique())
    oper_sel = st.selectbox("🧍 Operario:", op_list)

# Aplicar filtros
dff = df[
    (df[col_fecha].dt.date >= f_ini) &
    (df[col_fecha].dt.date <= f_fin)
].copy()

if linea_sel != "Todas":
    dff = dff[dff["linea"] == linea_sel]
if turno_sel != "Todos":
    dff = dff[dff["turno_norm"] == turno_sel]
if oper_sel != "Todos":
    dff = dff[dff["oper_norm"] == oper_sel]

if dff.empty:
    st.info("ℹ️ No hay datos para el filtro aplicado.")
    st.stop()

# ——————————————————————————————————————
# Producción y paros
# ——————————————————————————————————————
st.markdown("<h4>📊 Producción y causas de paro</h4>", unsafe_allow_html=True)
g1, g2, g3 = st.columns(3)

with g1:
    st.markdown("<h5>🔄 Distribución por línea</h5>", unsafe_allow_html=True)
    dist = dff.groupby("linea")["prod_norm"].sum().sort_values(ascending=True)
    if dist.empty:
        st.info("Sin datos de producción por línea.")
    else:
        fig, ax = plt.subplots(figsize=(5, 3.5))
        dist.plot(kind="barh", ax=ax, color=blues_colors(len(dist)))
        ax.set_xlabel("Cantidad", color="white"); ax.set_title("Distribución por línea", color="white")
        ax.tick_params(colors="white"); fig.tight_layout(); st.pyplot(fig)

with g2:
    st.markdown("<h5>📈 Producción diaria por línea</h5>", unsafe_allow_html=True)
    foco = dff[dff["linea"].isin(["planas", "filete_gasa", "filete_leno"])]
    if foco.empty:
        st.info("Sin datos para las líneas seleccionadas en el rango elegido.")
    else:
        g = foco.groupby([foco[col_fecha].dt.date, "linea"])["prod_norm"].sum().unstack(fill_value=0).sort_index()
        g = g.apply(pd.to_numeric, errors="coerce").fillna(0.0)
        if g.empty or g.select_dtypes(include="number").shape[1] == 0:
            st.info("No hay datos numéricos para graficar.")
        else:
            fig, ax = plt.subplots(figsize=(5, 3.5))
            g.plot(kind="bar", stacked=True, ax=ax, color=blues_colors(len(g.columns)))
            ax.set_ylabel("Unidades", color="white"); ax.set_xlabel("Fecha", color="white")
            ax.set_title("Producción diaria por línea", color="white")
            ax.tick_params(colors="white"); ax.legend(loc="upper left", fontsize=6)
            fig.tight_layout(); st.pyplot(fig)

with g3:
    st.markdown("<h5>⏱ Tiempo de corrida por día y línea</h5>", unsafe_allow_html=True)
    if "corr_norm" not in dff.columns or (dff["corr_norm"].sum() == 0):
        st.info("Sin datos de tiempo de corrida en el rango elegido.")
    else:
        foco = dff[dff["linea"].isin(["planas", "filete_gasa", "filete_leno"])]
        c = foco.groupby([foco[col_fecha].dt.date, "linea"])["corr_norm"].sum().unstack(fill_value=0).sort_index()
        c = c.apply(pd.to_numeric, errors="coerce").fillna(0.0)
        if c.empty or c.select_dtypes(include="number").shape[1] == 0:
            st.info("No hay datos numéricos para graficar tiempos.")
        else:
            fig, ax = plt.subplots(figsize=(5, 3.5))
            c.plot(kind="bar", stacked=True, ax=ax, color=blues_colors(len(c.columns)))
            ax.set_ylabel("Horas", color="white"); ax.set_xlabel("Fecha", color="white")
            ax.set_title("Tiempo de corrida diario", color="white")
            ax.tick_params(colors="white"); ax.legend(loc="upper left", fontsize=6)
            fig.tight_layout(); st.pyplot(fig)

# ——————————————————————————————————————
# Top / Bajo desempeño
# ——————————————————————————————————————
st.markdown("<h4>🏆 Mejores operarios por línea (Sacos/Hora)</h4>", unsafe_allow_html=True)
cols_top = st.columns(3)
lineas_top = ["filete_gasa", "filete_leno", "planas"]
titulos = ["🥇 Filete Gasa", "🥈 Filete Leno", "🥉 Plana"]

for i, linea in enumerate(lineas_top):
    with cols_top[i]:
        st.markdown(f"<h5 style='text-align:center'>{titulos[i]}</h5>", unsafe_allow_html=True)
        t = dff[dff["linea"] == linea]
        if t.empty:
            st.info("Sin datos.")
            continue
        agg = t.groupby("oper_norm", dropna=True).agg(prod=("prod_norm","sum"), corr=("corr_norm","sum")).reset_index()
        agg["sacos/hora"] = agg["prod"].div(agg["corr"].replace(0, pd.NA)).fillna(0.0)
        tabla = agg[["oper_norm","sacos/hora"]].sort_values("sacos/hora", ascending=False).head(5).rename(columns={"oper_norm":"Operario"})
        st.dataframe(tabla.style.set_table_attributes('class="styled-table"').format({"sacos/hora":"{:.2f}"}), use_container_width=True, hide_index=True)

st.markdown("<h4>❌ Operarios a revisar desempeño (Sacos/Hora)</h4>", unsafe_allow_html=True)
cols_low = st.columns(3)
titulos_low = ["⛔ Filete Gasa", "⛔ Filete Leno", "⛔ Plana"]

for i, linea in enumerate(lineas_top):
    with cols_low[i]:
        st.markdown(f"<h5 style='text-align:center'>{titulos_low[i]}</h5>", unsafe_allow_html=True)
        t = dff[dff["linea"] == linea]
        if t.empty:
            st.info("Sin datos.")
            continue
        agg = t.groupby("oper_norm", dropna=True).agg(prod=("prod_norm","sum"), corr=("corr_norm","sum")).reset_index()
        agg["sacos/hora"] = agg["prod"].div(agg["corr"].replace(0, pd.NA)).fillna(0.0)
        tabla = agg[["oper_norm","sacos/hora"]].sort_values("sacos/hora", ascending=True).head(10).rename(columns={"oper_norm":"Operario"})
        st.dataframe(tabla.style.set_table_attributes('class="styled-table"').format({"sacos/hora":"{:.2f}"}), use_container_width=True, hide_index=True)

# ——————————————————————————————————————
# Causas de tiempo perdido
# ——————————————————————————————————————
st.markdown("<h4>⏳ Causas de tiempo perdido por línea</h4>", unsafe_allow_html=True)
lineas_obj = ["filete_gasa", "filete_leno", "planas", "Auto7", "corte_gasa"]

for i in range(0, len(lineas_obj), 2):
    cols = st.columns(2)
    for j in range(2):
        if i + j >= len(lineas_obj): break
        linea = lineas_obj[i + j]
        with cols[j]:
            t = dff[dff["linea"] == linea].copy()
            if col_causa is None or col_tperd is None or t.empty or t["tperd_norm"].sum() == 0:
                st.markdown(f"<p style='text-align:center; font-size: 9px; color: gray;'>Sin datos</p>", unsafe_allow_html=True)
                continue
            causas = t.groupby("causa_norm")["tperd_norm"].sum().sort_values(ascending=True).tail(5)
            causas.index = [c[:20] + "…" if isinstance(c, str) and len(c) > 20 else c for c in causas.index]
            fig, ax = plt.subplots(figsize=(3, 2))
            causas.plot(kind="barh", ax=ax, color=blues_colors(len(causas)))
            ax.set_title(linea.upper(), color="white", fontsize=9)
            ax.set_xlabel("Horas", color="white", fontsize=8)
            ax.set_ylabel(""); ax.tick_params(colors="white", labelsize=7)
            fig.tight_layout(pad=0.3); st.pyplot(fig)

# ——————————————————————————————————————
# Pie
# ——————————————————————————————————————
st.markdown("""
<hr style="border:1px solid #444;">
<p style="text-align:center; font-size:14px; color:gray;">
    🤖 Generado por <strong>CiplasBot</strong> - creado por Ing. Wilson Calderón
</p>
""", unsafe_allow_html=True)
