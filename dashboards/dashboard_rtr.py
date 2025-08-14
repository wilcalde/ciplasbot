# dashboardrtr.py
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

    # ------------------ TARJETAS ------------------
    st.markdown("<h4>📊 Producción y tiempos perdidos</h4>", unsafe_allow_html=True)

    if df_filtrado.empty:
        st.info("No hay datos para el rango/filtrado seleccionado.")
    else:
        # Agrupar por máquina y turno (incluye Corrida_Standar para productividad)
        resumen_tarjetas = df_filtrado.groupby(["Maquinas", "Turno"]).agg({
            'Cantidad_Completada': 'sum',
            'Tiempo_Corrida': 'sum',
            'Tiempo_Perdido': 'sum',
            'Corrida_Standar': 'sum'
        }).reset_index()

        # Calcular velocidad promedio, tiempo disponible y productividad por turno
        resumen_tarjetas["Velocidad_Promedio"] = (
            resumen_tarjetas["Cantidad_Completada"] / (resumen_tarjetas["Tiempo_Corrida"] * 60)
        )
        resumen_tarjetas["Tiempo_Disponible"] = resumen_tarjetas["Tiempo_Corrida"] + resumen_tarjetas["Tiempo_Perdido"]
        resumen_tarjetas["Productividad_pct"] = (
            (resumen_tarjetas["Corrida_Standar"] / resumen_tarjetas["Tiempo_Disponible"]) * 100
        ).replace([float('inf'), -float('inf')], 0).fillna(0).round(1)

        # Lista única de máquinas
        maquinas_unicas = sorted(resumen_tarjetas["Maquinas"].unique())
        st.markdown("🕒 <b>Resumen de desempeño por máquina y turno</b>", unsafe_allow_html=True)
        cols_tarjetas = st.columns(len(maquinas_unicas))

        for idx, maquina in enumerate(maquinas_unicas):
            df_maquina = resumen_tarjetas[resumen_tarjetas["Maquinas"] == maquina]

            # ➕ Totales por máquina (para velocidad total m/min)
            total_cant = df_maquina["Cantidad_Completada"].sum()
            total_tcorr = df_maquina["Tiempo_Corrida"].sum()
            total_vel = (total_cant / (total_tcorr * 60)) if total_tcorr > 0 else 0.0

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

            # 🔚 Línea final con la velocidad total de la máquina
            texto_turnos += (
                f"<div style='margin-top:8px; padding-top:8px; border-top:1px solid #142233'>"
                f"<p><b>⚡ Velocidad total:</b> {total_vel:.1f} m/min</p>"
                f"</div>"
            )

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
    # ------------------ /TARJETAS ------------------

    #***** GRÁFICOS
    col_g1, col_g2, col_g3 = st.columns(3)

    # === BLOQUE ACTUALIZADO: barras con % de productividad dentro de cada barra (Blues, ascendente) ===
    with col_g1:
        st.markdown("<h5>🔄 Distribución por línea</h5>", unsafe_allow_html=True)

        if df_filtrado.empty:
            st.info("No hay datos para el rango/filtrado seleccionado.")
        else:
            agg = (
                df_filtrado.groupby('Maquinas', as_index=False)
                .agg(
                    Cantidad_Completada=('Cantidad_Completada', 'sum'),
                    Corrida_Standar=('Corrida_Standar', 'sum'),
                    Tiempo_Corrida=('Tiempo_Corrida', 'sum'),
                    Tiempo_Perdido=('Tiempo_Perdido', 'sum')
                )
            )
            agg['Tiempo_Disponible'] = agg['Tiempo_Corrida'] + agg['Tiempo_Perdido']
            agg['Prod_pct'] = np.where(
                agg['Tiempo_Disponible'] > 0,
                (agg['Corrida_Standar'] / agg['Tiempo_Disponible']) * 100,
                0
            )
            # Ordenar de menor a mayor por cantidad (preferencia del usuario)
            agg = agg.sort_values('Cantidad_Completada', ascending=True)

            # Colores Blues
            cmap = plt.cm.Blues
            colors = cmap(np.linspace(0.45, 0.95, len(agg)))

            fig_linea, ax_linea = plt.subplots(figsize=(5, 3.5))
            bars = ax_linea.barh(
                agg['Maquinas'],
                agg['Cantidad_Completada'],
                color=colors
            )

            ax_linea.set_xlabel("Cantidad", color="white")
            ax_linea.set_ylabel("Maquinas", color="white")
            ax_linea.set_title("Distribución por línea", color="white")
            ax_linea.tick_params(colors='white')

            max_x = agg['Cantidad_Completada'].max() if len(agg) else 0
            for bar, pct in zip(bars, agg['Prod_pct'].round(1)):
                width = bar.get_width()
                y = bar.get_y() + bar.get_height() / 2
                label = f"{pct:.1f}%"
                if max_x and width > 0.15 * max_x:
                    ax_linea.text(
                        width * 0.98, y, label,
                        va='center', ha='right',
                        color='white', fontsize=9, fontweight='bold'
                    )
                else:
                    ax_linea.text(
                        width + (0.02 * max_x), y, label,
                        va='center', ha='left',
                        color='white', fontsize=9, fontweight='bold'
                    )

            fig_linea.tight_layout()
            st.pyplot(fig_linea)
    # === FIN BLOQUE ACTUALIZADO ===

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
        import numpy as np

        if df_filtrado.empty:
            st.info("No hay datos para el rango/filtrado seleccionado.")
        else:
            # Copia y limpieza básica
            df_vel = df_filtrado.copy()

            # Solo consideramos registros con tiempo de corrida positivo
            df_vel = df_vel[df_vel['Tiempo_Corrida'] > 0].copy()

            # Fecha (día) para separar rodajes por día
            df_vel['Fecha'] = df_vel['Fecha_Efectiva'].dt.date

            # --- RODAJE = Maquina + Descripcion_Articulo + Fecha ---
            # Unificamos turnos del mismo día para la misma referencia (rodaje completo del día)
            agg_rodajes = (
                df_vel
                .groupby(['Maquinas', 'Descripcion_Articulo', 'Fecha'], as_index=False)
                .agg(
                    Cantidad_Completada=('Cantidad_Completada', 'sum'),
                    Tiempo_Corrida=('Tiempo_Corrida', 'sum')
                )
            )

            # Velocidad promedio del rodaje (m/min)
            agg_rodajes = agg_rodajes[agg_rodajes['Tiempo_Corrida'] > 0]
            agg_rodajes['Velocidad'] = agg_rodajes['Cantidad_Completada'] / (agg_rodajes['Tiempo_Corrida'] * 60)

            # Filtrado de outliers/valores no realistas (ajusta si lo necesitas)
            agg_rodajes = agg_rodajes[(agg_rodajes['Velocidad'] >= 20) & (agg_rodajes['Velocidad'] < 1000)]

            if agg_rodajes.empty:
                st.info("No hay rodajes válidos para graficar con los filtros actuales.")
            else:
                fig_disp, ax_disp = plt.subplots(figsize=(5, 3.5))

                maquinas_ordenadas = sorted(agg_rodajes['Maquinas'].unique())
                for i, maquina in enumerate(maquinas_ordenadas):
                    y_vals = agg_rodajes.loc[agg_rodajes['Maquinas'] == maquina, 'Velocidad'].values
                    # Puntos por máquina (columna de puntos). Si prefieres separarlos un poco:
                    # x_vals = np.random.normal(loc=i, scale=0.05, size=len(y_vals))  # con leve jitter
                    x_vals = [i] * len(y_vals)
                    ax_disp.scatter(x_vals, y_vals, alpha=0.7, s=25, edgecolors='none', color='deepskyblue')

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
    #*********************
    import numpy as np
import matplotlib.dates as mdates

if df_filtrado.empty:
    st.info("No hay datos para el rango/filtrado seleccionado.")
else:
    # 1) Preparación y filtros básicos
    dfl = df_filtrado.copy()
    dfl = dfl[dfl["Tiempo_Corrida"] > 0].copy()
    dfl["Fecha"] = dfl["Fecha_Efectiva"].dt.date

    # 2) Promedio DIARIO por MÁQUINA (unificando turnos): vel = sum(cant) / (sum(t_corrida)*60)
    by_day_machine = (
        dfl.groupby(["Fecha", "Maquinas"], as_index=False)
           .agg(Cant=("Cantidad_Completada", "sum"),
                Tcorr=("Tiempo_Corrida", "sum"))
    )
    by_day_machine["Velocidad"] = by_day_machine["Cant"] / (by_day_machine["Tcorr"] * 60)
    # Opcional: filtrar valores irreales
    by_day_machine = by_day_machine[np.isfinite(by_day_machine["Velocidad"])]

    # 3) Pivot para series por máquina
    pivot_speed = (
        by_day_machine.pivot(index="Fecha", columns="Maquinas", values="Velocidad")
                      .sort_index()
    )

    # 4) Promedio TOTAL diario CONSOLIDADO (todas las máquinas)
    total_daily = (
        by_day_machine.groupby("Fecha", as_index=False)
                      .agg(Cant=("Cant", "sum"), Tcorr=("Tcorr", "sum"))
                      .sort_values("Fecha")
    )
    total_daily["Vel_total"] = total_daily["Cant"] / (total_daily["Tcorr"] * 60)

    # 5) Figura 2x3: 5 máquinas + panel final con total
    fig, axes = plt.subplots(2, 3, figsize=(15, 10), sharex=True, sharey=True)
    axes = axes.flatten()

    maquinas = list(pivot_speed.columns)
    n_axes_for_machines = min(5, len(axes) - 1)  # el último es para el total

    # 6) Subplots por máquina + línea del promedio total diario
    for i, maquina in enumerate(maquinas[:n_axes_for_machines]):
        ax = axes[i]
        serie = pivot_speed[maquina].dropna()

        # línea de la máquina
        ax.plot(serie.index, serie.values, marker="o", linewidth=1.8, color="deepskyblue", label=maquina)
        # línea del promedio total del día (todas las máquinas)
        ax.plot(total_daily["Fecha"], total_daily["Vel_total"], linestyle="--", linewidth=1.6,
                color="yellow", label="Total diario")

        ax.set_title(maquina)
        ax.grid(True, linestyle="--", alpha=0.4)
        ax.set_xlabel("Fecha")
        ax.set_ylabel("Velocidad (m/min)")

    # apaga ejes sobrantes (si hay menos de 5 máquinas en filtros)
    for k in range(len(maquinas[:n_axes_for_machines]), len(axes) - 1):
        axes[k].axis("off")

    # 7) Subplot final: promedio total consolidado + tendencia
    ax_total = axes[-1]
    ax_total.plot(total_daily["Fecha"], total_daily["Vel_total"], marker="o",
                  color="yellow", linewidth=2, label="Promedio total", zorder=3)

    if len(total_daily) >= 2 and np.isfinite(total_daily["Vel_total"]).all():
        xnum = mdates.date2num(total_daily["Fecha"])
        coef = np.polyfit(xnum, total_daily["Vel_total"], 1)
        trend = np.poly1d(coef)
        ax_total.plot(total_daily["Fecha"], trend(xnum),
                      linestyle="--", color="red", linewidth=3, label="Tendencia", zorder=4)

    ax_total.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    for lbl in ax_total.get_xticklabels():
        lbl.set_rotation(45)
        lbl.set_ha("right")

    ax_total.set_title("Promedio total con tendencia")
    ax_total.grid(True, linestyle="--", alpha=0.4)
    ax_total.set_xlabel("Fecha")
    ax_total.set_ylabel("Velocidad (m/min)")
    ax_total.legend(loc="upper left")

    fig.tight_layout()
    st.pyplot(fig)
#**************************************
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
        df_m = df_tiempo[df_tiempo["Maquinas"] == maquina]
        if df_m.empty:
            graficos.append(None)
            continue

        resumen_causas = (
            df_m.groupby("Causa_Paro")["Tiempo_Perdido"]
            .sum()
            .sort_values(ascending=False)
            .round(2)
        )

        fig_m, ax_m = plt.subplots(figsize=(4.5, 3.5))
        resumen_causas.plot(kind="barh", ax=ax_m, color="tomato")
        ax_m.invert_yaxis()
        ax_m.set_title(f"{maquina}", color="white", fontsize=10)
        ax_m.set_xlabel("Horas", color="white")
        ax_m.set_ylabel("Causa", color="white")
        ax_m.tick_params(colors="white", labelsize=8)
        fig_m.patch.set_facecolor('#000000')
        ax_m.set_facecolor('#000000')
        graficos.append(fig_m)

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

    # Fin sección tiempos perdidos

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

    # ---- Sección: volúmenes por referencia y máquina ----
    st.markdown("<h4>📊 Análisis de volúmenes por referencia y máquina</h4>", unsafe_allow_html=True)

    df_ref = df_filtrado.copy()

    resumen_ref = (
        df_ref
        .groupby(['Descripcion_Articulo', 'Maquinas'], as_index=False)
        .agg(metros_continuos=('Cantidad_Completada', 'sum'))
    )

    resumen_ref = resumen_ref[resumen_ref['metros_continuos'] > 0]

    bins = [0, 3000, 5000, float('inf')]
    labels = ['<3000', '3000-5000', '>5000']
    resumen_ref['categoria'] = pd.cut(
        resumen_ref['metros_continuos'],
        bins=bins,
        labels=labels,
        right=False
    )

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

    cat_counts = resumen_ref['categoria'].value_counts().reindex(labels).fillna(0)
    fig_pie, ax_pie = plt.subplots(figsize=(4, 4))
    ax_pie.pie(
        cat_counts,
        labels=labels,
        autopct='%1.1f%%',
        textprops={'color': 'white'}
    )
    #*****************
    ax_pie.set_title("Distribución por tamaño de pedido", color="white")
    fig_pie.patch.set_facecolor('#000000')
    ax_pie.set_facecolor('#000000')
    fig_pie.tight_layout()

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
        if len(row)==1:
            with cols[1]:
                st.pyplot(fig_pie)