# services/prompts.py

FLOW_MAP = {
    "COSTURA": ["PROGRAMADAS", "PARADAS", "RESUMEN_PARO", "GENERAL_NOTES"],
    "CUERDAS": ["PERSONAL", "OPERANDO", "PARADAS", "GENERAL_NOTES"],
    "FILETEADO": ["PERSONAL", "PROGRAMADAS", "INVENTARIO", "PARADAS", "GENERAL_NOTES"],
    "IMPRESION RTR": ["PERSONAL", "OPERANDO", "PARADAS", "GENERAL_NOTES"],
    "IMPRESION GRAFICA": ["PERSONAL", "OPERANDO", "PARADAS", "GENERAL_NOTES"]
}

PROMPT_MAP = {
    "PERSONAL": {
        "COSTURA": "📌 Indica el personal ausente en COSTURA (nombre y causa):",
        "CUERDAS": "📌 Indica el personal ausente en CUERDAS (nombre y causa):",
        "FILETEADO": "📌 Indica el personal ausente en FILETEADO (nombre y causa):",
        "IMPRESION RTR": "📌 Indica el personal ausente en IMPRESIÓN RTR (nombre y causa):",
        "IMPRESION GRAFICA": "📌 Indica el personal ausente en IMPRESIÓN GRÁFICA (nombre y causa):"
    },
    "PROGRAMADAS": {
        "COSTURA": "📌 ¿Cuántas máquinas están programadas a trabajar hoy en COSTURA?",
        "FILETEADO": "📌 Indica número de puestos programados en Gasa, Leno y Plana:"
    },
    "OPERANDO": {
        "CUERDAS": "📌 Indica qué máquinas de CUERDAS están trabajando y qué referencias procesan:",
        "IMPRESION RTR": "📌 Indica qué máquinas de IMPRESIÓN RTR están operando:",
        "IMPRESION GRAFICA": "📌 Indica qué máquinas de IMPRESIÓN GRÁFICA están operando y la referencia:"
    },
    "INVENTARIO": {
        "FILETEADO": "📌 Indica número de rollos inventario: Gasa, Leno, Banda y Telas abiertas:"
    },
    "PARADAS": {
        "COSTURA": "📌 ¿Qué equipos de COSTURA están parados en este momento y cuál es la causa?",
        "CUERDAS": "📌 ¿Qué máquinas de CUERDAS están paradas y cuál es la causa?",
        "FILETEADO": "📌 ¿Qué máquinas de FILETEADO están paradas y cuál es la causa?",
        "IMPRESION RTR": "📌 ¿Qué máquinas de IMPRESIÓN RTR están paradas y cuál es la causa?",
        "IMPRESION GRAFICA": "📌 ¿Qué máquinas de IMPRESIÓN GRÁFICA están paradas y cuál es la causa?"
    },
    "RESUMEN_PARO": {
        "COSTURA": "📌 Haz un resumen de novedades de paro del día en COSTURA:"
    },
    "GENERAL_NOTES": {
        "COSTURA": "📌 ¿Hay alguna novedad importante para gerencia desde COSTURA?",
        "CUERDAS": "📌 ¿Hay alguna novedad importante para gerencia desde CUERDAS?",
        "FILETEADO": "📌 ¿Hay alguna novedad importante para gerencia desde FILETEADO?",
        "IMPRESION RTR": "📌 ¿Hay alguna novedad importante para gerencia desde IMPRESIÓN RTR?",
        "IMPRESION GRAFICA": "📌 ¿Hay alguna novedad importante para gerencia desde IMPRESIÓN GRÁFICA?"
    }
}

# ✅ Preguntas específicas para proceso SUPERVISIÓN
SUPERVISION_QUESTIONS = [
    "1. Novedades con programación (dificultades, temas por mejorar o reportar)",
    "2. Producto no conforme (materias primas o productos internos)",
    "3. Atención y novedades con mantenimiento",
    "4. Inventario de suministros y materias primas",
    "5. Estado del inventario de etiquetas sin leer en su ubicación",
    "6. Novedades en puntos de control y autorizaciones",
    "7. Retroalimentación al personal (desempeño, disciplina, reconocimientos)",
    "8. Verificación de registros de máquinas (control de proceso, calidad, listas de chequeo)",
    "9. Orden, aseo y cumplimiento de BPF",
    "10. Métodos de trabajo o documentos por actualizar"
]

def get_prompt(step, process):
    process = process.upper()
    if process == "SUPERVISION":
        return step  # Para este proceso, cada paso es la pregunta completa
    return PROMPT_MAP.get(step, {}).get(process, "⚠️ Pregunta no definida para este proceso.")

def get_flow(process):
    process = process.upper()
    if process == "SUPERVISION":
        return SUPERVISION_QUESTIONS
    return FLOW_MAP.get(process, [])
