import streamlit as st
import pandas as pd
import requests
import io
from datetime import datetime
import matplotlib.pyplot as plt

# Título del dashboard
st.title("📊 Informe Diario de Producción - Fileteado")

# Fecha del informe
fecha_actual = datetime.now().strftime("%d/%m/%Y")
st.markdown(f"**🗓 Fecha del informe:** {fecha_actual}")

# URL del archivo Excel
url = 'https://docs.google.com/spreadsheets/d/1FYLgfQhLvCUtiuxGnn5aQK6aCChoFPmMU-eMa0KAHrg/export?format=xlsx'

# Paso 1: Descargar el archivo
response = requests.get(url)

if response.status_code == 200:
    # Paso 2: Convertir el contenido descargado en un objeto similar a un archivo
    excel_data = io.BytesIO(response.content)

    # Paso 3: Leer todas las hojas del archivo Excel en un diccionario de DataFrames
    dfs = pd.read_excel(excel_data, sheet_name=None)

    # Seleccionar la hoja deseada ("Fileteado")
    df = dfs["Fileteado"]

    # Ajustar formato de fecha
    df['Fecha_Efectiva'] = pd.to_datetime(df['Fecha_Efectiva'])

    # Filtrar registros con artículos válidos
    df = df[df["Numero_Articulo"] != "ESP00001"]

    # Unificar mayúsculas en la columna Turno
    df['Turno'] = df['Turno'].astype(str).str.upper()

    # Crear columna de agrupación personalizada
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

    # 📊 Gráfico de barras apiladas por día y línea principal
    st.subheader("📈 Producción diaria por línea (planas, filete_gasa, filete_leno)")
    df_graf = df[df['Linea'].isin(['planas', 'filete_gasa', 'filete_leno'])]
    grafico = df_graf.groupby([df_graf['Fecha_Efectiva'].dt.date, 'Linea'])['Cantidad_Completada'].sum().unstack(fill_value=0)

    fig, ax = plt.subplots(figsize=(10, 5))
    grafico.plot(kind='bar', stacked=True, ax=ax)
    ax.set_ylabel("Unidades producidas")
    ax.set_xlabel("Fecha")
    ax.set_title("Producción diaria por línea")
    ax.legend(title="Línea")
    st.pyplot(fig)

    # Filtro por fecha
    fechas_disponibles = sorted(df['Fecha_Efectiva'].dt.date.unique(), reverse=True)
    fecha_seleccionada = st.selectbox("Selecciona una fecha de producción:", fechas_disponibles)
    df = df[df['Fecha_Efectiva'].dt.date == fecha_seleccionada]

    # Filtro por línea de producción
    lineas_disponibles = df['Linea'].unique()
    linea_seleccionada = st.selectbox("Selecciona una línea de producción:", sorted(lineas_disponibles))
    df = df[df['Linea'] == linea_seleccionada]

    # Filtro por turno con opción múltiple
    turnos_disponibles = sorted(df['Turno'].dropna().unique())
    opciones_turno = ["Todos"] + turnos_disponibles
    turno_seleccionado = st.selectbox("Selecciona un turno:", opciones_turno)

    if turno_seleccionado != "Todos":
        df_filtrado = df[df['Turno'] == turno_seleccionado]
    else:
        df_filtrado = df

    # Texto con unidades producidas por cada línea filtrada
    total_unidades = df_filtrado['Cantidad_Completada'].sum()
    st.markdown(f"### 🔢 Total unidades producidas en la línea **{linea_seleccionada}**, turno **{turno_seleccionado}**: {total_unidades:,.0f}")

    # Mostrar datos filtrados por línea y turno
    st.subheader(f"📌 Datos de producción para la línea: {linea_seleccionada}, turno: {turno_seleccionado}")
    st.dataframe(df_filtrado)

    # Unidades producidas por cada línea
    st.subheader("🏭 Producción total por Línea de Fileteado")
    resumen = df.groupby('Linea')['Cantidad_Completada'].sum().reset_index()
    resumen = resumen.rename(columns={"Cantidad_Completada": "Total_Producido"})
    st.dataframe(resumen)

else:
    st.error(f"❌ Error al descargar el archivo. Código de estado: {response.status_code}")
