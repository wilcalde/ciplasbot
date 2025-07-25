import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib as mpl
import seaborn as sns
import numpy as np


# Estilo oscuro
mpl.style.use('dark_background')
st.set_page_config(layout="wide", page_title="⚙ Cuerdas", page_icon="🭝")

# Estilos CSS personalizados
st.markdown("""
    <style>
        html, body, [data-testid="stApp"] {
            background-color: #000000 !important;
            color: white !important;
        }

        /* Sidebar rojo claro y texto en rojo */
        div[data-testid="stSidebar"] {
            background-color: #f5f5f5 !important;
        }
        div[data-testid="stSidebar"] * {
            color: #1a1a1a !important;
            font-weight: bold;
        }

        h1, h2, h3, h4, h5, h6, p, label {
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

        .stButton>button {
            background-color: #444;
            color: white;
            border: 1px solid #888;
        }

        .stTextInput>div>div>input,
        .stSelectbox>div>div>div>div {
            background-color: #222 !important;
            color: white !important;
        }

        ::-webkit-scrollbar {
            width: 8px;
            background-color: #222;
        }

        ::-webkit-scrollbar-thumb {
            background-color: #666;
        }
    </style>
""", unsafe_allow_html=True)

# Título principal
st.markdown("<h1>📌 Dashboard ejecutivo Cuerdas</h1>", unsafe_allow_html=True)
fecha_actual = datetime.now().strftime("%d/%m/%Y")
st.markdown(f"<p>🗓 Informe generado el {fecha_actual}</p>", unsafe_allow_html=True)

# Descargar archivo desde Google Sheets
url = "https://docs.google.com/spreadsheets/d/17cV1hJyZPsoaowZLGJuyhmKtoeWdDTrdWLUjPpDQInQ/export?format=xlsx"
response = requests.get(url)

if response.status_code == 200:
    excel_data = io.BytesIO(response.content)
    dfs = pd.read_excel(excel_data, sheet_name=None, engine="openpyxl")
    df = dfs["Cuerdas"]

    # Limpieza y transformación
    df['Fecha_Efectiva'] = pd.to_datetime(df['Fecha_Efectiva'])
    df = df[df["Numero_Articulo"] != "ESP00001"].copy()

    df['Turno'] = df['Turno'].astype(str).str.upper().str.strip()
    df['Turno'] = df['Turno'].replace({'A3': 'A'})
    df = df[df['Turno'].isin(['A', 'B', 'C'])]

    # Sidebar de navegación
    st.sidebar.title("📁 Navegación")
    pagina = st.sidebar.radio("Ir a la sección:", ["Principal", "Cableado", "Torsión", "Trenzado", "Embobina"])

    # Filtrar por centro de trabajo
    df_cableado = df[df['Centro_Trabajo'] == 'CABLEADO']
    df_torsion = df[df['Centro_Trabajo'] == 'TORSION']
    df_trenzado = df[df['Centro_Trabajo'] == 'TRENZADO']
    df_embobina = df[df['Centro_Trabajo'] == 'EMBOBINA']

    # PÁGINAS
    ### inicio pagina primcipal
    if pagina == "Principal":
        st.subheader("📊 Sección principal")
        st.write("Este resumen muestra el consumo de materias primas en el proceso de Torsión, clasificado por categoría de denier.")

        # 🎛️ Filtro de fecha
        st.markdown("### 🎛️ Filtro de fecha para el análisis")
        fechas = pd.to_datetime(df['Fecha_Efectiva'].dt.date.unique())
        fecha_rango = st.date_input("📅 Selecciona un rango de fechas:", value=(min(fechas), max(fechas)))
        fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)

        # 🔄 Filtrar Torsión
        df_torsion_filtrado = df_torsion.copy()
        df_torsion_filtrado = df_torsion_filtrado[
            (df_torsion_filtrado['Fecha_Efectiva'].dt.date >= fecha_inicio) &
            (df_torsion_filtrado['Fecha_Efectiva'].dt.date <= fecha_fin)
        ]

        if df_torsion_filtrado.empty:
            st.warning("⚠️ No hay datos de torsión disponibles para el rango de fechas seleccionado.")
        else:
            df_datos = df_torsion_filtrado.copy()
            df_datos['Tipo_Material'] = df_datos['Descripcion_Articulo'].str.extract(r'^(RAF|MONOF)', expand=False)
            df_datos['Denier'] = pd.to_numeric(df_datos['Descripcion_Articulo'].str.extract(r'(\d{4,6})', expand=False), errors='coerce')

            # Clasificación de denier
            def clasificar_denier(d):
                if pd.isna(d):
                    return 'Desconocido'
                elif 2000 <= d <= 6000:
                    return 'Bajo'
                elif 6001 <= d <= 12000:
                    return 'Medio'
                elif d > 12000:
                    return 'Alto'
                else:
                    return 'Fuera de rango'

            df_datos['Categoria_Denier'] = df_datos['Denier'].apply(clasificar_denier)
            df_datos['Cantidad_Completada'] = pd.to_numeric(df_datos['Cantidad_Completada'], errors='coerce').fillna(0)
            df_datos['Fecha'] = df_datos['Fecha_Efectiva'].dt.date

            st.markdown("## 🧵 Consumo de materia prima Torsión")

            # ===============================
            # GRÁFICOS agrupados (2 por fila)
            # ===============================
            col1, col2 = st.columns(2)

            # 🥧 Gráfico de torta por categoría
            with col1:
                st.markdown("### 🥧 % Kg por categoría de denier")
                resumen_torta = df_datos.groupby('Categoria_Denier')['Cantidad_Completada'].sum().reset_index()
                fig1, ax1 = plt.subplots(figsize=(5, 4))
                colores_torta = ['#1f77b4', '#ff7f0e', '#2ca02c', '#888888']
                ax1.pie(resumen_torta['Cantidad_Completada'],
                    labels=resumen_torta['Categoria_Denier'],
                    autopct='%1.1f%%',
                    startangle=90,
                    colors=colores_torta,
                    textprops={'color': 'white'})
                ax1.set_title("Distribución por categoría", color='white')
                fig1.patch.set_facecolor('#0e1117')
                st.pyplot(fig1)

            # 📊 Gráfico de barras apiladas por día
            with col2:
                st.markdown("### 📊 Kg diarios por categoría de denier")
                resumen_barras = df_datos.groupby(['Fecha', 'Categoria_Denier'])['Cantidad_Completada'].sum().reset_index()
                pivot_barras = resumen_barras.pivot(index='Fecha', columns='Categoria_Denier', values='Cantidad_Completada').fillna(0)

                fig2, ax2 = plt.subplots(figsize=(7, 4))
                pivot_barras.plot(kind='bar', stacked=True, ax=ax2, color=colores_torta, edgecolor='black')
                ax2.set_ylabel("Kg procesados", color='white')
                ax2.set_title("Producción diaria por categoría", color='white')
                ax2.tick_params(axis='x', rotation=45, colors='white')
                ax2.tick_params(axis='y', colors='white')
                ax2.legend(title="Categoría", labelcolor='white')
                ax2.set_facecolor('#0e1117')
                fig2.patch.set_facecolor('#0e1117')
                st.pyplot(fig2)

            # 📊 Nuevo gráfico de barras horizontales por Denier
            col3, _ = st.columns([1.2, 0.8])  # más espacio a la gráfica
            with col3:
                st.markdown("### 📏 Kg por Denier específico")
                resumen_horizontal = df_datos.groupby('Denier')['Cantidad_Completada'].sum().reset_index().dropna()
                resumen_horizontal = resumen_horizontal.sort_values(by='Cantidad_Completada', ascending=True)

                fig3, ax3 = plt.subplots(figsize=(8, 5))
                colores = plt.cm.magma(np.linspace(0.1, 0.9, len(resumen_horizontal)))
                ax3.barh(resumen_horizontal['Denier'].astype(str), resumen_horizontal['Cantidad_Completada'], color=colores)
                ax3.set_xlabel("Kg procesados", color='white')
                ax3.set_ylabel("Denier", color='white')
                ax3.set_title("Kg totales por Denier procesado", color='white')
                ax3.tick_params(axis='x', colors='white')
                ax3.tick_params(axis='y', labelsize=8, colors='white')
                fig3.tight_layout()
                fig3.patch.set_facecolor('#0e1117')
                ax3.set_facecolor('#0e1117')
                st.pyplot(fig3)


   
     ### fin pagina principla   
    elif pagina == "Cableado":
        st.subheader("🧵 Cableado")
        st.write("Aquí analizamos el proceso de cableado.")

        # 🎛️ Filtros para Cableado
        st.markdown("### 🎛️ Filtros")

        col1, col2, col3, col4 = st.columns([2.5, 1.5, 1.5, 2])

        with col1:
            fechas = pd.to_datetime(df_cableado['Fecha_Efectiva'].dt.date.unique())
            fecha_rango = st.date_input("📅 Rango de fechas:", value=(min(fechas), max(fechas)))
            fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)

        with col2:
            maquinas = ["Todas"] + sorted(df_cableado['Maquina'].dropna().unique())
            maquina_seleccionada = st.selectbox("🖨️ Máquina:", maquinas)

        with col3:
            turnos = ["Todos"] + sorted(df_cableado['Turno'].dropna().unique())
            turno_seleccionado = st.selectbox("🧭 Turno:", turnos)

        with col4:
            operarios = ["Todos"] + sorted(df_cableado['Apellidos_Nombres'].dropna().unique())
            operario_seleccionado = st.selectbox("👷 Operario:", operarios)

        # Aplicar filtros
        df_filtrado = df_cableado.copy()
        df_filtrado = df_filtrado[
            (df_filtrado['Fecha_Efectiva'].dt.date >= fecha_inicio) &
            (df_filtrado['Fecha_Efectiva'].dt.date <= fecha_fin)
        ]

        if maquina_seleccionada != "Todas":
            df_filtrado = df_filtrado[df_filtrado['Maquina'] == maquina_seleccionada]

        if turno_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

        if operario_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Apellidos_Nombres'] == operario_seleccionado]

        # Vista previa (puedes reemplazar esto con tus gráficas)
        st.markdown("#### 📄 Vista previa de datos filtrados")
        st.dataframe(df_filtrado.head())

    elif pagina == "Torsión":
        st.subheader("🔄 Torsión")
        st.write("Aquí analizamos el proceso de torsión.")
        # 🎛️ Filtros para torsion
        st.markdown("### 🎛️ Filtros")

        col1, col2, col3, col4 = st.columns([2.5, 1.5, 1.5, 2])

        with col1:
            fechas = pd.to_datetime(df_torsion['Fecha_Efectiva'].dt.date.unique())
            fecha_rango = st.date_input("📅 Rango de fechas:", value=(min(fechas), max(fechas)))
            fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)

        with col2:
            maquinas = ["Todas"] + sorted(df_torsion['Maquina'].dropna().unique())
            maquina_seleccionada = st.selectbox("🖨️ Máquina:", maquinas)

        with col3:
            turnos = ["Todos"] + sorted(df_torsion['Turno'].dropna().unique())
            turno_seleccionado = st.selectbox("🧭 Turno:", turnos)

        with col4:
            operarios = ["Todos"] + sorted(df_torsion['Apellidos_Nombres'].dropna().unique())
            operario_seleccionado = st.selectbox("👷 Operario:", operarios)

        # Aplicar filtros
        df_filtrado = df_torsion.copy()
        df_filtrado = df_filtrado[
            (df_filtrado['Fecha_Efectiva'].dt.date >= fecha_inicio) &
            (df_filtrado['Fecha_Efectiva'].dt.date <= fecha_fin)
        ]

        if maquina_seleccionada != "Todas":
            df_filtrado = df_filtrado[df_filtrado['Maquina'] == maquina_seleccionada]

        if turno_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

        if operario_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Apellidos_Nombres'] == operario_seleccionado]

        st.dataframe(df_torsion.head())

    elif pagina == "Trenzado":
        st.subheader("🧶 Trenzado")
        st.write("Aquí analizamos el proceso de trenzado.")
        # 🎛️ Filtros para trenzado
        st.markdown("### 🎛️ Filtros")

        col1, col2, col3, col4 = st.columns([2.5, 1.5, 1.5, 2])

        with col1:
            fechas = pd.to_datetime(df_trenzado['Fecha_Efectiva'].dt.date.unique())
            fecha_rango = st.date_input("📅 Rango de fechas:", value=(min(fechas), max(fechas)))
            fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)

        with col2:
            maquinas = ["Todas"] + sorted(df_trenzado['Maquina'].dropna().unique())
            maquina_seleccionada = st.selectbox("🖨️ Máquina:", maquinas)

        with col3:
            turnos = ["Todos"] + sorted(df_trenzado['Turno'].dropna().unique())
            turno_seleccionado = st.selectbox("🧭 Turno:", turnos)

        with col4:
            operarios = ["Todos"] + sorted(df_trenzado['Apellidos_Nombres'].dropna().unique())
            operario_seleccionado = st.selectbox("👷 Operario:", operarios)

        # Aplicar filtros
        df_filtrado = df_trenzado.copy()
        df_filtrado = df_filtrado[
            (df_filtrado['Fecha_Efectiva'].dt.date >= fecha_inicio) &
            (df_filtrado['Fecha_Efectiva'].dt.date <= fecha_fin)
        ]

        if maquina_seleccionada != "Todas":
            df_filtrado = df_filtrado[df_filtrado['Maquina'] == maquina_seleccionada]

        if turno_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

        if operario_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Apellidos_Nombres'] == operario_seleccionado]
        st.dataframe(df_trenzado.head())

    elif pagina == "Embobina":
        st.subheader("📦 Embobina")
        st.write("Aquí analizamos el proceso de embobinado.")
        # 🎛️ Filtros para embobina
        st.markdown("### 🎛️ Filtros")

        col1, col2, col3, col4 = st.columns([2.5, 1.5, 1.5, 2])

        with col1:
            fechas = pd.to_datetime(df_embobina['Fecha_Efectiva'].dt.date.unique())
            fecha_rango = st.date_input("📅 Rango de fechas:", value=(min(fechas), max(fechas)))
            fecha_inicio, fecha_fin = fecha_rango if isinstance(fecha_rango, tuple) else (fecha_rango, fecha_rango)

        with col2:
            maquinas = ["Todas"] + sorted(df_embobina['Maquina'].dropna().unique())
            maquina_seleccionada = st.selectbox("🖨️ Máquina:", maquinas)

        with col3:
            turnos = ["Todos"] + sorted(df_embobina['Turno'].dropna().unique())
            turno_seleccionado = st.selectbox("🧭 Turno:", turnos)

        with col4:
            operarios = ["Todos"] + sorted(df_embobina['Apellidos_Nombres'].dropna().unique())
            operario_seleccionado = st.selectbox("👷 Operario:", operarios)

        # Aplicar filtros
        df_filtrado = df_embobina.copy()
        df_filtrado = df_filtrado[
            (df_filtrado['Fecha_Efectiva'].dt.date >= fecha_inicio) &
            (df_filtrado['Fecha_Efectiva'].dt.date <= fecha_fin)
        ]

        if maquina_seleccionada != "Todas":
            df_filtrado = df_filtrado[df_filtrado['Maquina'] == maquina_seleccionada]

        if turno_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Turno'] == turno_seleccionado]

        if operario_seleccionado != "Todos":
            df_filtrado = df_filtrado[df_filtrado['Apellidos_Nombres'] == operario_seleccionado]
        st.dataframe(df_embobina.head())

else:
    st.error(f"❌ Error al descargar el archivo. Código de estado: {response.status_code}")
