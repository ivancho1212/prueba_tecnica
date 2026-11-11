"""Logs estructurados JSON, una línea por evento, sin PII (Parte E).

Dos barreras:
1. Campos prohibidos: cualquier clave cuyo nombre esté clasificado con
   `en_logs: false` en data_classification.yaml se sustituye por "[REDACTADO]".
2. Patrones: correos, celulares y cédulas en cualquier valor de texto se
   enmascaran con `bia.pii.enmascarar`, aunque el campo no esté clasificado.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

from bia import pii

_REDACTADO = "[REDACTADO]"


class FiltroPII(logging.Filter):
    def __init__(self, campos_prohibidos: set[str]):
        super().__init__()
        self.prohibidos = {c.lower() for c in campos_prohibidos}

    def _limpiar(self, obj: Any, clave: str | None = None) -> Any:
        if clave and clave.lower() in self.prohibidos:
            return _REDACTADO
        if isinstance(obj, dict):
            return {k: self._limpiar(v, k) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._limpiar(v) for v in obj]
        if isinstance(obj, str):
            return pii.enmascarar(obj, numeros_sueltos_como_cedula=False).texto
        return obj

    def filter(self, record: logging.LogRecord) -> bool:
        datos = getattr(record, "datos", None)
        record.datos = self._limpiar(datos) if isinstance(datos, dict) else {}
        record.msg = self._limpiar(str(record.msg))
        return True


class FormatoJSON(logging.Formatter):
    def __init__(self, corrida_id: str, componente: str):
        super().__init__()
        self.corrida_id, self.componente = corrida_id, componente

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "nivel": record.levelname,
            "componente": self.componente,
            "corrida_id": self.corrida_id,
            "evento": record.getMessage(),
        }
        doc.update(getattr(record, "datos", {}) or {})
        if record.exc_info:
            doc["error"] = self.formatException(record.exc_info).splitlines()[-1]
        return json.dumps(doc, ensure_ascii=False, default=str)


def configurar(ruta: Path, corrida_id: str, componente: str, campos_prohibidos: set[str],
               nivel: str = "INFO") -> logging.Logger:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bia")
    logger.handlers.clear()
    logger.setLevel(nivel.upper())
    logger.propagate = False
    filtro = FiltroPII(campos_prohibidos)
    fmt = FormatoJSON(corrida_id, componente)
    fh = logging.FileHandler(ruta, encoding="utf-8")
    fh.setFormatter(fmt); fh.addFilter(filtro)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt); sh.addFilter(filtro); sh.setLevel(logging.INFO)
    logger.addHandler(fh); logger.addHandler(sh)
    return logger


def log(logger: logging.Logger, evento: str, nivel: int = logging.INFO, **datos: Any) -> None:
    logger.log(nivel, evento, extra={"datos": datos})
