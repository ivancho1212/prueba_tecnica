#!/usr/bin/env python3
"""Crea .env a partir de .env.example y genera las llaves vacías.

Idempotente: si .env ya existe, solo completa las variables ausentes o vacías
cuyo nombre termina en _KEY. Nunca sobreescribe un valor existente, para que
una base ya cifrada siga siendo legible por el pipeline.
Solo stdlib: corre antes de instalar dependencias.
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
EJEMPLO = RAIZ / ".env.example"
DESTINO = RAIZ / ".env"


def _parse(lineas: list[str]) -> dict[str, str]:
    kv: dict[str, str] = {}
    for ln in lineas:
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        kv[k.strip()] = v.strip()
    return kv


def main() -> int:
    if not EJEMPLO.exists():
        print(f"No existe {EJEMPLO}", file=sys.stderr)
        return 1
    ejemplo = EJEMPLO.read_text(encoding="utf-8").splitlines()
    if DESTINO.exists():
        lineas = DESTINO.read_text(encoding="utf-8").splitlines()
        creado = False
    else:
        lineas = list(ejemplo)
        creado = True

    actuales = _parse(lineas)
    faltantes = [k for k in _parse(ejemplo) if k not in actuales]
    for k in faltantes:  # variables nuevas en la plantilla
        lineas.append(f"{k}={_parse(ejemplo)[k]}")

    generadas: list[str] = []
    salida: list[str] = []
    for ln in lineas:
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            if k.strip().endswith("_KEY") and not v.strip():
                ln = f"{k.strip()}={secrets.token_urlsafe(32)}"
                generadas.append(k.strip())
        salida.append(ln)

    DESTINO.write_text("\n".join(salida) + "\n", encoding="utf-8")
    try:
        DESTINO.chmod(0o600)
    except OSError:
        pass
    accion = "creado" if creado else "actualizado"
    print(f".env {accion}; llaves generadas: {', '.join(generadas) or 'ninguna'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
