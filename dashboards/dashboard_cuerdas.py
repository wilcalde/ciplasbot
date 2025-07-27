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

    # Normalizar turno
    df['Turno'] = df['Turno'].astype(str).str.upper().str.strip()
    df['Turno'] = df['Turno'].replace({'A3': 'A'})
    df = df[df['Turno'].isin(['A','B','C'])]

    # ————— Homologar nombres de máquina GLOBALEMENTE —————
    df['Maquina'] = (
        df['Maquina']
        .astype(str)
        .str.strip()    # quita espacios extras
        .str.upper()    # pasa todo a mayúsculas
    )

    # Ahora sí separamos por proceso
    df_cableado = df[df['Centro_Trabajo'] == 'CABLEADO'].copy()
    df_torsion  = df[df['Centro_Trabajo'] == 'TORSION'].copy()
    df_trenzado = df[df['Centro_Trabajo'] == 'TRENZADO'].copy()
    df_embobina = df[df['Centro_Trabajo'] == 'EMBOBINA'].copy()

    # Sidebar de navegación
    st.sidebar.title("📁 Navegación")
    pagina = st.sidebar.radio(
    "Ir a la sección:",
    ["Principal", "Cableado", "Torsión", "Trenzado", "Embobina"]
)


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
        #Inicio pagina cableado
        st.subheader("🧵 Cableado")
        st.write("Aquí analizamos el proceso de cableado.")

        # 🎛️ Filtros
        st.markdown("### 🎛️ Filtros")
        col_f1, col_f2, col_f3, col_f4 = st.columns([2.5, 1.5, 1.5, 2])
        with col_f1:
            fechas = sorted(df_cableado['Fecha_Efectiva'].dt.date.unique())
            fecha_rango = st.date_input(
                "📅 Rango de fechas:",
                value=(fechas[0], fechas[-1])
            )
            fecha_inicio, fecha_fin = (
                fecha_rango if isinstance(fecha_rango, tuple)
                else (fecha_rango, fecha_rango)
            )
        with col_f2:
            # Definir grupos de máquinas y asignar etiquetas
            grupos = {
                'C2_C5': ['C2','C5'],
                'C3_C7': ['C3','C7'],
                'C8_C9_C10': ['C8','C9','C10'],
            }
            mapping = {}
            for grp, lst in grupos.items():
                for m in lst:
                    mapping[m] = grp
            mapping['G48C-1'] = 'Wolkman'
            # Máquinas individuales restantes
            for m in df_cableado['Maquina'].unique():
                if m not in mapping:
                    mapping[m] = m
            df_cableado['Grupo_Maq'] = df_cableado['Maquina'].map(mapping)

            # Selectbox con grupos
            opciones_grupos = ['Todas'] + sorted(df_cableado['Grupo_Maq'].unique())
            maquina_seleccionada = st.selectbox("🖨️ Grupo/Máquina:", opciones_grupos)
        with col_f3:
            turnos = ["Todos"] + sorted(df_cableado['Turno'].unique())
            turno_seleccionado = st.selectbox("🧭 Turno:", turnos)
        with col_f4:
            operarios = ["Todos"] + sorted(df_cableado['Apellidos_Nombres'].dropna().unique())
            operario_seleccionado = st.selectbox("👷 Operario:", operarios)

        # Aplico filtros
        df_fil = df_cableado[
            (df_cableado['Fecha_Efectiva'].dt.date >= fecha_inicio) &
            (df_cableado['Fecha_Efectiva'].dt.date <= fecha_fin)
        ].copy()
        if maquina_seleccionada != "Todas":
            df_fil = df_fil[df_fil['Grupo_Maq'] == maquina_seleccionada]
        if turno_seleccionado != "Todos":
            df_fil = df_fil[df_fil['Turno'] == turno_seleccionado]
        if operario_seleccionado != "Todos":
            df_fil = df_fil[df_fil['Apellidos_Nombres'] == operario_seleccionado]

        if df_fil.empty:
            st.warning("⚠️ No hay datos para los filtros seleccionados.")
        else:
            # --- Gráfico 1: Kg por máquina con productividad ---
            agg_mach = df_fil.groupby('Maquina').agg(
                Kg=('Cant_Kg','sum'),
                Corrida=('Corrida_Standar','sum'),
                Tiempo_Corrida=('Tiempo_Corrida','sum'),
                Tiempo_Perdido=('Tiempo_Perdido','sum')
            )
            agg_mach['Productividad'] = (
                agg_mach['Corrida'] /
                (agg_mach['Tiempo_Corrida'] + agg_mach['Tiempo_Perdido'])
            ) * 100
            agg_mach = agg_mach.sort_values('Kg')

            fig1, ax1 = plt.subplots(figsize=(6,4))
            bars = ax1.barh(
                agg_mach.index,
                agg_mach['Kg'],
                color='#66c2a5',
                edgecolor='white'
            )
            ax1.set_xlabel("Kg procesados", color='white')
            ax1.set_ylabel("Máquina", color='white')
            ax1.set_title("Kg por máquina (Cableado)", color='white')
            ax1.tick_params(axis='x', colors='white')
            ax1.tick_params(axis='y', colors='white')
            ax1.set_facecolor('#0e1117')
            fig1.patch.set_facecolor('#0e1117')
            # Anotar productividad
            for bar, (_, row) in zip(bars, agg_mach.iterrows()):
                w = bar.get_width()
                ax1.text(
                    w * 0.02,
                    bar.get_y() + bar.get_height()/2,
                    f"{row['Productividad']:.1f}%",
                    va='center', color='white', fontsize=9
                )
            col1, col2 = st.columns(2)
            with col1:
                st.pyplot(fig1)

            # --- Gráfico 2: Producción diaria + % productividad con grupos ---
            # Mapear máquinas a grupos
            mapping = {}
            # Definir grupos
            grupos = {
                'C2_C5': ['C2','C5'],
                'C3_C7': ['C3','C7'],
                'C8_C9_C10': ['C8','C9','C10']
            }
            for g, lst in grupos.items():
                for m in lst:
                    mapping[m] = g
            # Renombrar G48C-1
            mapping['G48C-1'] = 'Wolkman'
            # Maquinas restantes individualmente
            for m in df_fil['Maquina'].unique():
                if m not in mapping:
                    mapping[m] = m
            df_fil['Grupo'] = df_fil['Maquina'].map(mapping)

            # Datos producción diaria por grupo
            df_fil['Fecha'] = df_fil['Fecha_Efectiva'].dt.date
            prod_daily = (
                df_fil
                .groupby(['Fecha','Grupo'])['Cant_Kg']
                .sum()
                .unstack(fill_value=0)
            )
            # Calcular % productividad diaria total
            agg_day = df_fil.groupby('Fecha').agg(
                Corrida=('Corrida_Standar','sum'),
                Tiempo_Corrida=('Tiempo_Corrida','sum'),
                Tiempo_Perdido=('Tiempo_Perdido','sum')
            )
            agg_day['%_productividad'] = (
                agg_day['Corrida'] /
                (agg_day['Tiempo_Corrida'] + agg_day['Tiempo_Perdido'])
            ) * 100

            # Graficar
            with col2:
                fig2, ax2 = plt.subplots(figsize=(6,4))
                machines = prod_daily.columns.tolist()
                palette = plt.cm.tab10(np.linspace(0,1,len(machines)))
                prod_daily.plot(
                    kind='bar', stacked=True,
                    ax=ax2, color=palette, edgecolor='white'
                )
                ax2.set_xlabel('Fecha', color='white')
                ax2.set_ylabel('Kg procesados', color='white')
                ax2.tick_params(axis='x', rotation=45, colors='white')
                ax2.tick_params(axis='y', colors='white')
                ax2.set_facecolor('#0e1117')

                # Línea % productividad
                ax3 = ax2.twinx()
                line, = ax3.plot(
                    agg_day.index.astype(str),
                    agg_day['%_productividad'],
                    color='cyan', marker='o', linewidth=2,
                    label='% Productividad'
                )
                ax3.set_ylabel('% Productividad', color='cyan')
                ax3.tick_params(axis='y', colors='cyan')

                # Leyenda de grupos afuera
                leg1 = ax2.legend(
                    title='Grupos/Máquinas',
                    bbox_to_anchor=(1.02,1), loc='upper left',
                    frameon=True, facecolor='#0e1117', edgecolor='white'
                )
                for txt in leg1.get_texts(): txt.set_color('white')
                leg1.get_title().set_color('white')
                # Leyenda de productividad
                leg2 = ax3.legend(
                    handles=[line], loc='lower left',
                    frameon=True, facecolor='#0e1117', edgecolor='white'
                )
                for txt in leg2.get_texts(): txt.set_color('cyan')
                leg2.get_title().set_text('')

                ax2.set_title(
                    'Producción diaria y % Productividad (Cableado)',
                    color='white'
                )
                fig2.patch.set_facecolor('#0e1117')
                st.pyplot(fig2)

            # --- NUEVA SECCIÓN: Causas de paro y disponibilidad ---
            st.markdown("## ⏱️ Análisis de paros y disponibilidad", unsafe_allow_html=True)
            col3, col4 = st.columns(2)

            # 1) Gráfico horizontal: tiempo de paro por causa
            with col3:
                # Agrupar por código de paro (columna existente)
                paro = (
                    df_fil
                    .groupby('Causa_Paro')['Tiempo_Perdido']
                    .sum()
                    .reset_index()
                )
                paro = paro.sort_values('Tiempo_Perdido', ascending=True)
                fig3, ax3 = plt.subplots(figsize=(6,4))
                colors_paro = plt.cm.tab20c(np.linspace(0,1,len(paro)))
                ax3.barh(
                    paro['Causa_Paro'],
                    paro['Tiempo_Perdido'],
                    color=colors_paro,
                    edgecolor='white'
                )
                ax3.set_xlabel('Horas perdidas', color='white')
                ax3.set_ylabel('Causa_Paro', color='white')
                ax3.set_title('Tiempo de paro por código', color='white')
                ax3.tick_params(axis='x', colors='white')
                ax3.tick_params(axis='y', colors='white')
                ax3.set_facecolor('#0e1117')
                fig3.patch.set_facecolor('#0e1117')
                st.pyplot(fig3)

            # 2) Gráfico de disponibilidad diaria por grupo
            with col4:
                df_fil['Horas_Disponibles'] = df_fil['Tiempo_Corrida'] + df_fil['Tiempo_Perdido']
                disp = df_fil.groupby(['Fecha','Grupo'])['Horas_Disponibles'].sum().unstack(fill_value=0)
                fig4, ax4 = plt.subplots(figsize=(6,4))
                groups = disp.columns.tolist()
                palette2 = plt.cm.tab20(np.linspace(0,1,len(groups)))
                disp.plot(kind='bar', stacked=True, ax=ax4, color=palette2, edgecolor='white')
                ax4.set_xlabel('Fecha', color='white')
                ax4.set_ylabel('Horas disponibles', color='white')
                ax4.tick_params(axis='x', rotation=45, colors='white')
                ax4.tick_params(axis='y', colors='white')
                ax4.set_facecolor('#0e1117')
                ax4.set_title('Disponibilidad diaria por grupo', color='white')
                fig4.patch.set_facecolor('#0e1117')
                st.pyplot(fig4)
        
        # --- NUEVA SECCIÓN: Diámetros producidos y tabla de referencias ---
        
        st.markdown("## 🎯 Diámetros de cuerdas producidas", unsafe_allow_html=True)
        col5, col6 = st.columns(2)

        # 1) Gráfico de barras horizontales: Kg por diámetro (excluyendo referencias HC)
        df_plot = df_fil[~df_fil['Descripcion_Articulo'].str.startswith('HC')].copy()
        df_plot['Diametro'] = (
            df_plot['Descripcion_Articulo']
            .str.extract(r"(\d+)\s*MM", expand=False)
            .fillna('Desconocido')
            .apply(lambda x: f"{x}MM" if x != 'Desconocido' else x)
        )
        resumen_diam = (
            df_plot
            .groupby('Diametro')['Cant_Kg']
            .sum()
            .reset_index()
            .sort_values('Cant_Kg', ascending=True)
        )

        with col5:
            fig5, ax5 = plt.subplots(figsize=(6, 4))
            colors_d = plt.cm.Pastel1(np.linspace(0, 1, len(resumen_diam)))
            ax5.barh(
                resumen_diam['Diametro'],
                resumen_diam['Cant_Kg'],
                color=colors_d,
                edgecolor='white'
            )
            ax5.set_xlabel('Kg procesados', color='white')
            ax5.set_ylabel('Diámetro', color='white')
            ax5.set_title('Kg por diámetro de cuerda', color='white')
            ax5.tick_params(axis='x', colors='white')
            ax5.tick_params(axis='y', colors='white')
            ax5.set_facecolor('#0e1117')
            fig5.patch.set_facecolor('#0e1117')
            st.pyplot(fig5)

        # 2) Tabla de referencias y Kg (incluye todas, incluso HC)
        with col6:
            st.markdown("#### 📋 Detalle por referencia")
            tabla_refs = (
                df_fil
                .groupby('Descripcion_Articulo')['Cant_Kg']
                .sum()
                .reset_index()
                .sort_values('Cant_Kg', ascending=False)
            )
            st.dataframe(tabla_refs, use_container_width=True)

        # --- SECCIÓN FINAL: Desempeño de operarios ---
        
        st.markdown("## 👥 Desempeño de operarios", unsafe_allow_html=True)
        col7, col8 = st.columns(2)

        # Preparar datos
        df_ops = df_fil.copy()
        cambios = df_ops[df_ops["Causa_Paro"] == "CAMBIO DE REFERENCIA"]
        grp_cambios = cambios.groupby("Apellidos_Nombres").agg(
            Num_Cambios=("Causa_Paro", "count"),
            Tiempo_Perdido_Cambio=("Tiempo_Perdido", "sum"),
        )
        prod = df_ops.groupby("Apellidos_Nombres").agg(
            Cant_Kg=("Cant_Kg", "sum"),
            Corrida_Standar=("Corrida_Standar", "sum"),
            Tiempo_Corrida=("Tiempo_Corrida", "sum"),
            Tiempo_Perdido=("Tiempo_Perdido", "sum"),
        )
        df_perf = prod.join(grp_cambios, how="left").fillna({"Num_Cambios": 0, "Tiempo_Perdido_Cambio": 0})
        df_perf["prom_cambio"] = df_perf["Tiempo_Perdido_Cambio"] / df_perf["Num_Cambios"].replace(0, 1)
        df_perf["Productividad"] = (
            df_perf["Corrida_Standar"] 
            / (df_perf["Tiempo_Corrida"] + df_perf["Tiempo_Perdido"])
        ) * 100

        # Top 5 y resto
        top5 = df_perf.sort_values("Productividad", ascending=False).head(5).copy()
        others = df_perf.drop(top5.index).copy()

        # Agregar iconos
        top5["Icono"] = "🏅"
        others["Icono"] = "🔴"
        top5 = top5.reset_index()
        others = others.reset_index()

        # Tabla Top 5
        with col7:
            st.markdown("#### 🌟 Top 5 operarios por productividad")
            st.dataframe(
                top5[["Icono","Apellidos_Nombres","Cant_Kg","Num_Cambios","prom_cambio","Productividad"]],
                use_container_width=True,
            )

        # Tabla Resto de operarios
        with col8:
            st.markdown("#### ⚠️ Resto de operarios a evaluar (bajo desempeño)")
            st.dataframe(
                others[["Icono","Apellidos_Nombres","Cant_Kg","Num_Cambios","prom_cambio","Productividad"]],
                use_container_width=True,
            )



        #Fin pagina cableado
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
