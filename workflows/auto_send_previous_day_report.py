# workflows/auto_send_previous_day_report.py
import os
import json
import datetime
import requests
from io import BytesIO
import pandas as pd

from services.session_memory import CONFIG_DIR
from services.whatsapp_service import send_whatsapp_message
from workflows.daily_report import get_admin_phone

# Importar utilidades de fileteado_efficiency_report
from workflows.fileteado_efficiency_report import (
    _find_supervisors_by_keyword,
    _send_reports_to_recipients,
    _cleanup_paths,
    _normalize_phone
)

# Importar utilidades de fileteado_report
from workflows.fileteado_report import (
    _download_fileteado_df,
    _filter_by_range,
    _date_range_in_df
)

# Importar constructores de PDF y descargas de áreas
from workflows.costura_report import _download_costura_df, build_pdf_costura
from workflows.cableado_report import _download_cableado_df, build_pdf_cableado
from workflows.torsion_report import build_pdf_torsion
from workflows.trenzado_report import build_pdf_trenzado
from workflows.embobina_report import build_pdf_embobina
from workflows.impresion_report import download_impresion_df, build_pdf_impresion

from workflows.fileteado_gasa import build_pdf_gasa
from workflows.fileteado_leno import build_pdf_leno
from workflows.fileteado_planas import build_pdf_planas
from workflows.fileteado_cortadoras import build_pdf_cortadoras

# Archivo de estado para registrar los envíos exitosos
SEND_LOG_FILE = os.path.join(CONFIG_DIR, "auto_send_report_log.json")

def get_previous_workday(d: datetime.date) -> datetime.date:
    """
    Calcula el día anterior hábil (L-V). Si hoy es lunes, el día anterior hábil es el viernes anterior.
    """
    if d.weekday() == 0:  # Lunes
        return d - datetime.timedelta(days=3)
    elif d.weekday() == 6:  # Domingo
        return d - datetime.timedelta(days=2)
    elif d.weekday() == 5:  # Sábado
        return d - datetime.timedelta(days=1)
    else:
        return d - datetime.timedelta(days=1)

def check_and_send_report():
    """
    Evalúa si la base de datos de Costura ya tiene datos del día anterior hábil.
    Si ya fue actualizada, genera y envía los reportes PDF de eficiencia para todas las áreas.
    """
    today = datetime.date.today()
    target_date = get_previous_workday(today)
    date_str = target_date.strftime('%d/%m/%Y')
    
    # 1. Verificar si ya se envió hoy para no duplicar
    if os.path.exists(SEND_LOG_FILE):
        try:
            with open(SEND_LOG_FILE, "r", encoding="utf-8") as f:
                log = json.load(f)
            if log.get("last_sent_date") == str(today):
                print(f"ℹ️ El reporte de eficiencia para el día {target_date} ya fue enviado hoy ({today}).")
                return
        except Exception as e:
            print(f"⚠️ Error leyendo log de envíos: {e}")
            
    # Obtener el teléfono del administrador
    admin_phone_raw = get_admin_phone()
    if not admin_phone_raw:
        admin_phone = "573176380061"  # Fallback Wilson
    else:
        admin_phone = _normalize_phone(admin_phone_raw)

    # 2. Descargar Costura como validador de actualización general de la base de datos
    print(f"🔍 Descargando base de Costura para validar actualización al {target_date}...")
    try:
        df_cos = _download_costura_df()
        if df_cos is None or df_cos.empty:
            print("⚠️ No se pudo descargar la base de datos de Costura o está vacía.")
            return
            
        _, _, col_cos = _date_range_in_df(df_cos)
        if not col_cos:
            print("⚠️ No se detectó la columna de fecha en Costura.")
            return
            
        df_cos[col_cos] = pd.to_datetime(df_cos[col_cos], errors="coerce")
        df_cos_target = df_cos[df_cos[col_cos].dt.date == target_date]
        
        if df_cos_target.empty:
            print(f"⏳ La base de datos aún no tiene registros de Costura para el {target_date}.")
            return
            
        print(f"✅ ¡Base de datos actualizada para el {target_date}! Generando reportes de eficiencia...")
        
        # Variable para validar si se envió al menos un reporte
        enviado_alguno = False
        
        # --- ÁREA 1: COSTURA ---
        try:
            print("📄 Procesando Costura...")
            df_cos_sel = _filter_by_range(df_cos, target_date, target_date, col_cos)
            if not df_cos_sel.empty:
                p, t = build_pdf_costura(df_cos_sel, target_date, target_date)
                if p:
                    temp_paths = [p, *(t or [])]
                    sups = _find_supervisors_by_keyword("costura")
                    sup_phones = {phone for _, phone in sups}
                    
                    # Destinatarios del documento PDF (Solo Supervisores)
                    doc_recipients = sup_phones.copy()
                        
                    _send_reports_to_recipients(doc_recipients, p, f"📄 EFICIENCIA COSTURA - {date_str}")
                    _cleanup_paths(temp_paths)
                    
                    # Enviar nota de invitación solo a los supervisores de la sección
                    for phone in sup_phones:
                        msg = (
                            f"🤖 *CiplasBot Informa*:\n"
                            f"Hola, se ha generado el reporte de eficiencia de *COSTURA* para el día *{date_str}*.\n\n"
                            f"Te invitamos a revisar el archivo PDF adjunto y realizar el seguimiento respectivo. 📊📈"
                        )
                        send_whatsapp_message(phone, msg)
                        
                    enviado_alguno = True
                    print("✅ Reporte de Costura enviado.")
            else:
                print("ℹ️ Sin datos de Costura para la fecha.")
        except Exception as e:
            print(f"❌ Error en reporte de Costura: {e}")

        # --- ÁREA 2: CUERDAS ---
        try:
            print("📄 Procesando Cuerdas...")
            df_cue = _download_cableado_df()
            if df_cue is not None and not df_cue.empty:
                _, _, col_cue = _date_range_in_df(df_cue)
                df_cue_sel = _filter_by_range(df_cue, target_date, target_date, col_cue)
                if not df_cue_sel.empty:
                    temp_paths = []
                    sups = _find_supervisors_by_keyword("cuerdas")
                    sup_phones = {phone for _, phone in sups}
                    
                    doc_recipients = sup_phones.copy()
                        
                    for func, name in [(build_pdf_cableado, "CABLEADO"), (build_pdf_torsion, "TORSIÓN"), 
                                       (build_pdf_trenzado, "TRENZADO"), (build_pdf_embobina, "EMBOBINADO")]:
                        p, t = func(df_cue_sel, target_date, target_date)
                        if p:
                            temp_paths.extend([p, *(t or [])])
                            _send_reports_to_recipients(doc_recipients, p, f"📄 EFICIENCIA {name} - {date_str}")
                            
                    _cleanup_paths(temp_paths)
                    
                    for phone in sup_phones:
                        msg = (
                            f"🤖 *CiplasBot Informa*:\n"
                            f"Hola, se han generado los reportes de eficiencia de *CUERDAS* (Cableado, Torsión, Trenzado, Embobinado) para el día *{date_str}*.\n\n"
                            f"Te invitamos a revisar los archivos PDF adjuntos y realizar el seguimiento respectivo. 📊📈"
                        )
                        send_whatsapp_message(phone, msg)
                        
                    enviado_alguno = True
                    print("✅ Reportes de Cuerdas enviados.")
                else:
                    print("ℹ️ Sin datos de Cuerdas para la fecha.")
        except Exception as e:
            print(f"❌ Error en reportes de Cuerdas: {e}")

        # --- ÁREA 3: IMPRESIÓN ---
        try:
            print("📄 Procesando Impresión...")
            df_imp = download_impresion_df()
            if df_imp is not None and not df_imp.empty:
                _, _, col_imp = _date_range_in_df(df_imp)
                df_imp_sel = _filter_by_range(df_imp, target_date, target_date, col_imp)
                if not df_imp_sel.empty:
                    p, t = build_pdf_impresion(df_imp_sel, target_date, target_date)
                    if p:
                        temp_paths = [p, *(t or [])]
                        sups = _find_supervisors_by_keyword("impresion")
                        sup_phones = {phone for _, phone in sups}
                        
                        doc_recipients = sup_phones.copy()
                            
                        _send_reports_to_recipients(doc_recipients, p, f"📄 EFICIENCIA IMPRESIÓN - {date_str}")
                        _cleanup_paths(temp_paths)
                        
                        for phone in sup_phones:
                            msg = (
                                f"🤖 *CiplasBot Informa*:\n"
                                f"Hola, se ha generado el reporte de eficiencia de *IMPRESIÓN* para el día *{date_str}*.\n\n"
                                f"Te invitamos a revisar el archivo PDF adjunto y realizar el seguimiento respectivo. 📊📈"
                            )
                            send_whatsapp_message(phone, msg)
                            
                        enviado_alguno = True
                        print("✅ Reporte de Impresión enviado.")
                else:
                    print("ℹ️ Sin datos de Impresión para la fecha.")
        except Exception as e:
            print(f"❌ Error en reporte de Impresión: {e}")

        # --- ÁREA 4: FILETEADO ---
        try:
            print("📄 Procesando Fileteado...")
            df_fil = _download_fileteado_df()
            if df_fil is not None and not df_fil.empty:
                _, _, col_fil = _date_range_in_df(df_fil)
                df_fil_sel = _filter_by_range(df_fil, target_date, target_date, col_fil)
                if not df_fil_sel.empty:
                    temp_paths = []
                    sups = _find_supervisors_by_keyword("fileteado")
                    sup_phones = {phone for _, phone in sups}
                    
                    doc_recipients = sup_phones.copy()
                        
                    for f, n in [(build_pdf_gasa, "GASA"), (build_pdf_leno, "LENO"), 
                                 (build_pdf_planas, "PLANAS"), (build_pdf_cortadoras, "CORTADORAS")]:
                        p, t = f(df_fil_sel, target_date, target_date)
                        if p:
                            temp_paths.extend([p, *(t or [])])
                            _send_reports_to_recipients(doc_recipients, p, f"📄 EFICIENCIA {n} - {date_str}")
                            
                    _cleanup_paths(temp_paths)
                    
                    for phone in sup_phones:
                        msg = (
                            f"🤖 *CiplasBot Informa*:\n"
                            f"Hola, se han generado los reportes de eficiencia de *FILETEADO* (Gasa, Leno, Planas, Cortadoras) para el día *{date_str}*.\n\n"
                            f"Te invitamos a revisar los archivos PDF adjuntos y realizar el seguimiento respectivo. 📊📈"
                        )
                        send_whatsapp_message(phone, msg)
                        
                    enviado_alguno = True
                    print("✅ Reportes de Fileteado enviados.")
                else:
                    print("ℹ️ Sin datos de Fileteado para la fecha.")
        except Exception as e:
            print(f"❌ Error en reportes de Fileteado: {e}")

        # --- ENVIAR INFORME MACRO-GERENCIAL AL ADMINISTRADOR ---
        if enviado_alguno and admin_phone:
            try:
                from workflows.manager_planta_report import handle_manager_planta_report
                
                admin_msg = (
                    f"🤖 *CiplasBot Informa*:\n"
                    f"Hola Administrador, se han generado y enviado con éxito los reportes de eficiencia del día *{date_str}* a los supervisores.\n\n"
                    f"A continuación, se generará tu *Informe Macro-Gerencial de Planta* consolidado para el día *{date_str}*. 📊🏢"
                )
                send_whatsapp_message(admin_phone, admin_msg)
                
                # Ejecutar la generación del reporte macro-gerencial del día anterior
                handle_manager_planta_report(
                    phone=admin_phone,
                    manager_name="Administrador",
                    to_norm=admin_phone,
                    target_date=target_date
                )
                print("✅ Informe Macro-Gerencial enviado al administrador.")
            except Exception as e:
                print(f"❌ Error enviando informe Macro-Gerencial al administrador: {e}")

        # 4. Registrar el envío exitoso
        if enviado_alguno:
            try:
                with open(SEND_LOG_FILE, "w", encoding="utf-8") as f:
                    json.dump({
                        "last_sent_date": str(today),
                        "target_date": str(target_date),
                        "sent_at": datetime.datetime.now().isoformat()
                    }, f, indent=2)
                print(f"💾 Registro de envío guardado para hoy ({today}).")
            except Exception as e:
                print(f"⚠️ Error guardando log de envíos: {e}")
            
    except Exception as e:
        print(f"❌ Error general en proceso de auto-envío: {e}")
