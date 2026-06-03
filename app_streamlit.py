"""
=============================================================================
SAMPLING CALCULATOR — Streamlit Web Version
Calculadora de Muestreo — Huella de Carbono y Agua

Web port of the v7 desktop app. The calculation core, translations and PDF
export are identical to the desktop version — only the UI is rewritten
for Streamlit.

Run locally:  streamlit run app_streamlit.py
Deploy free: push to GitHub + share.streamlit.io
Requires:    streamlit, reportlab, pandas
=============================================================================
"""

import io
import math
import datetime
from dataclasses import dataclass
from typing import List

import streamlit as st
import pandas as pd

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, HRFlowable)
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    REPORTLAB = True
except ImportError:
    REPORTLAB = False


# ═══════════════════════════════════════════════════════════════════════
# 1. CALCULATION CORE  (identical to desktop v7)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Stratum:
    name: str
    color: str
    pct_farms: float
    pct_volume: float
    allocation_weight: float = 0.0
    N_h: int = 0
    n_h: int = 0


@dataclass
class SamplingResult:
    N: int
    vol: float
    Z: float
    E: float
    strata: List[Stratum]
    n_total: int
    pct_sample: float
    allocation_method: str = "50% farms + 50% volume"


def calc_n(N: int, Z: float, E: float) -> int:
    """Finite population sample size, conservative p=0.5."""
    if N < 1:
        return 1
    n_inf = (Z ** 2 * 0.25) / (E ** 2)
    n = n_inf / (1 + (n_inf - 1) / N)
    return min(int(math.ceil(n)), N)


def _allocate_total(total, shares, caps=None, min_each=0):
    """Allocate an integer total by largest remainders, respecting caps."""
    k = len(shares)
    if k == 0 or total <= 0:
        return [0] * k
    caps = caps or [total] * k
    caps = [max(0, int(c)) for c in caps]
    shares = [max(0.0, float(x)) for x in shares]
    if sum(shares) <= 0:
        shares = [1.0 if c > 0 else 0.0 for c in caps]

    out = [0] * k
    active = [i for i, c in enumerate(caps) if c > 0]

    if min_each > 0 and total >= len(active) * min_each:
        for i in active:
            out[i] = min(min_each, caps[i])
    elif min_each > 0:
        ranked = sorted(active, key=lambda i: shares[i], reverse=True)
        remaining_min = total
        while remaining_min > 0:
            progressed = False
            for i in ranked:
                if remaining_min <= 0:
                    break
                if out[i] < min_each and out[i] < caps[i]:
                    out[i] += 1
                    remaining_min -= 1
                    progressed = True
            if not progressed:
                break
        return out

    remaining = total - sum(out)
    while remaining > 0:
        avail = [i for i in active if out[i] < caps[i]]
        if not avail:
            break
        wsum = sum(shares[i] for i in avail)
        if wsum <= 0:
            shares = [1.0 if i in avail else shares[i] for i in range(k)]
            wsum = sum(shares[i] for i in avail)
        raw = {i: remaining * shares[i] / wsum for i in avail}
        floors = {i: min(int(math.floor(raw[i])), caps[i] - out[i]) for i in avail}
        added = sum(floors.values())
        if added:
            for i, a in floors.items():
                out[i] += a
            remaining -= added
        if remaining <= 0:
            break
        ranked = sorted(avail, key=lambda i: (raw[i] - math.floor(raw[i]), shares[i]), reverse=True)
        progressed = False
        for i in ranked:
            if remaining <= 0:
                break
            if out[i] < caps[i]:
                out[i] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    return out


def run_sampling(N, vol, strata_inputs, Z, E):
    farm_shares = [s["pct_farms"] for s in strata_inputs]
    volume_shares = [s["pct_volume"] for s in strata_inputs]
    N_counts = _allocate_total(int(N), farm_shares, min_each=0)
    n_total = calc_n(int(N), Z, E)
    weights = [(pf + pv) / 2 for pf, pv in zip(farm_shares, volume_shares)]
    n_counts = _allocate_total(n_total, weights, caps=N_counts, min_each=1)
    strata = []
    for s, N_h, n_h, w in zip(strata_inputs, N_counts, n_counts, weights):
        strata.append(Stratum(
            name=s["name"], color=s["color"],
            pct_farms=s["pct_farms"], pct_volume=s["pct_volume"],
            allocation_weight=w, N_h=N_h, n_h=n_h,
        ))
    n_total = sum(s.n_h for s in strata)
    pct_sample = (n_total / N * 100) if N > 0 else 0
    return SamplingResult(N=N, vol=vol, Z=Z, E=E,
                          strata=strata, n_total=n_total, pct_sample=pct_sample)


# ═══════════════════════════════════════════════════════════════════════
# 2. TRANSLATIONS  (identical to desktop v7)
# ═══════════════════════════════════════════════════════════════════════

_T = {
    "es": {
        "title":             "Calculadora de Muestreo — Huella de Carbono y Agua",
        "subtitle":          "· Sampling Calculator for Carbon & Water Footprint",
        "method_note":       "p=0.5 como supuesto inicial de variabilidad | IC {ci} | Error {error} | regla práctica 50% fincas + 50% volumen",
        "tab_results":       "Resultados",
        "tab_report":        "Reporte",
        "sec_info":          "TÉCNICO Y ORGANIZACIÓN",
        "sec_org":           "DATOS DE LA ORGANIZACIÓN",
        "sec_strata":        "ESTRATOS / CATEGORÍAS DE MUESTREO",
        "sec_params":        "PARÁMETROS ESTADÍSTICOS",
        "notes_label":       "NOTAS / OBSERVACIONES",
        "technician_label":  "Técnico responsable",
        "company_label":     "Organización / Empresa",
        "n_label":           "Número total de fincas (N)",
        "vol_label":         "Volumen exportado (ton/año) — opcional",
        "vol_help":          "Opcional. Si no se conoce, déjelo en 0; no afecta el cálculo de la muestra, solo aparece en el reporte.",
        "ci_label":          "Nivel de confianza",
        "error_label":       "Margen de error",
        "col_stratum":       "Estrato",
        "col_pct_farms":     "% Fincas",
        "col_pct_vol":       "% Volumen",
        "col_n_h":           "N fincas",
        "col_n_sample":      "Fincas a muestrear",
        "col_vol_pct":       "Vol %",
        "col_weight":        "Peso de asignación",
        "stratum_high":      "Alta productividad",
        "stratum_med":       "Media productividad",
        "stratum_low":       "Baja productividad",
        "btn_add_stratum":   "+ Agregar estrato",
        "btn_reset":         "Reiniciar",
        "btn_pdf":           "Exportar PDF",
        "btn_txt":           "Exportar .txt",
        "btn_copy":          "Copiar reporte",
        "btn_copied":        "✓ Copiado",
        "btn_reset_confirm": "¿Desea reiniciar? Se perderán todos los datos.",
        "btn_yes":           "Sí, reiniciar",
        "btn_no":            "Cancelar",
        "val_sum_farms":     "Suma % Fincas: {val}%",
        "val_sum_vol":       "Suma % Volumen: {val}%",
        "val_n_min":         "N debe ser ≥ 3",
        "val_vol_min":       "Volumen debe ser > 0",
        "val_sum_must_100":  "Debe ser exactamente 100%",
        "val_ok":            "✓ Listo",
        "res_title":         "PLAN DE MUESTREO",
        "res_n_total":       "Total fincas",
        "res_n_sample":      "A muestrear",
        "res_pct":           "% población",
        "res_error":         "Margen error",
        "strata_title":      "DISTRIBUCIÓN POR ESTRATO",
        "alloc_weight_badge":"Peso asign.",
        "strata_note":       "El usuario debe definir estratos/categorías que expliquen diferencias relevantes entre fincas: tipo de producción, productividad, orgánico vs. convencional, con o sin riego, zona agroecológica, tamaño de finca o sistema de manejo. Use las notas para justificar la elección.",
        "placeholder_title": "Sin resultados",
        "placeholder_body":  "Complete los datos para ver el plan de muestreo.",
        "scope_note":        "Alcance del método: el tamaño de muestra se calcula para estimar una PROPORCIÓN (p. ej., la proporción de fincas por encima de un umbral) con precisión aceptable usando p=0.5 como valor conservador. NO garantiza estimar una MEDIA (p. ej., la huella promedio por finca) con precisión aceptable. Para estimar medias de variables continuas se necesita el coeficiente de variación (CV) por estrato a partir de datos piloto o registros.",
        "report_title":      "REPORTE DE MUESTREO — HUELLA DE CARBONO Y AGUA",
        "report_norm":       "Marco normativo",
        "report_norm_val":   "Finite population sampling with stratified allocation",
        "report_tech":       "Técnico responsable",
        "report_company":    "Organización",
        "report_org_type":   "Tipo de organización",
        "report_org_val":    "Asignación de muestra por fincas y volumen",
        "report_params":     "PARÁMETROS ESTADÍSTICOS",
        "report_ci":         "Nivel de confianza",
        "report_error":      "Margen de error",
        "report_method":     "Método de cálculo",
        "report_method_val": "El tamaño total de muestra se calcula para una población finita usando p=0.5 como valor conservador que maximiza el tamaño de muestra. Luego, la muestra se distribuye entre estratos combinando 50% número de fincas y 50% volumen. Esta regla es práctica y debe revisarse si existen datos piloto.",
        "report_scope":      "Alcance",
        "report_scope_val":  "Dimensiona la muestra para estimar una proporción con precisión aceptable; no garantiza estimar una media de variable continua, lo que requeriría el CV por estrato.",
        "report_domains":    "CUANDO LOS ESTRATOS SON DOMINIOS",
        "report_domains_val":(
            "Si desea obtener estimaciones confiables para cada estrato (que se convierte, en este caso, "
            "en su dominio de estimación), puede aplicar la fórmula del Punto 1 a cada estrato, donde p es "
            "la proporción de variabilidad esperada en cada estrato (use 0.5 solo si no se dispone de otra "
            "información). Luego puede sumar los tamaños de muestra obtenidos en cada estrato para obtener el "
            "tamaño total de muestra, que suele ser mayor que el obtenido siguiendo el enfoque propuesto "
            "anteriormente. Esta solución es más precisa porque se alcanza precisión en cada estrato/dominio, "
            "pero suele ser más costosa."
        ),
        "report_formula":    "Fórmula",
        "report_formula_val":"n = [N × Z² × p × (1-p)] / [E² × (N-1) + Z² × p × (1-p)]\nW_h = 0.5 × F_h + 0.5 × V_h\nn_h = round(n × W_h)   (ΣW = 1, ya que F_h y V_h suman 1)",
        "report_pop":        "POBLACIÓN",
        "report_n":          "Total fincas (N)",
        "report_vol":        "Volumen total",
        "report_strata":     "DISTRIBUCIÓN POR ESTRATO",
        "report_result":     "RESULTADO TOTAL",
        "report_nfinal":     "n total a muestrear",
        "report_pct_pop":    "de la población",
        "report_notes":      "NOTAS / OBSERVACIONES",
        "report_ops":        "INSTRUCCIONES OPERATIVAS",
        "report_op1":        "1. Preparar la lista de fincas: crear una lista completa de todas las fincas incluidas en el estudio. Cada finca debe tener un código o ID único.",
        "report_op2":        "2. Clasificar las fincas por estrato: asignar cada finca a su estrato correspondiente, según el criterio definido por el usuario, como productividad, tipo de producción, riego, sistema de manejo u otra categoría relevante.",
        "report_op3":        "3. Seleccionar las fincas de forma aleatoria: dentro de cada estrato, seleccionar aleatoriamente el número de fincas indicado en la columna “Fincas a muestrear”.",
        "report_op4":        "4. Recopilar la información de campo: para cada finca seleccionada, recopilar los datos primarios necesarios para calcular la huella de carbono y/o agua.",
        "report_op5":        "5. Usar los resultados por estrato: calcular los resultados promedio de las fincas muestreadas dentro de cada estrato. Luego, usar el número total de fincas y el volumen de cada estrato para interpretar o expandir los resultados.",
        "report_op6":        "6. Revisar la precisión si hay datos piloto: si existen datos previos o datos piloto, revisar la variabilidad entre fincas. Para variables continuas, como emisiones o uso de agua, el tamaño de muestra puede ajustarse usando el coeficiente de variación (CV).",
        "report_op7":        "7. Documentar el procedimiento: guardar la lista de fincas, el criterio usado para definir los estratos, el método de selección aleatoria y, si se usa, la semilla de randomización. Esto permite repetir y auditar el proceso.",
        "method_explanation":(
            "El tamaño muestral total mínimo se calcula para toda la población de fincas usando "
            "corrección de población finita y p=0.5 como valor conservador que maximiza el tamaño de muestra cuando la "
            "variabilidad real aún no se conoce. Este tamaño dimensiona la estimación de una proporción; "
            "para variables continuas como huella de carbono o agua, la precisión de una media "
            "debe revisarse cuando existan datos piloto o un coeficiente de variación (CV) por estrato. "
            "La regla 50/50 fincas-volumen no es un óptimo de Neyman; es un compromiso transparente "
            "para planificación cuando no hay estimaciones de desviación estándar por estrato."
        ),
        "step1_title": "1) Tamaño total mínimo de muestra",
        "step2_title": "2) Peso de asignación de cada estrato",
        "step3_title": "3) Muestra asignada al estrato",
        "cv_note_title": "Cuando existan datos piloto",
        "cv_note_body": "Para indicadores de huella, el tamaño de muestra depende de la variabilidad entre fincas. Si existen datos piloto, use el coeficiente de variación (CV = desviación estándar ÷ promedio) para ajustar el tamaño de muestra. Mayor variabilidad requiere más muestras.",
        "generated":         "Generado",
        "ton_year":          "ton/año",
        "farms":             "fincas",
        "na":                "No especificado",
    },
    "en": {
        "title":             "Sampling Calculator — Carbon & Water Footprint",
        "subtitle":          "· Sampling Calculator for Carbon & Water Footprint",
        "method_note":       "p=0.5 as a conservative value | {ci} CI | Error {error} | practical 50% farms + 50% volume rule",
        "tab_results":       "Results",
        "tab_report":        "Report",
        "sec_info":          "TECHNICIAN & ORGANIZATION",
        "sec_org":           "ORGANIZATION DATA",
        "sec_strata":        "SAMPLING STRATA / CATEGORIES",
        "sec_params":        "STATISTICAL PARAMETERS",
        "notes_label":       "NOTES / OBSERVATIONS",
        "technician_label":  "Responsible technician",
        "company_label":     "Organization / Company",
        "n_label":           "Total number of farms (N)",
        "vol_label":         "Exported volume (ton/year) — optional",
        "vol_help":          "Optional. If unknown, leave it at 0; it does not affect the sample-size calculation, it only appears in the report.",
        "ci_label":          "Confidence level",
        "error_label":       "Margin of error",
        "col_stratum":       "Stratum",
        "col_pct_farms":     "% Farms",
        "col_pct_vol":       "% Volume",
        "col_n_h":           "N farms",
        "col_n_sample":      "Farms to sample",
        "col_vol_pct":       "Vol %",
        "col_weight":        "Allocation weight",
        "stratum_high":      "High productivity",
        "stratum_med":       "Medium productivity",
        "stratum_low":       "Low productivity",
        "btn_add_stratum":   "+ Add stratum",
        "btn_reset":         "Reset",
        "btn_pdf":           "Export PDF",
        "btn_txt":           "Export .txt",
        "btn_copy":          "Copy report",
        "btn_copied":        "✓ Copied",
        "btn_reset_confirm": "Reset all data? This cannot be undone.",
        "btn_yes":           "Yes, reset",
        "btn_no":            "Cancel",
        "val_sum_farms":     "Sum % Farms: {val}%",
        "val_sum_vol":       "Sum % Volume: {val}%",
        "val_n_min":         "N must be >= 3",
        "val_vol_min":       "Volume must be > 0",
        "val_sum_must_100":  "Must equal exactly 100%",
        "val_ok":            "✓ Ready",
        "res_title":         "SAMPLING PLAN",
        "res_n_total":       "Total farms",
        "res_n_sample":      "To sample",
        "res_pct":           "% population",
        "res_error":         "Error margin",
        "strata_title":      "DISTRIBUTION BY STRATUM",
        "alloc_weight_badge":"Alloc. weight",
        "strata_note":       "Define strata/categories that explain relevant differences among farms: production type, productivity, organic vs. conventional, irrigated vs. non-irrigated, agroecological zone, farm size, or management system. Use the notes to justify the choice.",
        "placeholder_title": "No results yet",
        "placeholder_body":  "Fill in the data to see the sampling plan.",
        "scope_note":        "Method scope: the sample size is computed to estimate a PROPORTION (e.g., the share of farms above a threshold) with acceptable accuracy using p=0.5 as a conservative value. It does NOT guarantee estimating a MEAN (e.g., the average per-farm footprint) with acceptable precision. Estimating means of continuous variables requires the coefficient of variation (CV) per stratum from pilot data or registers.",
        "report_title":      "SAMPLING REPORT — CARBON & WATER FOOTPRINT",
        "report_norm":       "Normative framework",
        "report_norm_val":   "Finite population sampling with stratified allocation",
        "report_tech":       "Responsible technician",
        "report_company":    "Organization",
        "report_org_type":   "Organization type",
        "report_org_val":    "Sample allocation by farms and volume",
        "report_params":     "STATISTICAL PARAMETERS",
        "report_ci":         "Confidence level",
        "report_error":      "Margin of error",
        "report_method":     "Calculation method",
        "report_method_val": "The total sample size is calculated for a finite population using p=0.5 as a conservative value that maximises the sample size. The sample is then distributed across strata by combining 50% number of farms and 50% volume. This is a practical rule and should be reviewed if pilot data are available.",
        "report_scope":      "Scope",
        "report_scope_val":  "Sizes the sample to estimate a proportion with acceptable accuracy; it does not guarantee estimating a mean of a continuous variable, which would require the CV per stratum.",
        "report_domains":    "WHEN STRATA ARE DOMAINS",
        "report_domains_val":(
            "If you would like to obtain reliable estimates for each stratum (which becomes, in this case, "
            "your estimation domain) you can apply formula in Point 1 for each stratum where p is the expected "
            "variability proportion in each stratum (set equal to 0.5 only if no other information on it is "
            "available). Then you can sum up the sample sizes obtained in each stratum to get the total sample "
            "size, which is usually higher than that obtained following the approach proposed above. This "
            "solution is more accurate because precision is reached in each stratum/domain but it is usually "
            "more expensive."
        ),
        "report_formula":    "Formula",
        "report_formula_val":"n = [N × Z² × p × (1-p)] / [E² × (N-1) + Z² × p × (1-p)]\nW_h = 0.5 × F_h + 0.5 × V_h\nn_h = round(n × W_h)   (ΣW = 1, since F_h and V_h each sum to 1)",
        "report_pop":        "POPULATION",
        "report_n":          "Total farms (N)",
        "report_vol":        "Total volume",
        "report_strata":     "DISTRIBUTION BY STRATUM",
        "report_result":     "TOTAL RESULT",
        "report_nfinal":     "Total n to sample",
        "report_pct_pop":    "of population",
        "report_notes":      "NOTES / OBSERVATIONS",
        "report_ops":        "OPERATIONAL INSTRUCTIONS",
        "report_op1":        "1. Prepare the farm list: create a complete list of all farms included in the study. Each farm should have a unique code or ID.",
        "report_op2":        "2. Classify farms by stratum: assign each farm to its corresponding stratum according to the criterion defined by the user, such as productivity, production type, irrigation, management system, or another relevant category.",
        "report_op3":        "3. Select farms randomly: within each stratum, randomly select the number of farms indicated in the “Farms to sample” column.",
        "report_op4":        "4. Collect field information: for each selected farm, collect the primary data needed to calculate the carbon and/or water footprint.",
        "report_op5":        "5. Use results by stratum: calculate the average results of the sampled farms within each stratum. Then use the total number of farms and the volume of each stratum to interpret or expand the results.",
        "report_op6":        "6. Review precision if pilot data are available: if previous data or pilot data exist, review variability across farms. For continuous variables, such as emissions or water use, the sample size can be adjusted using the coefficient of variation (CV).",
        "report_op7":        "7. Document the procedure: save the farm list, the criterion used to define the strata, the random selection method, and, if used, the randomization seed. This allows the process to be repeated and audited.",
        "method_explanation":(
            "The minimum total sample is calculated for the whole farm population using finite "
            "population correction and p=0.5 as a conservative value that maximises the sample size when real "
            "variability is still unknown. This size dimensions the estimation of a proportion; "
            "for continuous variables such as carbon or water footprint, the precision of a mean "
            "should be revisited when pilot data or a coefficient of variation (CV) by stratum "
            "become available. The 50/50 farms-volume rule is not Neyman-optimal; it is a "
            "transparent planning compromise when stratum standard deviations are unavailable."
        ),
        "step1_title": "1) Minimum total sample size",
        "step2_title": "2) Allocation weight per stratum",
        "step3_title": "3) Sample allocated to stratum",
        "cv_note_title": "When pilot data are available",
        "cv_note_body": "For footprint indicators, sample size depends on variability between farms. If pilot data are available, use the coefficient of variation (CV = standard deviation ÷ mean) to refine the sample size. Higher variability requires more samples.",
        "generated":         "Generated",
        "ton_year":          "ton/year",
        "farms":             "farms",
        "na":                "Not specified",
    }
}

_diff = set(_T["es"]) ^ set(_T["en"])
if _diff:
    raise RuntimeError(f"Translation key mismatch: {_diff}")


def tr(lang, key, **kw):
    text = _T.get(lang, _T["es"]).get(key, f"[{key}]")
    return text.format(**kw) if kw else text


# ═══════════════════════════════════════════════════════════════════════
# 3. PDF EXPORT  (identical to desktop v7; writes to file path OR BytesIO)
# ═══════════════════════════════════════════════════════════════════════

def _c(h):
    return colors.HexColor(h) if REPORTLAB else None


_CD  = "#0B4E96"; _CM  = "#1A6DB5"; _CBG = "#EBF3FB"
_CAM = "#E65100"; _CAB = "#FFF3E0"; _CLG = "#F2F5F9"
_CMG = "#D0DFF0"; _CDG = "#4A6378"; _CDK = "#1A2C3D"
_CWH = "#FFFFFF"; _CGR = "#2E7D32"; _CRD = "#C62828"


def _pdf_styles():
    base = getSampleStyleSheet()
    def s(n, **kw): return ParagraphStyle(n, parent=base["Normal"], **kw)
    return {
        "title":  s("t",  fontSize=17, textColor=_c(_CWH), fontName="Helvetica-Bold", leading=22),
        "sub":    s("su", fontSize=9,  textColor=_c("#B8D4EE"), fontName="Helvetica"),
        "sec":    s("se", fontSize=10, textColor=_c(_CWH), fontName="Helvetica-Bold"),
        "key":    s("k",  fontSize=9,  textColor=_c(_CDG), fontName="Helvetica-Bold"),
        "val":    s("v",  fontSize=10, textColor=_c(_CDK), fontName="Helvetica"),
        "num":    s("nu", fontSize=22, textColor=_c(_CD),  fontName="Helvetica-Bold", alignment=TA_CENTER),
        "numlbl": s("nl", fontSize=9,  textColor=_c(_CDG), fontName="Helvetica-Bold", alignment=TA_CENTER),
        "mono":   s("mo", fontSize=9,  textColor=_c(_CDK), fontName="Courier", backColor=_c(_CLG), leftIndent=8, borderPad=6),
        "op":     s("op", fontSize=9,  textColor=_c(_CDK), fontName="Helvetica", leftIndent=6),
        "weight": s("wt", fontSize=9,  textColor=_c(_CAM), fontName="Helvetica-Bold", alignment=TA_CENTER),
        "amber":  s("am", fontSize=9,  textColor=_c(_CAM), fontName="Helvetica-Bold"),
        "note":   s("no", fontSize=9,  textColor=_c(_CDK), fontName="Helvetica", leading=14),
        "footer": s("fo", fontSize=8,  textColor=_c(_CDG), fontName="Helvetica", alignment=TA_CENTER),
    }


def _sec_hdr(text, W, st):
    t = Table([[Paragraph(text, st["sec"])]], colWidths=[W])
    t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c(_CD)),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("LEFTPADDING",(0,0),(-1,-1),14)]))
    return t


def _kv(rows, W, st):
    d = [[Paragraph(k, st["key"]), Paragraph(v, st["val"])] for k, v in rows]
    t = Table(d, colWidths=[W*0.36, W*0.64])
    t.setStyle(TableStyle([("ROWBACKGROUNDS",(0,0),(-1,-1),[_c(_CLG),_c(_CWH)]),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ("GRID",(0,0),(-1,-1),0.5,_c(_CMG))]))
    return t


def export_pdf(path_or_buffer, lang, technician, company, notes, result):
    """Generate the PDF. path_or_buffer can be a filename or a BytesIO."""
    if not REPORTLAB:
        raise ImportError("reportlab not installed. Run: pip install reportlab")
    t = lambda k, **kw: tr(lang, k, **kw)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    z_label = {1.96:"95%", 1.645:"90%", 2.576:"99%"}.get(result.Z, "95%")
    e_label = f"+-{int(result.E*100)}%"
    vol_str = (f"{int(result.vol):,} {t('ton_year')}"
               if result.vol and result.vol > 0 else t("na"))
    doc = SimpleDocTemplate(path_or_buffer, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    W = A4[0] - 4*cm
    st = _pdf_styles()
    story = []

    hdr = Table([[Paragraph(t("report_title"), st["title"])],
                 [Paragraph(t("subtitle"), st["sub"])]], colWidths=[W])
    hdr.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c(_CD)),
        ("TOPPADDING",(0,0),(0,0),14),("BOTTOMPADDING",(0,1),(0,1),14),
        ("LEFTPADDING",(0,0),(-1,-1),18)]))
    story += [hdr, Spacer(1,10)]

    story += [_sec_hdr(t("sec_info"), W, st),
              _kv([(t("report_tech"),    technician or t("na")),
                   (t("report_company"), company or t("na")),
                   (t("generated"),      now),
                   (t("report_norm"),    t("report_norm_val")),
                   (t("report_org_type"),t("report_org_val"))], W, st),
              Spacer(1,8)]

    story += [_sec_hdr(t("report_params"), W, st),
              _kv([(t("report_ci"),    f"{z_label} (Z={result.Z})"),
                   (t("report_error"), e_label),
                   (t("report_method"),t("report_method_val")),
                   (t("report_scope"), t("report_scope_val"))], W, st),
              Spacer(1,8)]

    story += [_sec_hdr(t("report_pop"), W, st),
              _kv([(t("report_n"),   str(result.N)),
                   (t("report_vol"), vol_str)], W, st),
              Spacer(1,8)]

    box_w = W / 4
    vals = [str(result.N), str(result.n_total),
            f"{result.pct_sample:.1f}%", e_label]
    labels = [t("res_n_total"), t("res_n_sample"), t("res_pct"), t("res_error")]
    sum_d = [[Paragraph(v, st["num"])    for v in vals],
             [Paragraph(l, st["numlbl"]) for l in labels]]
    sum_t = Table(sum_d, colWidths=[box_w]*4, rowHeights=[44,26])
    sum_t.setStyle(TableStyle([("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,0),10),("BOTTOMPADDING",(0,0),(-1,0),2),
        ("TOPPADDING",(0,1),(-1,1),2),("BOTTOMPADDING",(0,1),(-1,1),10),
        ("LEFTPADDING",(0,0),(-1,-1),4),("RIGHTPADDING",(0,0),(-1,-1),4),
        ("BOX",(0,0),(-1,-1),0.5,_c(_CMG)),("LINEAFTER",(0,0),(2,-1),0.5,_c(_CMG)),
        ("BACKGROUND",(0,0),(2,-1),_c(_CBG)),("BACKGROUND",(3,0),(3,-1),_c(_CAB))]))
    story += [sum_t, Spacer(1,8)]

    dot_colors = [_CGR,_CAM,_CRD,"#7B1FA2","#00838F","#37474F"]
    hdr_row = [Paragraph(t(k), st["sec"]) for k in
               ["col_stratum","col_n_h","col_n_sample","col_vol_pct","col_weight"]]
    rows = [hdr_row]
    for i, s in enumerate(result.strata):
        dc = dot_colors[i % len(dot_colors)]
        rows.append([
            Paragraph(f'<font color="{dc}">&#9679;</font>  {s.name}', st["val"]),
            Paragraph(str(s.N_h), st["val"]),
            Paragraph(f"<b>{s.n_h}</b>", st["val"]),
            Paragraph(f"{s.pct_volume*100:.0f}%", st["val"]),
            Paragraph(f"{s.allocation_weight*100:.1f}%", st["weight"]),
        ])
    st_tbl = Table(rows, colWidths=[W*0.35,W*0.14,W*0.14,W*0.12,W*0.25])
    st_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),_c(_CM)),("TEXTCOLOR",(0,0),(-1,0),_c(_CWH)),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),("FONTSIZE",(0,0),(-1,0),10),
        ("ALIGN",(1,0),(-1,-1),"CENTER"),("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[_c(_CLG),_c(_CWH)]),
        ("BACKGROUND",(4,1),(4,-1),_c(_CAB)),("GRID",(0,0),(-1,-1),0.5,_c(_CMG))]))
    story += [_sec_hdr(t("strata_title"), W, st), st_tbl, Spacer(1,8)]

    story += [_sec_hdr(t("report_result"), W, st),
              _kv([(t("report_nfinal"),
                    f"{result.n_total} {t('farms')}  "
                    f"({result.pct_sample:.1f}% {t('report_pct_pop')})")], W, st),
              Spacer(1,8)]

    domains_tbl = Table([[Paragraph(t("report_domains_val"), st["note"])]], colWidths=[W])
    domains_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c(_CLG)),
        ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
        ("LEFTPADDING",(0,0),(-1,-1),12),("RIGHTPADDING",(0,0),(-1,-1),12),
        ("BOX",(0,0),(-1,-1),0.5,_c(_CMG))]))
    story += [_sec_hdr(t("report_domains"), W, st), domains_tbl, Spacer(1,8)]

    if notes.strip():
        notes_tbl = Table([[Paragraph(notes.replace("\n","<br/>"), st["note"])]], colWidths=[W])
        notes_tbl.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_c(_CLG)),
            ("TOPPADDING",(0,0),(-1,-1),10),("BOTTOMPADDING",(0,0),(-1,-1),10),
            ("LEFTPADDING",(0,0),(-1,-1),12),("BOX",(0,0),(-1,-1),0.5,_c(_CMG))]))
        story += [_sec_hdr(t("report_notes"), W, st), notes_tbl, Spacer(1,8)]

    ops_d = [[Paragraph(t(f"report_op{i}"), st["op"])] for i in range(1, 8)]
    ops_t = Table(ops_d, colWidths=[W])
    ops_t.setStyle(TableStyle([("ROWBACKGROUNDS",(0,0),(-1,-1),[_c(_CLG),_c(_CWH)]),
        ("TOPPADDING",(0,0),(-1,-1),7),("BOTTOMPADDING",(0,0),(-1,-1),7),
        ("LEFTPADDING",(0,0),(-1,-1),14),("GRID",(0,0),(-1,-1),0.3,_c(_CMG))]))
    story += [_sec_hdr(t("report_ops"), W, st), ops_t, Spacer(1,12)]

    story += [HRFlowable(width=W, color=_c(_CM), thickness=1), Spacer(1,4),
              Paragraph(f"Sampling Calculator for Carbon & Water Footprint  |  {now}",
                        st["footer"])]
    doc.build(story)


# ═══════════════════════════════════════════════════════════════════════
# 4. PLAIN-TEXT REPORT  (same content as desktop Report tab)
# ═══════════════════════════════════════════════════════════════════════

def build_report_text(lang, technician, company, notes, result):
    import textwrap
    def _wrap(s, width):
        return textwrap.wrap(s, width=width) or [""]
    t = lambda k, **kw: tr(lang, k, **kw)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    zs = {1.96:"95%", 1.645:"90%", 2.576:"99%"}.get(result.Z, "95%")
    el = f"+-{int(result.E*100)}%"
    tech = (technician or "").strip() or t("na")
    comp = (company or "").strip() or t("na")
    vol_str = (f"{int(result.vol):,} {t('ton_year')}"
               if result.vol and result.vol > 0 else t("na"))
    SEP = "=" * 65
    sep = "-" * 65
    out = []
    out += [SEP, "  " + t("report_title"), SEP]
    out += [f"{t('report_tech')}:      {tech}",
            f"{t('report_company')}:   {comp}",
            f"{t('generated')}:        {now}",
            f"{t('report_norm')}:      {t('report_norm_val')}",
            f"{t('report_org_type')}: {t('report_org_val')}", ""]
    out += [sep, "  " + t("report_params"), sep]
    out += [f"{t('report_ci')}:        {zs} (Z={result.Z})",
            f"{t('report_error')}:     {el}",
            f"{t('report_method')}:    {t('report_method_val')}",
            f"{t('report_scope')}:     {t('report_scope_val')}", ""]
    out += [sep, "  " + t("report_pop"), sep]
    out += [f"{t('report_n')}:         {result.N}",
            f"{t('report_vol')}:       {vol_str}", ""]
    out += [sep, "  " + t("report_strata"), sep]
    for s in result.strata:
        out.append(f"  {s.name:<28}  N_h={s.N_h:<5} n={s.n_h:<5} "
                   f"Vol={s.pct_volume*100:.0f}%  Weight={s.allocation_weight*100:.1f}%")
    out.append("")
    out += [sep, "  " + t("report_result"), sep]
    out.append(f"{t('report_nfinal')}:  {result.n_total} {t('farms')}  "
               f"({result.pct_sample:.1f}% {t('report_pct_pop')})")
    out.append("")
    out += [sep, "  " + t("report_domains"), sep]
    for line in _wrap(t("report_domains_val"), 63):
        out.append("  " + line)
    out.append("")
    if notes and notes.strip():
        out += [sep, "  " + t("report_notes"), sep]
        for line in notes.strip().split("\n"):
            out.append("  " + line)
        out.append("")
    out += [sep, "  " + t("report_ops"), sep]
    for i in range(1, 8):
        out.append("  " + t(f"report_op{i}"))
    out += ["", SEP]
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════════
# 5. STREAMLIT UI
# ═══════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Sampling Calculator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

STRATUM_COLORS = ["#2E7D32", "#E65100", "#C62828", "#7B1FA2",
                  "#00838F", "#37474F", "#558B2F", "#6D4C41"]


def _default_strata(lang):
    return [
        {"name": tr(lang, "stratum_high"), "color": "#2E7D32", "pct_farms": 25.0, "pct_volume": 55.0},
        {"name": tr(lang, "stratum_med"),  "color": "#E65100", "pct_farms": 30.0, "pct_volume": 30.0},
        {"name": tr(lang, "stratum_low"),  "color": "#C62828", "pct_farms": 45.0, "pct_volume": 15.0},
    ]


# --- Session state init ---
if "lang" not in st.session_state:
    st.session_state.lang = "es"
if "strata" not in st.session_state:
    st.session_state.strata = _default_strata("es")
if "confirm_reset" not in st.session_state:
    st.session_state.confirm_reset = False

lang = st.session_state.lang


def t(key, **kw):
    return tr(lang, key, **kw)


# --- Light theming ---
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    h1, h2, h3 { color: #0B4E96; }
    div[data-testid="stMetric"] {
        background-color: #EBF3FB;
        padding: 14px 18px;
        border-radius: 6px;
        border: 1px solid #B8D4EE;
    }
    div[data-testid="stMetric"]:last-child {
        background-color: #FFF3E0;
        border-color: #E65100;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 4px; }
    .stTabs [data-baseweb="tab"] {
        background-color: #1A6DB5;
        color: white;
        border-radius: 4px 4px 0 0;
        padding: 8px 18px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #0B4E96;
    }
</style>
""", unsafe_allow_html=True)


# --- Header ---
hcol1, hcol2 = st.columns([6, 1])
with hcol1:
    st.markdown(f"## 📊  · {t('title')}")
    st.caption(t("subtitle"))
with hcol2:
    new_lang_pick = st.radio(
        "lang", ["ES", "EN"],
        index=0 if lang == "es" else 1,
        horizontal=True,
        label_visibility="collapsed",
        key="lang_radio",
    )
    new_lang_code = "es" if new_lang_pick == "ES" else "en"
    if new_lang_code != lang:
        prev_defaults = (
            [tr("es", k) for k in ("stratum_high", "stratum_med", "stratum_low")] +
            [tr("en", k) for k in ("stratum_high", "stratum_med", "stratum_low")]
        )
        new_defaults = [tr(new_lang_code, k) for k in ("stratum_high", "stratum_med", "stratum_low")]
        for i, srow in enumerate(st.session_state.strata[:3]):
            if srow["name"] in prev_defaults:
                srow["name"] = new_defaults[i]
        st.session_state.lang = new_lang_code
        st.rerun()

st.divider()

# --- Two-column main layout ---
left_col, right_col = st.columns([2, 3], gap="large")


# =========================
# LEFT COLUMN — INPUTS
# =========================
with left_col:

    with st.container(border=True):
        st.markdown(f"**{t('sec_info')}**")
        c1, c2 = st.columns(2)
        technician = c1.text_input(t("technician_label"), key="technician")
        company    = c2.text_input(t("company_label"),    key="company")

    with st.container(border=True):
        st.markdown(f"**{t('sec_org')}**")
        c1, c2 = st.columns(2)
        N   = c1.number_input(t("n_label"),   min_value=1,   value=340,     step=1,    key="N_in")
        vol = c2.number_input(t("vol_label"), min_value=0.0, value=0.0,     step=100.0,
                              key="vol_in", help=t("vol_help"))

    with st.container(border=True):
        st.markdown(f"**{t('sec_strata')}**")
        st.caption(t("strata_note"))

        # Header row
        h = st.columns([0.4, 3, 1.2, 1.2, 0.6])
        h[0].markdown("&nbsp;", unsafe_allow_html=True)
        h[1].markdown(f"_{t('col_stratum')}_")
        h[2].markdown(f"_{t('col_pct_farms')}_")
        h[3].markdown(f"_{t('col_pct_vol')}_")
        h[4].markdown("&nbsp;", unsafe_allow_html=True)

        remove_idx = None
        for i, s in enumerate(st.session_state.strata):
            row = st.columns([0.4, 3, 1.2, 1.2, 0.6])
            row[0].markdown(
                f"<div style='color:{s['color']};font-size:26px;line-height:42px;text-align:center'>●</div>",
                unsafe_allow_html=True,
            )
            s["name"] = row[1].text_input(
                "n", value=s["name"], key=f"name_{i}", label_visibility="collapsed"
            )
            s["pct_farms"] = row[2].number_input(
                "pf", value=float(s["pct_farms"]),
                min_value=0.0, max_value=100.0, step=1.0,
                key=f"pf_{i}", label_visibility="collapsed",
            )
            s["pct_volume"] = row[3].number_input(
                "pv", value=float(s["pct_volume"]),
                min_value=0.0, max_value=100.0, step=1.0,
                key=f"pv_{i}", label_visibility="collapsed",
            )
            disabled = len(st.session_state.strata) <= 1
            if row[4].button("✕", key=f"rm_{i}", disabled=disabled, help="Remove stratum"):
                remove_idx = i

        if remove_idx is not None:
            st.session_state.strata.pop(remove_idx)
            st.rerun()

        # Sum indicators
        sf = sum(s["pct_farms"] for s in st.session_state.strata)
        sv = sum(s["pct_volume"] for s in st.session_state.strata)
        sf_ok = abs(sf - 100) < 0.5
        sv_ok = abs(sv - 100) < 0.5
        sf_color = "#2E7D32" if sf_ok else "#C62828"
        sv_color = "#2E7D32" if sv_ok else "#C62828"
        st.markdown(
            f"<div style='margin-top:6px;'>"
            f"<span style='color:{sf_color};font-weight:bold;font-size:14px'>"
            f"{t('val_sum_farms', val=f'{sf:.0f}')}</span>"
            f"&nbsp;&nbsp;&nbsp;"
            f"<span style='color:{sv_color};font-weight:bold;font-size:14px'>"
            f"{t('val_sum_vol', val=f'{sv:.0f}')}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        if st.button(t("btn_add_stratum")):
            idx = len(st.session_state.strata)
            st.session_state.strata.append({
                "name": f"Estrato {idx+1}" if lang == "es" else f"Stratum {idx+1}",
                "color": STRATUM_COLORS[idx % len(STRATUM_COLORS)],
                "pct_farms": 0.0,
                "pct_volume": 0.0,
            })
            st.rerun()

    with st.container(border=True):
        st.markdown(f"**{t('sec_params')}**")
        c1, c2 = st.columns(2)
        ci_choices  = ["95%  (Z=1.96)", "90%  (Z=1.645)", "99%  (Z=2.576)"]
        err_choices = ["±5%", "±10%", "±15%", "±20%"]
        ci_label  = c1.selectbox(t("ci_label"),    ci_choices,  index=0, key="ci_in")
        err_label = c2.selectbox(t("error_label"), err_choices, index=1, key="err_in")
        Z = {"95%  (Z=1.96)": 1.96, "90%  (Z=1.645)": 1.645, "99%  (Z=2.576)": 2.576}[ci_label]
        E = {"±5%": 0.05, "±10%": 0.10, "±15%": 0.15, "±20%": 0.20}[err_label]

    with st.container(border=True):
        st.markdown(f"**{t('notes_label')}**")
        notes = st.text_area(
            "notes", value="", height=110,
            label_visibility="collapsed", key="notes_in",
        )

    # Reset
    if st.button(t("btn_reset"), type="secondary"):
        st.session_state.confirm_reset = True
    if st.session_state.confirm_reset:
        st.warning(t("btn_reset_confirm"))
        cr1, cr2 = st.columns(2)
        if cr1.button(t("btn_yes"), type="primary", key="reset_yes"):
            keep_lang = st.session_state.lang
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.session_state.lang = keep_lang
            st.session_state.strata = _default_strata(keep_lang)
            st.session_state.confirm_reset = False
            st.rerun()
        if cr2.button(t("btn_no"), key="reset_no"):
            st.session_state.confirm_reset = False
            st.rerun()


# =========================
# RIGHT COLUMN — RESULTS + REPORT
# =========================
with right_col:
    errors = []
    if N < 3:
        errors.append(t("val_n_min"))
    # Exported volume is OPTIONAL — it does not enter the calculation, only the
    # report. No validation error when it is left at 0 / unavailable.
    sf_pct = sum(s["pct_farms"]  for s in st.session_state.strata)
    sv_pct = sum(s["pct_volume"] for s in st.session_state.strata)
    if abs(sf_pct - 100) > 0.5:
        errors.append(f"{t('val_sum_farms', val=f'{sf_pct:.0f}')} — {t('val_sum_must_100')}")
    if abs(sv_pct - 100) > 0.5:
        errors.append(f"{t('val_sum_vol', val=f'{sv_pct:.0f}')} — {t('val_sum_must_100')}")

    if errors:
        for e in errors:
            st.error("⚠ " + e)
        st.info(t("placeholder_body"))
    else:
        strata_input = [{
            "name": s["name"],
            "color": s["color"],
            "pct_farms":  s["pct_farms"]  / 100.0,
            "pct_volume": s["pct_volume"] / 100.0,
        } for s in st.session_state.strata]
        result = run_sampling(int(N), float(vol), strata_input, Z, E)

        zs = {1.96:"95%", 1.645:"90%", 2.576:"99%"}.get(Z, "95%")
        st.success(f"✓ {t('val_ok')}  ·  " + t("method_note", ci=zs, error=f"±{int(E*100)}%"))

        tab_results, tab_report = st.tabs([
            f"📊  {t('tab_results')}",
            f"📄  {t('tab_report')}",
        ])

        # ---------- Results tab ----------
        with tab_results:
            st.markdown(f"#### {t('res_title')}")
            m1, m2, m3, m4 = st.columns(4)
            m1.metric(t("res_n_total"),  result.N)
            m2.metric(t("res_n_sample"), result.n_total)
            m3.metric(t("res_pct"),      f"{result.pct_sample:.1f}%")
            m4.metric(t("res_error"),    f"±{int(result.E*100)}%")

            st.caption("ℹ " + t("scope_note"))

            st.markdown(f"#### {t('strata_title')}")
            df = pd.DataFrame([{
                t("col_stratum"):   s.name,
                t("col_n_h"):       s.N_h,
                t("col_n_sample"):  s.n_h,
                t("col_vol_pct"):   f"{s.pct_volume * 100:.0f}%",
                t("col_weight"):    f"{s.allocation_weight * 100:.1f}%",
            } for s in result.strata])
            st.dataframe(df, hide_index=True, use_container_width=True)

            with st.expander(f"📐  {t('report_formula')}", expanded=True):
                st.markdown(f"**{t('step1_title')}**")
                st.latex(r"n = \frac{N \cdot Z^{2} \cdot p \cdot (1-p)}{E^{2} \cdot (N-1) + Z^{2} \cdot p \cdot (1-p)}")
                st.markdown(f"**{t('step2_title')}**")
                st.latex(r"W_h = 0.5 \cdot F_h + 0.5 \cdot V_h")
                st.markdown(f"**{t('step3_title')}**")
                st.latex(r"n_h = \mathrm{round}\!\left(n \cdot W_h\right)")
                st.caption(
                    "ΣW = 1, ya que las proporciones de fincas (Fₕ) y de volumen (Vₕ) "
                    "suman 1 cada una; por eso no es necesario normalizar."
                    if lang == "es" else
                    "ΣW = 1, since the farm shares (Fₕ) and volume shares (Vₕ) each sum "
                    "to 1, so no normalization is needed."
                )

                st.markdown("**Leyenda de variables**" if lang == "es" else "**Variable legend**")
                legend_rows = [
                    ("n", "Tamaño total mínimo de muestra" if lang == "es" else "Minimum total sample size"),
                    ("N", "Número total de fincas en la población" if lang == "es" else "Total number of farms in the population"),
                    ("Z", "Valor estadístico asociado al nivel de confianza" if lang == "es" else "Statistical value associated with the confidence level"),
                    ("E", "Margen de error absoluto aceptado" if lang == "es" else "Accepted absolute margin of error"),
                    ("p", "Proporción de variabilidad esperada; se usa 0.5 como valor conservador para varianza máxima" if lang == "es" else "Expected variability proportion; 0.5 is used as a conservative value for maximum variance"),
                    ("p(1-p)", "Varianza poblacional de una proporción" if lang == "es" else "Population variance for a proportion"),
                    ("h", "Estrato o categoría de muestreo" if lang == "es" else "Sampling stratum or category"),
                    ("Fₕ", "Proporción de fincas en el estrato h (número de fincas en el estrato dividido entre el número total de fincas)" if lang == "es" else "Share of farms in stratum h (number of farms in the stratum divided by total number of farms)"),
                    ("Vₕ", "Proporción del volumen total en el estrato h (suma de volúmenes en el estrato dividida entre la suma de volúmenes de la población de fincas)" if lang == "es" else "Share of total volume in stratum h (sum of volumes in stratum divided by sum of volumes in farms population)"),
                    ("Wₕ", "Peso práctico del estrato h para distribuir la muestra" if lang == "es" else "Practical weight of stratum h for allocating the sample"),
                    ("nₕ", "Número de fincas a muestrear en el estrato h" if lang == "es" else "Number of farms to sample in stratum h"),
                    ("CV", "Coeficiente de variación para variables continuas: σ / μ" if lang == "es" else "Coefficient of variation for continuous variables: σ / μ"),
                ]
                legend_df = pd.DataFrame(
                    legend_rows,
                    columns=["Variable", "Significado" if lang == "es" else "Meaning"]
                )
                st.dataframe(legend_df, hide_index=True, use_container_width=True)

                st.markdown(f"**{t('cv_note_title')}**")
                st.info(t("cv_note_body"))

                st.markdown(t("method_explanation"))

                st.markdown(f"**{t('report_domains')}**")
                st.markdown(t("report_domains_val"))

        # ---------- Report tab ----------
        with tab_report:
            report_text = build_report_text(lang, technician, company, notes, result)

            cd1, cd2, _ = st.columns([1, 1, 3])

            if REPORTLAB:
                try:
                    pdf_buf = io.BytesIO()
                    export_pdf(pdf_buf, lang, technician, company, notes, result)
                    pdf_buf.seek(0)
                    cd1.download_button(
                        f"📄  {t('btn_pdf')}",
                        data=pdf_buf,
                        file_name="sampling_report.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )
                except Exception as ex:
                    cd1.error(f"PDF: {ex}")
            else:
                cd1.warning("⚠ reportlab not installed")

            cd2.download_button(
                f"📝  {t('btn_txt')}",
                data=report_text,
                file_name="sampling_report.txt",
                mime="text/plain",
                use_container_width=True,
            )

            st.code(report_text, language=None)


# --- Footer ---
st.divider()
st.caption(
    f" · Sampling Calculator for Carbon & Water Footprint  ·  v8 (web)  ·  "
    f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
)
