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
    col1, col2, col3, col4 = st.columns([2.5, 1.5, 1.5, 2])

with col1:
    fechas = pd.to_datetime(df['Fecha_Efectiva'].dt.date.unique())
    fecha_rango = st.date_input("🔕 Selecciona fecha o rango:", value=(min(fechas), max(fechas)))
    fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)

with col2:
    linea_seleccionada = st.selectbox("🖨 Maquinas:", ["Todas"] + sorted(df['Maquinas'].unique()))

with col3:
    turno_seleccionado = st.selectbox("👨‍💼 Turno:", ["Todos"] + sorted(df['Turno'].dropna().unique()))

with col4:
    nombres_operarios = sorted(df["Apellidos_Nombres"].dropna().unique())
    operario_seleccionado = st.selectbox("👷‍♂️ Operario:", ["Todos"] + nombres_operarios)

    # Aplicar filtros
    # Aplicar filtros
df_filtrado = df[
    (df['Fecha_Efectiva'].dt.date >= fecha_inicio) &
    (df['Fecha_Efectiva'].dt.date <= fecha_fin)
].copy()

if linea_seleccionada != "Todas":
    df_filtrado = df_filtrado[df_filtrado['Maquinas'] == linea_seleccionada]

if turno_seleccionado != "Todos":
    df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

if operario_seleccionado != "Todos":
    df_filtrado = df_filtrado[df_filtrado["Apellidos_Nombres"] == operario_seleccionado]


    # Mostrar datos filtrados
    #st.markdown("### 📄 Datos filtrados")
    #st.dataframe(df_filtrado.style.set_table_attributes('class="styled-table"'))

    # Gráficos
st.markdown("<h4>📊 Producción y tiempos perdidos</h4>", unsafe_allow_html=True)
#****
# Agrupar por máquina y turno (incluye Corrida_Standar para productividad)
resumen_tarjetas = df_filtrado.groupby(["Maquinas", "Turno"]).agg({
    'Cantidad_Completada': 'sum',
    'Tiempo_Corrida': 'sum',
    'Tiempo_Perdido': 'sum',
    'Corrida_Standar': 'sum'
}).reset_index()

# Calcular velocidad promedio, tiempo disponible y productividad por turno
resumen_tarjetas["Velocidad_Promedio"] = resumen_tarjetas["Cantidad_Completada"] / (resumen_tarjetas["Tiempo_Corrida"] * 60)
resumen_tarjetas["Tiempo_Disponible"] = resumen_tarjetas["Tiempo_Corrida"] + resumen_tarjetas["Tiempo_Perdido"]
resumen_tarjetas["Productividad_pct"] = (
    (resumen_tarjetas["Corrida_Standar"] / resumen_tarjetas["Tiempo_Disponible"]) * 100
).replace([float('inf'), -float('inf')], 0).fillna(0).round(1)

# Lista única de máquinas
maquinas_unicas = resumen_tarjetas["Maquinas"].unique()
st.markdown("🕒 <b>Resumen de desempeño por máquina y turno</b>", unsafe_allow_html=True)

cols_tarjetas = st.columns(len(maquinas_unicas))

for idx, maquina in enumerate(maquinas_unicas):
    df_maquina = resumen_tarjetas[resumen_tarjetas["Maquinas"] == maquina]

    texto_turnos = ""
    for turno in ['A', 'B', 'C']:
        fila = df_maquina[df_maquina["Turno"] == turno]
        if not fila.empty:
            velocidad = fila["Velocidad_Promedio"].values[0]
            tiempo = fila["Tiempo_Disponible"].values[0]
            productividad = fila["Productividad_pct"].values[0]
            texto_turnos += (
                f"<p>🔁 Turno {turno}: "
                f"🚀 {velocidad:.1f} m/min – "
                f"⏱ {tiempo:.1f} h – "
                f"📊 {productividad:.1f} %</p>"
            )
        else:
            texto_turnos += f"<p>🔁 Turno {turno}: 🚫 Sin datos</p>"

    with cols_tarjetas[idx]:
        st.markdown(
            f"""
            <div style='background-color:#0d1b2a; padding:20px; border-radius:10px; text-align:center'>
                <h5 style='color:white'>🖨️ {maquina}</h5>
                {texto_turnos}
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

# --- Sección: 6 líneas individuales + 1 línea de promedio total con tendencia destacada ---

import numpy as np
import matplotlib.dates as mdates

# 1. Cálculo de velocidad y extracción de fecha
df['Velocidad'] = df['Cantidad_Completada'] / (df['Tiempo_Corrida'] * 60)
df['Fecha'] = df['Fecha_Efectiva'].dt.date

# 2. Promedio diario de velocidad por máquina
avg_speed = (
    df
    .groupby(['Fecha', 'Maquina'])['Velocidad']
    .mean()
    .reset_index()
)

# 3. Pivot para graficar cada máquina
pivot_speed = avg_speed.pivot(index='Fecha', columns='Maquina', values='Velocidad')

# 4. Serie de promedio total (todas las máquinas)
promedio_total = pivot_speed.mean(axis=1)

# 5. Crear figura con 2 filas × 3 columnas
fig, axes = plt.subplots(2, 3, figsize=(15, 10), sharex=True, sharey=True)
axes = axes.flatten()
machines = pivot_speed.columns.tolist()

# 6. Dibujar una línea por máquina en cada subplot
for i, maquina in enumerate(machines):
    ax = axes[i]
    ax.plot(
        pivot_speed.index,
        pivot_speed[maquina],
        marker='o'
    )
    ax.set_title(maquina)
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.set_xlabel('Fecha')
    ax.set_ylabel('Velocidad (u/minuto)')

# 7. Último subplot: promedio total + tendencia muy visible
ax_total = axes[-1]
# Serie principal
ax_total.plot(
    promedio_total.index,
    promedio_total.values,
    marker='o',
    color='yellow',
    linewidth=2,
    label='Promedio total',
    zorder=3
)
# Conversión de fechas para la regresión
dates_num = mdates.date2num(promedio_total.index)
y = promedio_total.values
# Cálculo de la tendencia lineal
coef = np.polyfit(dates_num, y, 1)
trend = np.poly1d(coef)
# Línea de tendencia destacada
ax_total.plot(
    promedio_total.index,
    trend(dates_num),
    linestyle='--',
    color='red',       # rojo para resaltar
    linewidth=3,       # más gruesa
    zorder=4,
    label='Tendencia'
)
# Formateo de fechas
ax_total.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
for label in ax_total.get_xticklabels():
    label.set_rotation(45)
    label.set_ha('right')

ax_total.set_title('Promedio total con tendencia')
ax_total.grid(True, linestyle='--', alpha=0.4)
ax_total.set_xlabel('Fecha')
ax_total.set_ylabel('Velocidad (u/minuto)')
ax_total.legend(loc='upper left')

fig.tight_layout()

# 8. Mostrar en Streamlit
st.pyplot(fig) 

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

   # 👷 Sección: Desempeño por operario con velocidad por turno
df_validos = df_filtrado.copy()

# Asegurar valores válidos
df_validos = df_validos[
    (df_validos['Tiempo_Corrida'] >= 0) &
    (df_validos['Corrida_Standar'] >= 0)
]

# Agrupación principal
resumen_operario = df_validos.groupby("Apellidos_Nombres").agg({
    "Cantidad_Completada": "sum",
    "Tiempo_Corrida": "sum",
    "Tiempo_Perdido": "sum",
    "Corrida_Standar": "sum"
}).reset_index()

#seccion de tiempos perdidos
# 🔻 Sección: Tiempos perdidos por máquina
st.markdown("<h3>🕒 Resumen Tiempos perdidos</h3>", unsafe_allow_html=True)

# Asegurar datos válidos
df_tiempo = df_filtrado[
    (df_filtrado["Tiempo_Perdido"] > 0) & 
    (df_filtrado["Causa_Paro"].notnull())
].copy()

# Lista de máquinas objetivo
maquinas_objetivo = ["COM1", "COM2", "COM3", "COM4", "COM5"]

# Gráfico total por causa (todas las máquinas)
resumen_total = (
    df_tiempo.groupby("Causa_Paro")["Tiempo_Perdido"]
    .sum()
    .sort_values(ascending=False)
    .round(2)
)

# 🔷 Mostrar gráficos de 2 por fila
graficos = []

# Generar gráficos por máquina
for maquina in maquinas_objetivo:
    df_maquina = df_tiempo[df_tiempo["Maquinas"] == maquina]
    if df_maquina.empty:
        graficos.append(None)
        continue

    resumen_causas = (
        df_maquina.groupby("Causa_Paro")["Tiempo_Perdido"]
        .sum()
        .sort_values(ascending=False)
        .round(2)
    )

    fig, ax = plt.subplots(figsize=(4.5, 3.5))
    resumen_causas.plot(kind="barh", ax=ax, color="tomato")
    ax.invert_yaxis()
    ax.set_title(f"{maquina}", color="white", fontsize=10)
    ax.set_xlabel("Horas", color="white")
    ax.set_ylabel("Causa", color="white")
    ax.tick_params(colors="white", labelsize=8)
    fig.patch.set_facecolor('#000000')
    ax.set_facecolor('#000000')
    graficos.append(fig)

# Crear el gráfico global combinado
fig_total, ax_total = plt.subplots(figsize=(4.5, 3.5))
resumen_total.plot(kind="barh", ax=ax_total, color="orange")
ax_total.invert_yaxis()
ax_total.set_title("TOTAL (todas las máquinas)", color="white", fontsize=10)
ax_total.set_xlabel("Horas", color="white")
ax_total.set_ylabel("Causa", color="white")
ax_total.tick_params(colors="white", labelsize=8)
fig_total.patch.set_facecolor('#000000')
ax_total.set_facecolor('#000000')
graficos.append(fig_total)

# Mostrar los 6 gráficos (5 máquinas + total) en 3 filas de 2 columnas
for i in range(0, len(graficos), 2):
    cols = st.columns(2)
    for j in range(2):
        if i + j < len(graficos):
            with cols[j]:
                if graficos[i + j] is None:
                    st.markdown("⚠️ No hay datos disponibles.")
                else:
                    st.pyplot(graficos[i + j])

# Fin se seccion tiempos perdidos


# Cálculo de velocidad y productividad global
resumen_operario["Velocidad_mmin"] = (
    resumen_operario["Cantidad_Completada"] / (resumen_operario["Tiempo_Corrida"] * 60)
).replace([float('inf'), -float('inf')], 0).fillna(0).round(1)

resumen_operario["Productividad_pct"] = (
    (resumen_operario["Corrida_Standar"] / 
     (resumen_operario["Tiempo_Corrida"] + resumen_operario["Tiempo_Perdido"])) * 100
).replace([float('inf'), -float('inf')], 0).fillna(0).round(1)

# Cálculo de cambios de referencia
df_cambios = df_validos[
    (df_validos["Causa_Paro"] == "CAMBIO DE REFERENCIA") & (df_validos["Tiempo_Perdido"] > 0)
]

cambios_por_operario = df_cambios.groupby("Apellidos_Nombres").agg({
    "Tiempo_Perdido": ["count", "mean"]
}).fillna(0)

cambios_por_operario.columns = ["Num_Cambios", "Tiempo_Prom_Cambio"]
cambios_por_operario = cambios_por_operario.round(1).reset_index()

# Unión con resumen principal
resumen_operario = resumen_operario.merge(cambios_por_operario, on="Apellidos_Nombres", how="left")
resumen_operario["Num_Cambios"] = resumen_operario["Num_Cambios"].fillna(0).astype(int)
resumen_operario["Tiempo_Prom_Cambio"] = resumen_operario["Tiempo_Prom_Cambio"].fillna(0)

# Velocidad por turno
vel_turno = df_validos.groupby(["Apellidos_Nombres", "Turno"]).apply(
    lambda x: (x["Cantidad_Completada"].sum() / (x["Tiempo_Corrida"].sum() * 60))
).reset_index(name="Vel_Turno").round(1)

# Convertir a formato ancho
vel_turno_pivot = vel_turno.pivot(index="Apellidos_Nombres", columns="Turno", values="Vel_Turno").reset_index()
vel_turno_pivot.columns.name = None
vel_turno_pivot = vel_turno_pivot.rename(columns={
    'A': 'Vel_A', 'B': 'Vel_B', 'C': 'Vel_C'
})

# Unión con resumen principal
resumen_operario = resumen_operario.merge(vel_turno_pivot, on="Apellidos_Nombres", how="left")

# Ordenar por productividad
resumen_operario = resumen_operario.sort_values(by="Productividad_pct", ascending=False).reset_index(drop=True)

# Mostrar tarjetas
 # 🧑‍🏭 Tabla profesional de desempeño por operario
st.markdown("### 🧑‍🏭🖨️ Ranking de desempeño por operario (ordenado por productividad)")

# Añadir íconos de desempeño
resumen_operario["Ícono"] = resumen_operario["Productividad_pct"].apply(
    lambda x: "🏅" if x >= 60 else "🔍"
)

# Reordenar columnas para visualización
resumen_operario_vista = resumen_operario[[
    "Ícono", "Apellidos_Nombres", "Cantidad_Completada", "Num_Cambios",
    "Tiempo_Prom_Cambio", "Velocidad_mmin", "Vel_A", "Vel_B", "Vel_C", "Productividad_pct"
]]

# Renombrar columnas
resumen_operario_vista.columns = [
    "🔎", "Operario", "Metros impresos", "# Cambios ref", "Tiempo prom. cambio (h)",
    "Vel. global (m/min)", "Vel. Turno A", "Vel. Turno B", "Vel. Turno C", "Productividad (%)"
]

# Formatear valores
resumen_operario_vista["Metros impresos"] = resumen_operario_vista["Metros impresos"].map(lambda x: f"{x:,.0f}")
resumen_operario_vista["Vel. global (m/min)"] = resumen_operario_vista["Vel. global (m/min)"].map(lambda x: f"{x:.1f}")
resumen_operario_vista["Vel. Turno A"] = resumen_operario_vista["Vel. Turno A"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "—")
resumen_operario_vista["Vel. Turno B"] = resumen_operario_vista["Vel. Turno B"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "—")
resumen_operario_vista["Vel. Turno C"] = resumen_operario_vista["Vel. Turno C"].map(lambda x: f"{x:.1f}" if pd.notna(x) else "—")
resumen_operario_vista["Tiempo prom. cambio (h)"] = resumen_operario_vista["Tiempo prom. cambio (h)"].map(lambda x: f"{x:.1f}")
resumen_operario_vista["Productividad (%)"] = resumen_operario_vista["Productividad (%)"].map(lambda x: f"{x:.1f}")

# Mostrar tabla estilizada
st.dataframe(
    resumen_operario_vista.style.set_properties(**{
        'background-color': '#0d1b2a',
        'color': 'white',
        'border-color': '#0d1b2a',
        'text-align': 'center',
        'font-size': '13px'
    }).set_table_styles([
        {'selector': 'thead th', 'props': [('background-color', '#1b263b'), ('color', 'white'), ('font-weight', 'bold')]}
    ]).set_table_attributes('class="styled-table"')
)

# nueva seccion
# ---- Sección actualizada: Agrupar por referencia y máquina (corridas multi-día) ----
st.markdown("<h4>📊 Análisis de volúmenes por referencia y máquina</h4>", unsafe_allow_html=True)

# Preparar datos
df_ref = df_filtrado.copy()

# Totalizar metros impresos por referencia + máquina, sin dividir por fecha
resumen_ref = (
    df_ref
    .groupby(['Descripcion_Articulo', 'Maquinas'], as_index=False)
    .agg(metros_continuos=('Cantidad_Completada', 'sum'))
)

# Eliminar registros cero
resumen_ref = resumen_ref[resumen_ref['metros_continuos'] > 0]

# Categorizar para gráfica de barras y torta
bins = [0, 3000, 5000, float('inf')]
labels = ['<3000', '3000-5000', '>5000']
resumen_ref['categoria'] = pd.cut(
    resumen_ref['metros_continuos'],
    bins=bins,
    labels=labels,
    right=False
)

# Scatter: metros continuos por referencia (x sin etiquetas)
fig_disp, ax_disp = plt.subplots(figsize=(5, 4))
sns.scatterplot(
    data=resumen_ref,
    x='Descripcion_Articulo',
    y='metros_continuos',
    legend=False,
    ax=ax_disp
)
ax_disp.tick_params(axis='x', labelbottom=False)
ax_disp.set_ylabel("Metros impresos", color="white")
ax_disp.set_title("Volumen total por referencia y máquina", color="white")
ax_disp.tick_params(colors='white')
fig_disp.tight_layout()

# Torta: distribución general
cat_counts = resumen_ref['categoria'].value_counts().reindex(labels).fillna(0)
fig_pie, ax_pie = plt.subplots(figsize=(4, 4))
ax_pie.pie(
    cat_counts,
    labels=labels,
    autopct='%1.1f%%',
    textprops={'color': 'white'}
)
ax_pie.set_title("Distribución por tamaño de pedido", color="white")
fig_pie.patch.set_facecolor('#000000')
ax_pie.set_facecolor('#000000')
fig_pie.tight_layout()

# Mostrar scatter + tabla alineados
col1, col2 = st.columns(2)
with col1:
    st.pyplot(fig_disp)
with col2:
    st.dataframe(
        resumen_ref[['Descripcion_Articulo','Maquinas','metros_continuos']].style
            .set_properties(**{
                'background-color':'#0d1b2a',
                'color':'white',
                'border-color':'gray',
                'text-align':'center'
            })
            .set_table_styles([{
                'selector':'thead th',
                'props':[
                    ('background-color','#1b263b'),
                    ('color','white'),
                    ('font-weight','bold')
                ]
            }])
            .set_table_attributes('class="styled-table"')
    )

# ---- Barras horizontales: % de pedidos por rango y máquina ----
machine_counts = (
    resumen_ref
    .groupby(['Maquinas','categoria'])
    .size()
    .unstack(fill_value=0)
)
machine_pct = machine_counts.div(machine_counts.sum(axis=1), axis=0)*100

layout = [['COM1','COM2'],['COM3','COM4'],['COM5']]
for row in layout:
    cols = st.columns(2)
    for i, m in enumerate(row):
        with cols[i]:
            if m in machine_pct.index:
                fig_bar, ax_bar = plt.subplots(figsize=(5,3.5))
                machine_pct.loc[m].plot(kind='barh', ax=ax_bar)
                ax_bar.set_xlim(0,100)
                ax_bar.set_title(m, color="white")
                ax_bar.set_xlabel("% pedidos", color="white")
                ax_bar.tick_params(colors='white', labelsize=8)
                fig_bar.patch.set_facecolor('#000000')
                ax_bar.set_facecolor('#000000')
                fig_bar.tight_layout()
                st.pyplot(fig_bar)
            else:
                st.markdown(f"⚠️ Sin datos: {m}")
    # última fila, segunda columna: pie
    if len(row)==1:
        with cols[1]:
            st.pyplot(fig_pie)