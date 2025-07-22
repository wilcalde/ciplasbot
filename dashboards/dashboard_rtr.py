# dashboardrtr.py
import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib as mpl
import seaborn as sns


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
#****
# 📊 Agrupación por máquina con sumatoria total
resumen_tarjetas = df_filtrado.groupby("Maquinas").agg({
    'Cantidad_Completada': 'sum',
    'Tiempo_Corrida': 'sum',
    'Tiempo_Perdido': 'sum'
}).reset_index()

# ⚙️ Cálculo de velocidad promedio (total producción / total tiempo corrida) y tiempo disponible
resumen_tarjetas["Velocidad_Promedio"] = resumen_tarjetas["Cantidad_Completada"] / (resumen_tarjetas["Tiempo_Corrida"] * 60)
resumen_tarjetas["Tiempo_Disponible"] = resumen_tarjetas["Tiempo_Corrida"] + resumen_tarjetas["Tiempo_Perdido"]

# 🎨 Diseño horizontal de tarjetas
st.markdown("🕒 <b>Resumen de desempeño por máquina</b>", unsafe_allow_html=True)
cols_tarjetas = st.columns(len(resumen_tarjetas))

for idx, row in resumen_tarjetas.iterrows():
    with cols_tarjetas[idx]:
        st.markdown(
            f"""
            <div style='background-color:#0d1b2a; padding:20px; border-radius:10px; text-align:center'>
                <h5 style='color:white'>🖨️ {row["Maquinas"]}</h5>
                <p style='color:#46d9f5; font-size:20px'>🚀 {row["Velocidad_Promedio"]:.1f} m/min</p>
                <p style='color:#f5f242; font-size:18px'>⏱ {row["Tiempo_Disponible"]:.1f} h</p>
            </div>
            """,
            unsafe_allow_html=True
        )

#*****
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

# fin de la primera seccion
    
# Filtro por causa CAMBIO DE REFERENCIA y sin tiempos negativos
df_cambio_ref_strip = df_filtrado[
    (df_filtrado['Causa_Paro'] == "CAMBIO DE REFERENCIA") & 
    (df_filtrado['Tiempo_Perdido'] > 0)
].copy()

st.markdown("<h4>📌 Tiempos individuales de cambio de referencia por máquina</h4>", unsafe_allow_html=True)

if not df_cambio_ref_strip.empty:

    # 🛠 Tabla resumen por máquina
    resumen_maquina = df_cambio_ref_strip.groupby("Maquina").agg(
        Numero_Cambios=('Tiempo_Perdido', 'count'),
        Tiempo_Promedio_h=('Tiempo_Perdido', 'mean')
    ).reset_index().round(2).sort_values(by="Tiempo_Promedio_h", ascending=False)

    st.markdown("⏱️ <b>Resumen visual de desempeño por máquina</b>", unsafe_allow_html=True)

    cols_reloj = st.columns(len(resumen_maquina))
    for i, row in resumen_maquina.iterrows():
        with cols_reloj[i]:
            st.markdown(f"""
                <div style="background-color:#0d1b2a;padding:15px;border-radius:10px;
                            text-align:center;box-shadow:0 4px 8px rgba(0,0,0,0.6);">
                    <h5 style="color:white;margin:0;">🛠 {row['Maquina']}</h5>
                    <p style="color:#F9D923;font-size:22px;margin:5px 0;">⏱ <b>{row['Tiempo_Promedio_h']:.2f} h</b></p>
                    <p style="color:#D65A31;font-size:16px;margin:0;">🔁 {row['Numero_Cambios']} cambios</p>
                </div>
            """, unsafe_allow_html=True)

    # Tabla por operario
    resumen_operario = df_cambio_ref_strip.groupby("Apellidos_Nombres").agg(
        Numero_Cambios=('Tiempo_Perdido', 'count'),
        Tiempo_Promedio_h=('Tiempo_Perdido', 'mean')
    ).reset_index().round(2).sort_values(by="Tiempo_Promedio_h", ascending=False)

    # Tabla por referencia
    resumen_referencia = df_cambio_ref_strip.groupby("Descripcion_Articulo").agg(
        Tiempo_Promedio_h=('Tiempo_Perdido', 'mean')
    ).reset_index().round(2).sort_values(by="Tiempo_Promedio_h", ascending=False)

    # Distribución horizontal: gráfico + operario + referencia
    col1, col2, col3 = st.columns([1, 1.2, 1.5])

    with col1:
        fig_strip, ax = plt.subplots(figsize=(4, 4))
        sns.stripplot(
            data=df_cambio_ref_strip,
            x='Maquina',
            y='Tiempo_Perdido',
            hue='Maquina',
            dodge=True,
            jitter=True,
            size=7,
            palette='Set2',
            ax=ax
        )
        ax.set_title("Tiempos por máquina", color='white')
        ax.set_xlabel("Máquina", color='white')
        ax.set_ylabel("Tiempo perdido (h)", color='white')
        ax.tick_params(colors='white')
        ax.legend([], [], frameon=False)
        fig_strip.patch.set_facecolor('#000000')
        st.pyplot(fig_strip)

    with col2:
        st.markdown("👷 <b>Resumen por operario</b>", unsafe_allow_html=True)
        st.dataframe(
            resumen_operario.style.set_properties(**{
                'background-color': '#0d1b2a',
                'color': 'white',
                'border-color': 'gray',
                'text-align': 'center'
            }).set_table_styles([
                {'selector': 'thead th', 'props': [('background-color', '#1b263b'), ('color', 'white')]}
            ]).set_table_attributes('class="styled-table"')
        )

    with col3:
        st.markdown("📦 <b>Resumen por referencia</b>", unsafe_allow_html=True)
        st.dataframe(
            resumen_referencia.style.set_properties(**{
                'background-color': '#0d1b2a',
                'color': 'white',
                'border-color': 'gray',
                'text-align': 'center'
            }).set_table_styles([
                {'selector': 'thead th', 'props': [('background-color', '#1b263b'), ('color', 'white')]}
            ]).set_table_attributes('class="styled-table"')
        )

    # 👷 Sección: Desempeño por operario
df_validos = df_filtrado.copy()

# Asegurar valores válidos
df_validos = df_validos[
    (df_validos['Tiempo_Corrida'] >= 0) &
    (df_validos['Corrida_Standar'] >= 0)
]

# Agrupación por operario
resumen_operario = df_validos.groupby("Apellidos_Nombres").agg({
    "Cantidad_Completada": "sum",
    "Tiempo_Corrida": "sum",
    "Tiempo_Perdido": "sum",
    "Corrida_Standar": "sum"
}).reset_index()

# Cálculo de velocidad promedio (m/min)
resumen_operario["Velocidad_mmin"] = (
    resumen_operario["Cantidad_Completada"] / (resumen_operario["Tiempo_Corrida"] * 60)
).replace([float('inf'), -float('inf')], 0).fillna(0).round(1)

# Cálculo de productividad
resumen_operario["Productividad_pct"] = (
    (resumen_operario["Corrida_Standar"] / 
     (resumen_operario["Tiempo_Corrida"] + resumen_operario["Tiempo_Perdido"])) * 100
).replace([float('inf'), -float('inf')], 0).fillna(0).round(1)

# Número de cambios de referencia y tiempo promedio por cambio
df_cambios = df_validos[
    (df_validos["Causa_Paro"] == "CAMBIO DE REFERENCIA") &
    (df_validos["Tiempo_Perdido"] > 0)
]

cambios_por_operario = df_cambios.groupby("Apellidos_Nombres").agg({
    "Tiempo_Perdido": ["count", "mean"]
}).fillna(0)

cambios_por_operario.columns = ["Num_Cambios", "Tiempo_Prom_Cambio"]
cambios_por_operario = cambios_por_operario.round(1).reset_index()

# Unión con el resumen general
resumen_operario = resumen_operario.merge(cambios_por_operario, on="Apellidos_Nombres", how="left")
resumen_operario["Num_Cambios"] = resumen_operario["Num_Cambios"].fillna(0).astype(int)
resumen_operario["Tiempo_Prom_Cambio"] = resumen_operario["Tiempo_Prom_Cambio"].fillna(0)

# Ordenar por productividad descendente
resumen_operario = resumen_operario.sort_values(by="Productividad_pct", ascending=False).reset_index(drop=True)

# Mostrar tarjetas en filas de 3
st.markdown("### 🧑‍🏭🖨️ Desempeño por operario")

cols = st.columns(3)  # 3 tarjetas por fila
for i, row in resumen_operario.iterrows():
    with cols[i % 3]:
        st.markdown(f"""
        <div style="background-color:#1b263b; color:white; padding:15px; margin-bottom:10px; border-radius:10px">
            <b>{row['Apellidos_Nombres']}</b><br>
            🖨️ <b>Metros impresos:</b> {row['Cantidad_Completada']:,.0f} m<br>
            🧩 <b># Cambios ref:</b> {row['Num_Cambios']}<br>
            ⏱️ <b>Tiempo prom. cambio:</b> {row['Tiempo_Prom_Cambio']} h<br>
            🚀 <b>Velocidad prom.:</b> {row['Velocidad_mmin']} m/min<br>
            📊 <b>Productividad:</b> {row['Productividad_pct']} %
        </div>
        """, unsafe_allow_html=True)



else:
    st.warning("⚠️ No hay registros válidos de cambios de referencia para mostrar.")