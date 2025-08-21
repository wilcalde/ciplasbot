# dashboard.py
import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib as mpl
import numpy as np

# 🎨 Estilo oscuro
mpl.style.use('dark_background')
st.set_page_config(layout="wide", page_title="🏠 Dashboard Fileteado", page_icon="🭝")

# 🧼 Estilos CSS
st.markdown("""
    <style>
        html, body, [data-testid="stApp"] {
            background-color: #000000;
            color: white;
        }
        div[data-testid="stSidebar"] {
            background-color: #1e1e1e;
        }
        h1, h2, h3, h4, p, label {
            color: white !important;
        }
        .dataframe {
            background-color: #000000;
            color: white;
            border: none;
        }
        .styled-table {
            border-collapse: collapse;
            margin: 0;
            font-size: 12px;
        }
        .styled-table th, .styled-table td {
            border: 1px solid #444;
            padding: 4px 8px;
            text-align: center;
        }
        .styled-table thead tr {
            background-color: #333;
        }
    </style>
""", unsafe_allow_html=True)

# 🏷️ Título
st.markdown("<h1>📌 Dashboard ejecutivo fileteado</h1>", unsafe_allow_html=True)
fecha_actual = datetime.now().strftime("%d/%m/%Y")
st.markdown(f"<p>🗓 Informe generado el {fecha_actual}</p>", unsafe_allow_html=True)

# 📥 Descargar archivo desde Google Sheets (caché 5 min)
@st.cache_data(ttl=300)
def load_excel_from_gsheet(gsheet_export_url: str):
    resp = requests.get(gsheet_export_url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Error HTTP {resp.status_code}")
    excel_data = io.BytesIO(resp.content)
    dfs = pd.read_excel(excel_data, sheet_name=None)
    return dfs

url = 'https://docs.google.com/spreadsheets/d/1FYLgfQhLvCUtiuxGnn5aQK6aCChoFPmMU-eMa0KAHrg/export?format=xlsx'

try:
    dfs = load_excel_from_gsheet(url)
except Exception as e:
    st.error(f"❌ Error al descargar/leer el archivo: {e}")
    st.stop()

if "Fileteado" not in dfs:
    st.error("❌ No se encontró la hoja 'Fileteado' en el archivo.")
    st.stop()

# =========================
#   PREPROCESAMIENTO
# =========================
df = dfs["Fileteado"].copy()

# Fechas
if 'Fecha_Efectiva' not in df.columns:
    st.error("❌ Falta la columna 'Fecha_Efectiva'.")
    st.stop()
df['Fecha_Efectiva'] = pd.to_datetime(df['Fecha_Efectiva'], errors='coerce')

# Limpieza básica
if 'Numero_Articulo' in df.columns:
    df = df[df["Numero_Articulo"] != "ESP00001"].copy()

# Normalización de turno
if 'Turno' in df.columns:
    df['Turno'] = df['Turno'].astype(str).str.upper()
else:
    df['Turno'] = ""

# Conversión robusta a numérico
for col in ['Cantidad_Completada', 'Tiempo_Corrida', 'Tiempo_Perdido']:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    else:
        # Si no existe, crearla en cero para no romper filtros/gráficos
        df[col] = 0.0

# Clasificación de líneas
def clasificar_maquina(row):
    maquina = str(row.get('Maquina', '')).lower()
    articulo = str(row.get('Numero_Articulo', '')).lower()
    if 'koom2000' in maquina:
        return 'Auto7'
    elif maquina in ['cort1', 'cort2', 'cort3', 'cort5']:
        return 'corte_gasa'
    elif maquina.startswith('fipla'):
        return 'planas'
    elif maquina.startswith('filet') and articulo.startswith('cag'):
        return 'filete_gasa'
    elif maquina.startswith('filet') and articulo.startswith('len'):
        return 'filete_leno'
    else:
        return 'otros'

df['Linea'] = df.apply(clasificar_maquina, axis=1)

# =========================
#        FILTROS
# =========================
col1, col2, col3, col4 = st.columns([2, 1, 1, 1])

with col1:
    fechas_unicas = sorted(pd.to_datetime(df['Fecha_Efectiva'].dt.date.unique()))
    if not fechas_unicas:
        st.error("❌ No hay fechas válidas en los datos.")
        st.stop()
    fecha_rango = st.date_input("🔕 Selecciona fecha o rango:", value=(fechas_unicas[0], fechas_unicas[-1]))
    fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)

with col2:
    lineas_disponibles = ["Todas"] + sorted(df['Linea'].dropna().unique())
    linea_seleccionada = st.selectbox("🏠 Línea de producción:", lineas_disponibles)

with col3:
    turnos_disponibles = ["Todos"] + sorted(df['Turno'].dropna().unique())
    turno_seleccionado = st.selectbox("👨‍💼 Turno:", turnos_disponibles)

with col4:
    operarios_disponibles = ["Todos"] + sorted(df.get('Apellidos_Nombres', pd.Series([], dtype=str)).dropna().unique())
    operario_seleccionado = st.selectbox("🧍 Operario:", operarios_disponibles)

# Aplicar filtros
df_filtrado = df[
    (df['Fecha_Efectiva'].dt.date >= fecha_inicio) &
    (df['Fecha_Efectiva'].dt.date <= fecha_fin)
].copy()

if linea_seleccionada != "Todas":
    df_filtrado = df_filtrado[df_filtrado['Linea'] == linea_seleccionada]

if turno_seleccionado != "Todos":
    df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

if operario_seleccionado != "Todos":
    df_filtrado = df_filtrado[df_filtrado['Apellidos_Nombres'] == operario_seleccionado]

if df_filtrado.empty:
    st.info("ℹ️ No hay datos para el filtro aplicado.")
    st.stop()

# Utilidad: paleta Blues (n colores)
def blues_colors(n):
    cmap = plt.cm.Blues
    # Evitamos tonos muy claros (inicio) para visibilidad en tema oscuro
    return [cmap(x) for x in np.linspace(0.35, 0.9, max(n, 1))]

# =========================
#   PRODUCCIÓN Y PAROS
# =========================
st.markdown("<h4>📊 Producción y causas de paro</h4>", unsafe_allow_html=True)

col_g1, col_g2, col_g3 = st.columns(3)

# --- Distribución por línea
with col_g1:
    st.markdown("<h5>🔄 Distribución por línea</h5>", unsafe_allow_html=True)
    resumen_linea = (
        df_filtrado.groupby('Linea')['Cantidad_Completada']
        .sum()
        .sort_values(ascending=True)
    )
    if resumen_linea.empty:
        st.info("Sin datos de producción por línea.")
    else:
        fig_linea, ax_linea = plt.subplots(figsize=(5, 3.5))
        resumen_linea.plot(kind='barh', ax=ax_linea, color=blues_colors(len(resumen_linea)))
        ax_linea.set_xlabel("Cantidad", color="white")
        ax_linea.set_title("Distribución por línea", color="white")
        ax_linea.tick_params(colors='white')
        fig_linea.tight_layout()
        st.pyplot(fig_linea)

# --- Producción diaria por línea (stacked)
with col_g2:
    st.markdown("<h5>📈 Producción diaria por línea</h5>", unsafe_allow_html=True)
    df_graf = df_filtrado[df_filtrado['Linea'].isin(['planas', 'filete_gasa', 'filete_leno'])].copy()

    if df_graf.empty:
        st.info("Sin datos para las líneas seleccionadas en el rango elegido.")
    else:
        grafico = (
            df_graf
            .groupby([df_graf['Fecha_Efectiva'].dt.date, 'Linea'])['Cantidad_Completada']
            .sum()
            .unstack(fill_value=0)
            .sort_index()
        )
        grafico = grafico.apply(pd.to_numeric, errors='coerce').fillna(0.0)

        if grafico.empty or grafico.select_dtypes(include='number').shape[1] == 0:
            st.info("No hay datos numéricos para graficar.")
        else:
            colors = blues_colors(len(grafico.columns))
            fig, ax = plt.subplots(figsize=(5, 3.5))
            grafico.plot(kind='bar', stacked=True, ax=ax, color=colors)
            ax.set_ylabel("Unidades", color="white")
            ax.set_xlabel("Fecha", color="white")
            ax.set_title("Producción diaria por línea", color="white")
            ax.tick_params(colors='white')
            ax.legend(loc='upper left', fontsize=6)
            fig.tight_layout()
            st.pyplot(fig)

# --- Tiempo de corrida por día y línea (stacked)
with col_g3:
    st.markdown("<h5>⏱ Tiempo de corrida por día y línea</h5>", unsafe_allow_html=True)
    df_corrida = df_filtrado[df_filtrado['Linea'].isin(['planas', 'filete_gasa', 'filete_leno'])].copy()

    if df_corrida.empty:
        st.info("Sin datos de tiempo de corrida en el rango elegido.")
    else:
        corrida = (
            df_corrida
            .groupby([df_corrida['Fecha_Efectiva'].dt.date, 'Linea'])['Tiempo_Corrida']
            .sum()
            .unstack(fill_value=0)
            .sort_index()
        )
        corrida = corrida.apply(pd.to_numeric, errors='coerce').fillna(0.0)

        if corrida.empty or corrida.select_dtypes(include='number').shape[1] == 0:
            st.info("No hay datos numéricos para graficar tiempos.")
        else:
            colors_corrida = blues_colors(len(corrida.columns))
            fig_corrida, ax_corrida = plt.subplots(figsize=(5, 3.5))
            corrida.plot(kind='bar', stacked=True, ax=ax_corrida, color=colors_corrida)
            ax_corrida.set_ylabel("Horas", color="white")
            ax_corrida.set_xlabel("Fecha", color="white")
            ax_corrida.set_title("Tiempo de corrida diario", color="white")
            ax_corrida.tick_params(colors='white')
            ax_corrida.legend(loc='upper left', fontsize=6)
            fig_corrida.tight_layout()
            st.pyplot(fig_corrida)

# =========================
#     TOP / BAJO DESEMPEÑO
# =========================
st.markdown("<h4>🏆 Mejores operarios por línea (Sacos/Hora)</h4>", unsafe_allow_html=True)

cols_top = st.columns(3)
lineas_top = ['filete_gasa', 'filete_leno', 'planas']
titulos = ['🥇 Filete Gasa', '🥈 Filete Leno', '🥉 Plana']

for i, linea in enumerate(lineas_top):
    with cols_top[i]:
        st.markdown(f"<h5 style='text-align: center;'>{titulos[i]}</h5>", unsafe_allow_html=True)
        df_top = df_filtrado[df_filtrado['Linea'] == linea].copy()
        if df_top.empty:
            st.info("Sin datos.")
        else:
            df_agg = df_top.groupby('Apellidos_Nombres', dropna=True).agg({
                'Cantidad_Completada': 'sum',
                'Tiempo_Corrida': 'sum'
            }).reset_index()

            # sacos/hora robusto
            denom = df_agg['Tiempo_Corrida'].replace(0, pd.NA)
            df_agg['sacos/hora'] = df_agg['Cantidad_Completada'].div(denom).fillna(0.0)

            df_agg = df_agg[['Apellidos_Nombres', 'sacos/hora']].sort_values(by='sacos/hora', ascending=False).head(5)
            df_agg = df_agg.rename(columns={'Apellidos_Nombres': 'Operario'})

            st.dataframe(
                df_agg.style
                    .set_table_attributes('class="styled-table"')
                    .set_properties(**{'background-color': '#111','color': 'white','border': '1px solid #444'})
                    .format({'sacos/hora': '{:.2f}'}),
                use_container_width=True,
                hide_index=True
            )

st.markdown("<h4>❌ Operarios a revisar desempeño (Sacos/Hora)</h4>", unsafe_allow_html=True)

cols_low = st.columns(3)
titulos_low = ['⛔ Filete Gasa', '⛔ Filete Leno', '⛔ Plana']

for i, linea in enumerate(lineas_top):
    with cols_low[i]:
        st.markdown(f"<h5 style='text-align: center;'>{titulos_low[i]}</h5>", unsafe_allow_html=True)
        df_top = df_filtrado[df_filtrado['Linea'] == linea].copy()
        if df_top.empty:
            st.info("Sin datos.")
        else:
            df_agg = df_top.groupby('Apellidos_Nombres', dropna=True).agg({
                'Cantidad_Completada': 'sum',
                'Tiempo_Corrida': 'sum'
            }).reset_index()

            denom = df_agg['Tiempo_Corrida'].replace(0, pd.NA)
            df_agg['sacos/hora'] = df_agg['Cantidad_Completada'].div(denom).fillna(0.0)

            df_agg = df_agg[['Apellidos_Nombres', 'sacos/hora']].sort_values(by='sacos/hora', ascending=True).head(10)
            df_agg = df_agg.rename(columns={'Apellidos_Nombres': 'Operario'})

            st.dataframe(
                df_agg.style
                    .set_table_attributes('class="styled-table"')
                    .set_properties(**{'background-color': '#111','color': 'white','border': '1px solid #444'})
                    .format({'sacos/hora': '{:.2f}'}),
                use_container_width=True,
                hide_index=True
            )

# =========================
#   CAUSAS TIEMPO PERDIDO
# =========================
st.markdown("<h4>⏳ Causas de tiempo perdido por línea</h4>", unsafe_allow_html=True)

lineas_objetivo = ['filete_gasa', 'filete_leno', 'planas', 'Auto7', 'corte_gasa']

for i in range(0, len(lineas_objetivo), 2):
    cols = st.columns(2)
    for j in range(2):
        if i + j >= len(lineas_objetivo):
            continue
        linea = lineas_objetivo[i + j]
        with cols[j]:
            df_linea = df_filtrado[df_filtrado['Linea'] == linea].copy()

            # Protección por columnas faltantes
            if 'Causa_Paro' not in df_linea.columns or 'Tiempo_Perdido' not in df_linea.columns:
                st.markdown(f"<p style='text-align: center; font-size: 9px; color: gray;'>Sin columnas de causas/tiempos</p>", unsafe_allow_html=True)
                continue

            causas = (
                df_linea.groupby('Causa_Paro')['Tiempo_Perdido']
                .sum()
                .sort_values(ascending=True)
            )

            if causas.empty:
                st.markdown(f"<p style='text-align: center; font-size: 9px; color: gray;'>Sin datos</p>", unsafe_allow_html=True)
            else:
                # Top 5 (orden ascendente para coherencia)
                causas = causas.tail(5)
                # recortar etiquetas largas
                causas.index = [c[:20] + '…' if isinstance(c, str) and len(c) > 20 else c for c in causas.index]

                fig_causa, ax_causa = plt.subplots(figsize=(3, 2))
                causas.plot(kind='barh', ax=ax_causa, color=blues_colors(len(causas)))
                ax_causa.set_title(linea.upper(), color='white', fontsize=9)
                ax_causa.set_xlabel("Horas", color='white', fontsize=8)
                ax_causa.set_ylabel("")
                ax_causa.tick_params(colors='white', labelsize=7)
                fig_causa.tight_layout(pad=0.3)
                st.pyplot(fig_causa)

# =========================
#        PIE
# =========================
st.markdown("""
<hr style="border: 1px solid #444;">
<p style="text-align: center; font-size: 14px; color: gray;">
    🤖 Generado por <strong>CiplasBot</strong> - creado por Ing. Wilson Calderón
</p>
""", unsafe_allow_html=True)
