# dashboard.py
import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib as mpl

# Estilo oscuro
mpl.style.use('dark_background')
st.set_page_config(layout="wide", page_title="🏠 Dashboard Fileteado", page_icon="🭝")

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
st.markdown("<h1>📌 Dashboard ejecutivo fileteado</h1>", unsafe_allow_html=True)
fecha_actual = datetime.now().strftime("%d/%m/%Y")
st.markdown(f"<p>🗓 Informe generado el {fecha_actual}</p>", unsafe_allow_html=True)

# Descargar archivo desde Google Sheets
url = 'https://docs.google.com/spreadsheets/d/1FYLgfQhLvCUtiuxGnn5aQK6aCChoFPmMU-eMa0KAHrg/export?format=xlsx'
response = requests.get(url)

if response.status_code == 200:
    excel_data = io.BytesIO(response.content)
    dfs = pd.read_excel(excel_data, sheet_name=None)
    df = dfs["Fileteado"]
    df['Fecha_Efectiva'] = pd.to_datetime(df['Fecha_Efectiva'])
    df = df[df["Numero_Articulo"] != "ESP00001"].copy()
    df['Turno'] = df['Turno'].str.upper()

    def clasificar_maquina(row):
        maquina = str(row['Maquina']).lower()
        articulo = str(row['Numero_Articulo']).lower()
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

    # Filtros
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        fechas = pd.to_datetime(df['Fecha_Efectiva'].dt.date.unique())
        fecha_rango = st.date_input("🔕 Selecciona fecha o rango:", value=(min(fechas), max(fechas)))
        fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)
    with col2:
        linea_seleccionada = st.selectbox("🏠 Línea de producción:", ["Todas"] + sorted(df['Linea'].unique()))
    with col3:
        turno_seleccionado = st.selectbox("👨‍💼 Turno:", ["Todos"] + sorted(df['Turno'].dropna().unique()))

    # Aplicar filtros
    df_filtrado = df[(df['Fecha_Efectiva'].dt.date >= fecha_inicio) & (df['Fecha_Efectiva'].dt.date <= fecha_fin)].copy()
    if linea_seleccionada != "Todas":
        df_filtrado = df_filtrado[df_filtrado['Linea'] == linea_seleccionada]
    if turno_seleccionado != "Todos":
        df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

    # Gráficos de producción y tiempo perdido
    st.markdown("""
        <h4>📊 Producción y causas de paro</h4>
    """, unsafe_allow_html=True)

    col_g1, col_g2, col_g3 = st.columns(3)
    with col_g1:
        st.markdown("<h5>🔄 Distribución por línea</h5>", unsafe_allow_html=True)
        resumen_linea = df_filtrado.groupby('Linea')['Cantidad_Completada'].sum().sort_values()
        fig_linea, ax_linea = plt.subplots(figsize=(5, 3.5))
        resumen_linea.plot(kind='barh', ax=ax_linea, color='skyblue')
        ax_linea.set_xlabel("Cantidad", color="white")
        ax_linea.set_title("Distribución por línea", color="white")
        ax_linea.tick_params(colors='white')
        st.pyplot(fig_linea)

    with col_g2:
        st.markdown("<h5>📈 Producción diaria por línea</h5>", unsafe_allow_html=True)
        df_graf = df[df['Linea'].isin(['planas', 'filete_gasa', 'filete_leno'])].copy()
        grafico = df_graf.groupby([df_graf['Fecha_Efectiva'].dt.date, 'Linea'])['Cantidad_Completada'].sum().unstack(fill_value=0)
        color_map = {'filete_gasa': 'blue', 'filete_leno': 'orange', 'planas': 'green'}
        colors = [color_map.get(col, 'gray') for col in grafico.columns]
        fig, ax = plt.subplots(figsize=(5, 3.5))
        grafico.plot(kind='bar', stacked=True, ax=ax, color=colors)
        ax.set_ylabel("Unidades", color="white")
        ax.set_xlabel("Fecha", color="white")
        ax.set_title("Producción diaria por línea", color="white")
        ax.tick_params(colors='white')
        ax.legend(loc='upper left', fontsize=6)
        st.pyplot(fig)

    with col_g3:
        st.markdown("<h5>⏱ Tiempo de corrida por día y línea</h5>", unsafe_allow_html=True)
        df_corrida = df[df['Linea'].isin(['planas', 'filete_gasa', 'filete_leno'])].copy()
        corrida = df_corrida.groupby([df_corrida['Fecha_Efectiva'].dt.date, 'Linea'])['Tiempo_Corrida'].sum().unstack(fill_value=0)
        color_map_corrida = {'filete_gasa': 'blue', 'filete_leno': 'orange', 'planas': 'green'}
        colors_corrida = [color_map_corrida.get(col, 'gray') for col in corrida.columns]
        fig_corrida, ax_corrida = plt.subplots(figsize=(5, 3.5))
        corrida.plot(kind='bar', stacked=True, ax=ax_corrida, color=colors_corrida)
        ax_corrida.set_ylabel("Horas", color="white")
        ax_corrida.set_xlabel("Fecha", color="white")
        ax_corrida.set_title("Tiempo de corrida diario", color="white")
        ax_corrida.tick_params(colors='white')
        ax_corrida.legend(loc='upper left', fontsize=6)
        st.pyplot(fig_corrida)

    # Top 5 operarios por línea
    st.markdown("""
        <h4>🏆 Mejores operarios por línea (Sacos/Hora)</h4>
    """, unsafe_allow_html=True)

    cols_top = st.columns(3)
    lineas_top = ['filete_gasa', 'filete_leno', 'planas']
    titulos = ['🥇 Filete Gasa', '🥈 Filete Leno', '🥉 Plana']

    for i, linea in enumerate(lineas_top):
        with cols_top[i]:
            st.markdown(f"<h5 style='text-align: center;'>{titulos[i]}</h5>", unsafe_allow_html=True)
            df_top = df_filtrado[df_filtrado['Linea'] == linea]
            df_agg = df_top.groupby('Apellidos_Nombres').agg({
                'Cantidad_Completada': 'sum',
                'Tiempo_Corrida': 'sum'
            }).reset_index()
            df_agg['sacos/hora'] = df_agg['Cantidad_Completada'] / df_agg['Tiempo_Corrida']
            df_agg = df_agg[['Apellidos_Nombres', 'sacos/hora']].sort_values(by='sacos/hora', ascending=False).head(5)
            df_agg = df_agg.rename(columns={'Apellidos_Nombres': 'Operario'})
            st.dataframe(df_agg.style
                .set_table_attributes('class="styled-table"')
                .set_properties(**{
                    'background-color': '#111',
                    'color': 'white',
                    'border': '1px solid #444',
                })
                .format({'sacos/hora': '{:.2f}'}),
                use_container_width=True,
                hide_index=True)

    # Operariosbajo desempeno
    st.markdown("""
        <h4>❌ Operarios a revisar desempeno (Sacos/Hora)</h4>
    """, unsafe_allow_html=True)

    cols_top = st.columns(3)
    lineas_top = ['filete_gasa', 'filete_leno', 'planas']
    titulos = ['⛔ Filete Gasa', '⛔ Filete Leno', '⛔ Plana']

    for i, linea in enumerate(lineas_top):
        with cols_top[i]:
            st.markdown(f"<h5 style='text-align: center;'>{titulos[i]}</h5>", unsafe_allow_html=True)
            df_top = df_filtrado[df_filtrado['Linea'] == linea]
            df_agg = df_top.groupby('Apellidos_Nombres').agg({
                'Cantidad_Completada': 'sum',
                'Tiempo_Corrida': 'sum'
            }).reset_index()
            df_agg['sacos/hora'] = df_agg['Cantidad_Completada'] / df_agg['Tiempo_Corrida']
            df_agg = df_agg[['Apellidos_Nombres', 'sacos/hora']].sort_values(by='sacos/hora', ascending=True).head(10)
            df_agg = df_agg.rename(columns={'Apellidos_Nombres': 'Operario'})
            st.dataframe(df_agg.style
                .set_table_attributes('class="styled-table"')
                .set_properties(**{
                    'background-color': '#111',
                    'color': 'white',
                    'border': '1px solid #444',
                })
                .format({'sacos/hora': '{:.2f}'}),
                use_container_width=True,
                hide_index=True)


    st.markdown("""
        <h4>⏳ Causas de tiempo perdido por línea</h4>
    """, unsafe_allow_html=True)
    lineas_objetivo = ['filete_gasa', 'filete_leno', 'planas', 'Auto7', 'corte_gasa']
    cols = st.columns(5)
    for i, linea in enumerate(lineas_objetivo):
        with cols[i]:
            df_linea = df[
                (df['Linea'] == linea) &
                (df['Fecha_Efectiva'].dt.date >= fecha_inicio) &
                (df['Fecha_Efectiva'].dt.date <= fecha_fin)
            ].copy()
            if turno_seleccionado != "Todos":
                df_linea = df_linea[df_linea['Turno'] == turno_seleccionado]
            causas = df_linea.groupby('Causa_Paro')['Tiempo_Perdido'].sum().sort_values(ascending=True)
            fig_causa, ax_causa = plt.subplots(figsize=(3, 3))
            causas.plot(kind='barh', ax=ax_causa, color="#FF9900")
            ax_causa.set_title(linea.upper(), color='white', fontsize=10)
            ax_causa.set_xlabel("Horas", color='white')
            ax_causa.tick_params(colors='white', labelsize=8)
            fig_causa.tight_layout()
            st.pyplot(fig_causa)

    # Pie de página
    st.markdown("""
    <hr style="border: 1px solid #444;">
    <p style="text-align: center; font-size: 14px; color: gray;">
        🤖 Generado por <strong>CiplasBot</strong> - creado por Ing. Wilson Calderón
    </p>
""", unsafe_allow_html=True)

else:
    st.error(f"❌ Error al descargar el archivo. Código de estado: {response.status_code}")
