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
    if pagina == "Principal":
        st.subheader("📊 Sección principal")
        st.write("Aquí va el resumen general del proceso de cuerdas.")

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
