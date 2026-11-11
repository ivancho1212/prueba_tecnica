"""Clasificación de datos viva (C1).

Carga `data_classification.yaml` y responde, para cualquier columna física o
campo de origen, qué nivel tiene y qué política aplica. Ofrece además las
verificaciones que el pipeline ejecuta al arrancar:

* `verificar_contra_db`: cada columna de core/quarantine/audit/analytics en
  information_schema está clasificada, y no hay clasificaciones huérfanas.
* `verificar_vistas_analista`: analytics.* solo expone Público/Interno.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

NIVELES = ("Público", "Interno", "Confidencial", "Restringido-PII")
ESQUEMAS_MODELO = ("core", "quarantine", "audit", "analytics")
RUTA_DEFAULT = Path(__file__).resolve().parents[2] / "data_classification.yaml"


class ErrorClasificacion(RuntimeError):
    """Una columna o campo no está clasificado, o viola su política."""


@dataclass(frozen=True)
class Politica:
    cifrado_en_reposo: bool
    en_logs: bool
    en_vista_analista: bool
    en_cuarentena_en_claro: bool


class Clasificacion:
    def __init__(self, doc: dict[str, Any]):
        self._doc = doc
        self.politicas: dict[str, Politica] = {
            n: Politica(**p) for n, p in doc["politicas"].items()
        }
        faltan = set(NIVELES) - set(self.politicas)
        if faltan:
            raise ErrorClasificacion(f"niveles sin política: {sorted(faltan)}")
        self.tablas: dict[str, dict[str, str]] = doc["tablas"]
        self.campos_origen: dict[str, dict[str, str]] = doc.get("campos_origen", {})
        for tabla, cols in list(self.tablas.items()) + list(self.campos_origen.items()):
            for col, nivel in cols.items():
                if nivel not in NIVELES:
                    raise ErrorClasificacion(f"{tabla}.{col}: nivel desconocido {nivel!r}")

    # ------------------------------------------------------------ carga ----
    @classmethod
    def cargar(cls, ruta: Path | None = None) -> "Clasificacion":
        with open(ruta or RUTA_DEFAULT, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    # ---------------------------------------------------------- consulta ----
    def nivel(self, tabla: str, columna: str) -> str:
        try:
            return self.tablas[tabla][columna]
        except KeyError:
            raise ErrorClasificacion(f"columna sin clasificar: {tabla}.{columna}") from None

    def nivel_origen(self, archivo: str, campo: str) -> str:
        try:
            return self.campos_origen[archivo][campo]
        except KeyError:
            raise ErrorClasificacion(f"campo de origen sin clasificar: {archivo}.{campo}") from None

    def politica(self, nivel: str) -> Politica:
        return self.politicas[nivel]

    def es_pii(self, tabla: str, columna: str) -> bool:
        return self.nivel(tabla, columna) == "Restringido-PII"

    def columnas(self, tabla: str, nivel: str | None = None) -> list[str]:
        cols = self.tablas.get(tabla, {})
        return [c for c, n in cols.items() if nivel is None or n == nivel]

    def campos_origen_por_politica(self, archivo: str, **flags: bool) -> list[str]:
        """Campos del archivo cuya política cumple todos los flags dados.

        Ej.: campos_origen_por_politica('clientes.csv', cifrado_en_reposo=True)
        """
        out = []
        for campo, nivel in self.campos_origen.get(archivo, {}).items():
            pol = self.politicas[nivel]
            if all(getattr(pol, k) == v for k, v in flags.items()):
                out.append(campo)
        return out

    def permitida_en_logs(self, tabla: str, columna: str) -> bool:
        return self.politicas[self.nivel(tabla, columna)].en_logs

    def campos_prohibidos_en_logs(self) -> set[str]:
        """Nombres de campo (origen y modelo) que jamás deben aparecer en un log."""
        out: set[str] = set()
        for cols in list(self.tablas.values()) + list(self.campos_origen.values()):
            for c, n in cols.items():
                if not self.politicas[n].en_logs:
                    out.add(c.split(".")[-1])
        return out

    # ---------------------------------------------------- verificaciones ----
    def verificar_vistas_analista(self) -> None:
        malas = [
            f"{t}.{c}={n}"
            for t, cols in self.tablas.items() if t.startswith("analytics.")
            for c, n in cols.items() if not self.politicas[n].en_vista_analista
        ]
        if malas:
            raise ErrorClasificacion(f"analytics expone niveles prohibidos: {malas}")

    def verificar_contra_db(self, conn) -> dict[str, int]:
        """Compara con information_schema. Lanza si hay columnas sin clasificar
        o clasificaciones de columnas que ya no existen. Devuelve conteos."""
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_schema || '.' || table_name, column_name
                  FROM information_schema.columns
                 WHERE table_schema = ANY(%s)
                 ORDER BY 1, ordinal_position
                """,
                (list(ESQUEMAS_MODELO),),
            )
            fisicas = {(t, c) for t, c in cur.fetchall()}
        declaradas = {(t, c) for t, cols in self.tablas.items() for c in cols}
        sin_clasificar = sorted(fisicas - declaradas)
        huerfanas = sorted(declaradas - fisicas)
        if sin_clasificar or huerfanas:
            raise ErrorClasificacion(
                f"sin clasificar en DB: {sin_clasificar}; clasificadas pero inexistentes: {huerfanas}"
            )
        return {"columnas": len(fisicas), "tablas": len({t for t, _ in fisicas})}
