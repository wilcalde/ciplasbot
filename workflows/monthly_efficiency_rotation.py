# workflows/monthly_efficiency_rotation.py
import sqlite3
import os
import pandas as pd
from datetime import date
from dateutil.relativedelta import relativedelta
from services.session_memory import CONFIG_DIR
from services.whatsapp_service import send_whatsapp_message

# Configuración de Rutas y Contacto
DB_PATH = os.path.join(CONFIG_DIR, "tasks", "base_conversion_eficiencias_conversion.db")
# Número directo solicitado por Wilson
ADMIN_PHONE = "573176380061"

MESES_ES = {1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
            7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"}

def rotate_efficiency_database():
    """Ejecuta la rotación mensual: Guarda el mes anterior y borra el más antiguo."""
    today = date.today()
    # Mes a guardar (el que acaba de terminar)
    target_month = today - relativedelta(months=1)
    # Mes a eliminar (hace 4 meses, para mantener ventana de 3)
    oldest_month = today - relativedelta(months=4)
    
    col_ef_nueva = f"eficiencia_{MESES_ES[target_month.month]}_{target_month.year}"
    col_pr_nueva = f"produccion_{MESES_ES[target_month.month]}_{target_month.year}"
    
    col_ef_vieja = f"eficiencia_{MESES_ES[oldest_month.month]}_{oldest_month.year}"
    col_pr_vieja = f"produccion_{MESES_ES[oldest_month.month]}_{oldest_month.year}"
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    log_verificacion = []
    
    try:
        # 1. Asegurar que las columnas del mes nuevo existan en 'hoja1'
        for col in [col_ef_nueva, col_pr_nueva]:
            try:
                tipo = "TEXT" if "eficiencia" in col else "REAL"
                cursor.execute(f"ALTER TABLE hoja1 ADD COLUMN {col} {tipo}")
            except sqlite3.OperationalError:
                pass # La columna ya existe

        # 2. Obtener y actualizar eficiencias de todas las secciones
        secciones = ["fileteado", "costura", "cuerdas", "impresion"]
        for sec in secciones:
            df_sec = _obtener_datos_finales_seccion(sec, target_month)
            
            muestras = 0
            if not df_sec.empty:
                for _, row in df_sec.iterrows():
                    nombre = str(row.get('Nombre', row.get('nombre', ''))).strip().upper()
                    if not nombre or nombre == "NAN": continue
                    
                    # Extraer métricas (efic y prod)
                    efic = float(row.get('%efic', row.get('%eficiencia', row.get('promedio', 0))))
                    prod = float(row.get('produccion', row.get('Produccion', 0)))

                    # Asegurar que el operario existe
                    cursor.execute("INSERT OR IGNORE INTO hoja1 (nombre, area) VALUES (?, ?)", (nombre, sec))
                    
                    # Actualizar mes específico
                    cursor.execute(f"UPDATE hoja1 SET {col_ef_nueva} = ?, {col_pr_nueva} = ? WHERE nombre = ?", 
                                   (efic, prod, nombre))
                    
                    if muestras < 2:
                        log_verificacion.append(f"📍 *{sec.upper()}*: {nombre} -> {efic:.2f}%")
                        muestras += 1

        # 3. Eliminar el mes más antiguo para mantener ventana de 3 meses
        for col in [col_ef_vieja, col_pr_vieja]:
            try:
                cursor.execute(f"ALTER TABLE hoja1 DROP COLUMN {col}")
            except sqlite3.OperationalError:
                pass 

        conn.commit()
        
        # 4. Notificar a Wilson
        msg = (f"🔄 *Rotación Mensual Eficiencias*\n"
               f"✅ Actualizado: {MESES_ES[target_month.month].capitalize()} {target_month.year}\n"
               f"🗑️ Eliminado: {MESES_ES[oldest_month.month].capitalize()} {oldest_month.year}\n\n"
               "*Muestra de Verificación:*\n" + "\n".join(log_verificacion))
        send_whatsapp_message(ADMIN_PHONE, msg)

    except Exception as e:
        send_whatsapp_message(ADMIN_PHONE, f"❌ Error en rotación mensual: {str(e)}")
    finally:
        conn.close()

def _obtener_datos_finales_seccion(sec, fecha):
    """Puente para obtener datos calculados de cada sección."""
    try:
        if sec == "impresion":
            from workflows.impresion_report import download_impresion_df, prepare_change_analysis
            df = download_impresion_df()
            return prepare_change_analysis(df)
        # Aquí se conectarían las demás secciones similarmente
        return pd.DataFrame()
    except:
        return pd.DataFrame()