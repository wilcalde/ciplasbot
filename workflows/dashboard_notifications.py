import os
import json
import datetime
from services.whatsapp_service import send_whatsapp_message

# Ruta al archivo de configuración de usuarios
CONFIG_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '..', 'config', 'users.json')  # ahora apunta a 'users.json'
)

def load_users():
    """
    Carga la lista de usuarios desde config/users.json
    """
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get('users', [])
    except Exception as e:
        print(f"Error cargando usuarios: {e}")
        return []


def send_dashboard_links():
    """
    Envía a cada supervisor el link de su dashboard con los resultados del día anterior
    y envía al administrador un listado con todos los enlaces.
    """
    # Fecha de 'ayer'
    today = datetime.date.today()
    yesterday_str = (today - datetime.timedelta(days=1)).strftime("%d/%m/%Y")

    users = load_users()
    # Filtrar supervisores y administradores
    supervisors = [u for u in users if u.get('role', '').lower() == 'supervisor']
    admins      = [u for u in users if u.get('role', '').lower() == 'administrador']

    # Enviar a cada supervisor
    for user in supervisors:
        name    = user.get('name')
        process = user.get('process')
        phone   = user.get('phone')
        url     = user.get('url')

        if not phone or not url:
            print(f"⚠️ Datos incompletos para {name}: phone={phone}, url={url}")
            continue

        mensaje = (
            f"👋 Hola {name},\n"
            f"El dashboard de *{process.capitalize()}* con los resultados del {yesterday_str} ya está disponible:\n"
            f"{url}\n\n"
            "Revisa y analiza los datos para tomar decisiones. 👍"
        )
        try:
            send_whatsapp_message(phone, mensaje)
        except Exception as e:
            print(f"Error enviando dashboard a {name} ({phone}): {e}")

    # Construir y enviar mensaje al administrador con todos los enlaces
    if supervisors and admins:
        enlaces = "\n".join(
            [f"- {u.get('process').capitalize()}: {u.get('url')}" for u in supervisors]
        )
        admin_message = (
            f"👋 Hola Administrador,\n"
            f"Aquí tienes los enlaces a todos los dashboards (resultados del {yesterday_str}):\n{enlaces}"
        )
        for admin in admins:
            phone = admin.get('phone')
            try:
                send_whatsapp_message(phone, admin_message)
            except Exception as e:
                print(f"Error enviando al administrador ({phone}): {e}")
