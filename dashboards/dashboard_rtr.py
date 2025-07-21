# dashboardrtr.py
import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib as mpl

# Estilo oscuro
mpl.style.use('dark_background')
st.set_page_config(layout="wide", page_title="⚙ Impresion RTR", page_icon="🭝")

# Estilos CSS
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

# Título
st.markdown("<h1>📌 Dashboard ejecutivo Impresion RTR </h1>", unsafe_allow_html=True)
fecha_actual = datetime.now().strftime("%d/%m/%Y")
st.markdown(f"<p>🗓 Informe generado el {fecha_actual}</p>", unsafe_allow_html=True)

# Descargar archivo desde Google Sheets
url = "https://docs.google.com/spreadsheets/d/1V-9iIVMLf19vuQIoiu53t6k2J2vlu49vUjEMnKS5bLY/export?format=xlsx"
response = requests.get(url)

if response.status_code == 200:
    excel_data = io.BytesIO(response.content)
    dfs = pd.read_excel(excel_data, sheet_name=None, engine="openpyxl")
    df = dfs["Costura"]

    # Limpieza y transformación
    df['Fecha_Efectiva'] = pd.to_datetime(df['Fecha_Efectiva'])
    df = df[df["Numero_Articulo"] != "ESP00001"].copy()

    # Normalizar y limpiar turnos
    df['Turno'] = df['Turno'].astype(str).str.upper().str.strip()
    df['Turno'] = df['Turno'].replace({'A3': 'A'})
    df = df[df['Turno'].isin(['A', 'B', 'C'])]

    # Normalizar nombre de máquina
    df['Maquina'] = df['Maquina'].astype(str).str.upper().str.strip()
    df = df[df['Maquina'].isin(['COM1', 'COM2', 'COM3', 'COM4', 'COM5'])]

    # Columna estándar de máquina
    df['Maquinas'] = df['Maquina']

    # Filtros
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        fechas = pd.to_datetime(df['Fecha_Efectiva'].dt.date.unique())
        fecha_rango = st.date_input("🔕 Selecciona fecha o rango:", value=(min(fechas), max(fechas)))
        fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)
    with col2:
        linea_seleccionada = st.selectbox("🖨 Maquinas:", ["Todas"] + sorted(df['Maquinas'].unique()))
    with col3:
        turno_seleccionado = st.selectbox("👨‍💼 Turno:", ["Todos"] + sorted(df['Turno'].dropna().unique()))

    # Aplicar filtros
    df_filtrado = df[
        (df['Fecha_Efectiva'].dt.date >= fecha_inicio) &
        (df['Fecha_Efectiva'].dt.date <= fecha_fin)
    ].copy()

    if linea_seleccionada != "Todas":
        df_filtrado = df_filtrado[df_filtrado['Maquinas'] == linea_seleccionada]
    if turno_seleccionado != "Todos":
        df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

    # Mostrar datos filtrados
    #st.markdown("### 📄 Datos filtrados")
    #st.dataframe(df_filtrado.style.set_table_attributes('class="styled-table"'))

    # Gráficos
st.markdown("<h4>📊 Producción y tiempos perdidos</h4>", unsafe_allow_html=True)
col_g1, col_g2, col_g3 = st.columns(3)

with col_g1:
    st.markdown("<h5>🔄 Distribución por línea</h5>", unsafe_allow_html=True)
    resumen_linea = df_filtrado.groupby('Maquinas')['Cantidad_Completada'].sum().sort_values()
    fig_linea, ax_linea = plt.subplots(figsize=(5, 3.5))
    resumen_linea.plot(kind='barh', ax=ax_linea, color='skyblue')
    ax_linea.set_xlabel("Cantidad", color="white")
    ax_linea.set_title("Distribución por línea", color="white")
    ax_linea.tick_params(colors='white')
    st.pyplot(fig_linea)

with col_g2:
    st.markdown("<h5>📈 Producción diaria por máquina</h5>", unsafe_allow_html=True)
    df_graf = df_filtrado.copy()
    grafico = df_graf.groupby([df_graf['Fecha_Efectiva'].dt.date, 'Maquinas'])['Cantidad_Completada'].sum().unstack(fill_value=0)
    color_map = {'COM1': 'blue', 'COM2': 'orange', 'COM3': 'green', 'COM4': 'yellow', 'COM5': 'white'}
    colors = [color_map.get(col, 'gray') for col in grafico.columns]
    fig, ax = plt.subplots(figsize=(5, 3.5))
    grafico.plot(kind='bar', stacked=True, ax=ax, color=colors)
    ax.set_ylabel("Unidades", color="white")
    ax.set_xlabel("Fecha", color="white")
    ax.set_title("Producción diaria por máquina", color="white")
    ax.tick_params(colors='white')
    ax.legend(loc='upper left', fontsize=6)
    st.pyplot(fig)

with col_g3:
    st.markdown("<h5>🚀 Velocidad por máquina (m/min) - Dispersión</h5>", unsafe_allow_html=True)

    df_vel = df_filtrado.copy()
    df_vel = df_vel[df_vel['Tiempo_Corrida'] > 0]
    df_vel['Velocidad'] = df_vel['Cantidad_Completada'] / (df_vel['Tiempo_Corrida'] * 60)  # m/min

    # Filtrar valores válidos: entre 20 y 1000 m/min
    df_vel = df_vel[(df_vel['Velocidad'] >= 20) & (df_vel['Velocidad'] < 1000)]

    if not df_vel.empty:
        fig_disp, ax_disp = plt.subplots(figsize=(5, 3.5))

        maquinas_ordenadas = sorted(df_vel['Maquinas'].unique())
        for i, maquina in enumerate(maquinas_ordenadas):
            datos = df_vel[df_vel['Maquinas'] == maquina]['Velocidad']
            x_vals = [i] * len(datos)
            ax_disp.scatter(x_vals, datos, alpha=0.6, color='deepskyblue')

        ax_disp.set_xticks(range(len(maquinas_ordenadas)))
        ax_disp.set_xticklabels(maquinas_ordenadas, color='white')
        ax_disp.set_ylabel("Velocidad (m/min)", color='white')
        ax_disp.set_title("Dispersión de velocidad por máquina", color='white')
        ax_disp.tick_params(colors='white')
        fig_disp.tight_layout()
        st.pyplot(fig_disp)
    else:
        st.warning("⚠️ No hay datos válidos entre 20 y 1000 m/min en el rango seleccionado.")

