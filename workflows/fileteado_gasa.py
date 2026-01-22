# workflows/fileteado_gasa.py
import os
import re
from datetime import date, datetime

import numpy as np
import pandas as pd
from fpdf import FPDF

from services.session_memory import CONFIG_DIR

REPORTS_DIR = os.path.join(CONFIG_DIR, "fileteado_reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

COLS = {
    "articulo": ["Numero_Articulo", "numero_articulo", "Articulo", "articulo", "Numero de articulo"],
    "maquina": ["Maquina", "Máquina", "maquina", "máquina", "Equipo", "equipo"],
    "cantidad": ["Cantidad_Completada", "cantidad_completada", "Cantidad", "cantidad"],
    "tc": ["Tiempo_Corrida", "tiempo_corrida", "tpo_corrida", "tpo_cda"],
    "tp": ["Tiempo_Perdido", "tiempo_perdido", "tmp_perd", "tiempo_paro"],
    "cs": ["Corrida_Standar", "corrida_standar", "CORRSTAND", "corrida_estandar"],
    "operario": ["Apellidos_Nombres", "apellidos_nombres", "Operario", "operario", "Nombre_Operario"],
}


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or df.empty:
        return None
    for c in candidates:
        if c in df.columns:
            return c
    norm_map = {_normalize(c): c for c in df.columns}
    for c in candidates:
        key = _normalize(c)
        if key in norm_map:
            return norm_map[key]
    return None


def _starts_with_ci(series: pd.Series, prefix: str) -> pd.Series:
    return series.astype(str).str.upper().str.startswith(prefix.upper())


class ReporteGasa(FPDF):
    def __init__(self, start: date, end: date, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._start_date = start
        self._end_date = end

    def header(self):
        self.set_font("Arial", "B", 16)
        self.cell(0, 10, "Analisis proceso Gasa", 0, 1, "C")
        self.set_font("Arial", "", 10)
        rango_txt = (
            f"Rango del informe {self._start_date.strftime('%d/%m/%Y')} "
            f"a {self._end_date.strftime('%d/%m/%Y')}"
        )
        self.cell(0, 6, rango_txt, 0, 1, "C")
        fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        self.cell(0, 8, f"Fecha y hora de generacion: {fecha_hora}", 0, 1, "R")
        self.ln(3)

    def tabla_maquinas(self, df: pd.DataFrame) -> None:
        self.set_font("Arial", "B", 12)
        self.set_fill_color(230, 230, 230)
        self.cell(0, 10, " 1. Resumen de Produccion por Maquina", 0, 1, "L", True)
        self.ln(2)

        self.set_font("Arial", "B", 8)
        self.set_fill_color(200, 220, 255)
        cols = ["maquina", "produccion", "t_corrida", "T.perd", "cor.estandar", "%productividad", "%eficiencia"]
        widths = [25, 25, 25, 20, 25, 30, 30]

        for i, col in enumerate(cols):
            self.cell(widths[i], 8, col, 1, 0, "C", True)
        self.ln()

        self.set_font("Arial", "", 8)
        for _, row in df.iterrows():
            self.cell(widths[0], 7, str(row["Maquina"]), 1)
            self.cell(widths[1], 7, f"{row['produccion']:,.0f}", 1, 0, "R")
            self.cell(widths[2], 7, f"{row['t_corrida']:.2f}", 1, 0, "R")
            self.cell(widths[3], 7, f"{row['T.perd']:.2f}", 1, 0, "R")
            self.cell(widths[4], 7, f"{row['cor.estandar']:.2f}", 1, 0, "R")
            self.cell(widths[5], 7, f"{row['%productividad']:.2f}%", 1, 0, "R")

            if row["%eficiencia"] >= 80:
                self.set_font("Arial", "B", 8)
            self.cell(widths[6], 7, f"{row['%eficiencia']:.2f}%", 1, 0, "R")
            self.set_font("Arial", "", 8)
            self.ln()
        self.ln(8)

    def tabla_operarios(self, df: pd.DataFrame) -> None:
        self.set_font("Arial", "B", 12)
        self.set_fill_color(235, 241, 222)
        self.cell(0, 10, " 2. Seguimiento Eficiencia operarios", 0, 1, "L", True)
        self.ln(2)

        self.set_font("Arial", "B", 8)
        self.set_fill_color(210, 230, 200)
        cols = ["Nombre", "T. corrida", "T. perdido", "Cor. est.", "% Efic Mes"]
        widths = [75, 25, 25, 25, 30]

        for i, col in enumerate(cols):
            self.cell(widths[i], 8, col, 1, 0, "C", True)
        self.ln()

        self.set_font("Arial", "", 7)
        for _, row in df.iterrows():
            if row["%eficiencia_mes"] >= 80:
                self.set_fill_color(198, 239, 206)
            elif row["%eficiencia_mes"] >= 75:
                self.set_fill_color(255, 235, 156)
            else:
                self.set_fill_color(255, 255, 255)

            self.cell(widths[0], 6, str(row["Nombre"])[:55], 1, 0, "L", True)
            self.cell(widths[1], 6, f"{row['Tiempo_corrida']:.2f}", 1, 0, "R", True)
            self.cell(widths[2], 6, f"{row['T.perdido']:.2f}", 1, 0, "R", True)
            self.cell(widths[3], 6, f"{row['Cor.estandar']:.2f}", 1, 0, "R", True)
            self.cell(widths[4], 6, f"{row['%eficiencia_mes']:.2f}%", 1, 0, "R", True)
            self.ln()


def _filter_gasa_records(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    c_art = _find_col(df, COLS["articulo"])
    c_maq = _find_col(df, COLS["maquina"])
    if not (c_art and c_maq):
        return pd.DataFrame()
    mask = _starts_with_ci(df[c_art], "CAG") & _starts_with_ci(df[c_maq], "FILET")
    return df.loc[mask].copy()


def _prepare_maquina_table(df_prod: pd.DataFrame) -> pd.DataFrame:
    if df_prod is None or df_prod.empty:
        return pd.DataFrame(columns=[
            "Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar", "%productividad", "%eficiencia"
        ])
    c_maq = _find_col(df_prod, COLS["maquina"])
    c_qty = _find_col(df_prod, COLS["cantidad"])
    c_tc = _find_col(df_prod, COLS["tc"])
    c_tp = _find_col(df_prod, COLS["tp"])
    c_cs = _find_col(df_prod, COLS["cs"])
    if not (c_maq and c_qty and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=[
            "Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar", "%productividad", "%eficiencia"
        ])

    df_maq = df_prod.groupby(c_maq).agg({
        c_qty: "sum",
        c_tc: "sum",
        c_tp: "sum",
        c_cs: "sum",
    }).reset_index()
    df_maq.columns = ["Maquina", "produccion", "t_corrida", "T.perd", "cor.estandar"]

    for col in ["produccion", "t_corrida", "T.perd", "cor.estandar"]:
        df_maq[col] = pd.to_numeric(df_maq[col], errors="coerce").fillna(0.0)

    denom_prod = df_maq["t_corrida"] + df_maq["T.perd"]
    df_maq["%productividad"] = np.where(denom_prod > 0, (df_maq["cor.estandar"] / denom_prod) * 100, 0.0)
    df_maq["%eficiencia"] = np.where(df_maq["t_corrida"] > 0, (df_maq["cor.estandar"] / df_maq["t_corrida"]) * 100, 0.0)
    df_maq.replace([np.inf, -np.inf], 0, inplace=True)
    return df_maq


def _prepare_operario_table(df_prod: pd.DataFrame) -> pd.DataFrame:
    if df_prod is None or df_prod.empty:
        return pd.DataFrame(columns=[
            "Nombre", "Tiempo_corrida", "T.perdido", "Cor.estandar", "%eficiencia_mes"
        ])
    c_op = _find_col(df_prod, COLS["operario"])
    c_tc = _find_col(df_prod, COLS["tc"])
    c_tp = _find_col(df_prod, COLS["tp"])
    c_cs = _find_col(df_prod, COLS["cs"])
    if not (c_op and c_tc and c_tp and c_cs):
        return pd.DataFrame(columns=[
            "Nombre", "Tiempo_corrida", "T.perdido", "Cor.estandar", "%eficiencia_mes"
        ])

    df_ope = df_prod.groupby(c_op).agg({
        c_tc: "sum",
        c_tp: "sum",
        c_cs: "sum",
    }).reset_index()
    df_ope.columns = ["Nombre", "Tiempo_corrida", "T.perdido", "Cor.estandar"]
    for col in ["Tiempo_corrida", "T.perdido", "Cor.estandar"]:
        df_ope[col] = pd.to_numeric(df_ope[col], errors="coerce").fillna(0.0)
    df_ope["%eficiencia_mes"] = np.where(
        df_ope["Tiempo_corrida"] > 0,
        (df_ope["Cor.estandar"] / df_ope["Tiempo_corrida"]) * 100,
        0.0,
    )
    df_ope.replace([np.inf, -np.inf], 0, inplace=True)
    df_ope = df_ope.sort_values(by="%eficiencia_mes", ascending=False)
    return df_ope


def build_pdf_gasa(df_range_filtered: pd.DataFrame, start: date, end: date) -> tuple[str, list[str]]:
    df_filt = _filter_gasa_records(df_range_filtered)
    df_maq = _prepare_maquina_table(df_filt)
    df_ope = _prepare_operario_table(df_filt)

    pdf = ReporteGasa(start, end, format="Letter")
    pdf.add_page()
    if df_maq.empty:
        pdf.set_font("Arial", "B", 11)
        pdf.cell(0, 8, "Sin datos para Gasa en el rango seleccionado.", 0, 1, "L")
    else:
        pdf.tabla_maquinas(df_maq)
        if not df_ope.empty:
            pdf.tabla_operarios(df_ope)

    fname = f"Analisis_Proceso_Gasa_{start.isoformat()}_{end.isoformat()}.pdf"
    out_path = os.path.join(REPORTS_DIR, fname)
    pdf.output(out_path)
    return out_path, []
