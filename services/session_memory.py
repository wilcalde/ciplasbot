# services/session_memory.py

import os

# 📁 Rutas y carpetas necesarias para la gestión de sesiones y alertas
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "../config")
SUPERVISORS_FILE = os.path.join(CONFIG_DIR, "users.json")
ALERT_LOG_FILE = os.path.join(CONFIG_DIR, "alert_log.json")

# 🗂️ Asegura que la carpeta config exista
os.makedirs(CONFIG_DIR, exist_ok=True)

# 📦 Diccionario global para mantener los flujos activos por número de teléfono
sessions = {}
