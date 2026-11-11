"""Salidas de `make run`: out/consumo_mensual.csv, out/dq_report.json, out/metrics.json."""
from __future__ import annotations

import csv
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

LLAVES_DQ = [
    "filas_leidas", "duplicados_exactos", "duplicados_conflictivos", "registros_negativos",
    "registros_nulos", "registros_outlier", "registros_mwh_convertidos", "correcciones_tardias_aplicadas",
    "horas_sin_lectura_valida_total", "horas_sin_lectura_valida_por_frontera",
    "fronteras_huerfanas_facturacion", "clientes_con_pii_en_texto_libre",
]


def escribir_consumo_mensual(ruta: Path, filas) -> int:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    q = Decimal("0.001")
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["frontera_id", "mes", "consumo_kwh", "horas_con_dato"])
        n = 0
        for frontera, mes, total, horas in filas:
            w.writerow([frontera, mes, f"{Decimal(total).quantize(q, ROUND_HALF_UP):.3f}", int(horas)])
            n += 1
    return n


def escribir_dq_report(ruta: Path, dq: dict) -> None:
    faltan = [k for k in LLAVES_DQ if k not in dq]
    if faltan:
        raise ValueError(f"dq_report sin llaves obligatorias: {faltan}")
    ordenado = {k: dq[k] for k in LLAVES_DQ}
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(ordenado, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def escribir_metrics(ruta: Path, metrics: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
