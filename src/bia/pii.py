"""Detección y enmascaramiento de PII en texto libre (C2) y en logs (E).

Patrones calibrados con lo que aparece en `observaciones` de clientes.csv:
correos, celulares colombianos (10 dígitos que empiezan por 3, con o sin +57 y
separadores) y cédulas ("CC 12345678", "C.C. 1.234.567", "cédula 80123456").
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RE_CELULAR = re.compile(r"(?<!\d)(?:\+?57[\s\-.]?)?3\d{2}[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)")
RE_CEDULA = re.compile(
    r"(?i)\b(?:c\.?\s?c\.?|c[eé]dula(?:\s+de\s+ciudadan[ií]a)?|nuip|t\.?\s?i\.?)\s*(?:n[o°º.]?\s*)?[:#]?\s*"
    r"(\d{1,3}(?:[.,]\d{3}){1,3}|\d{6,10})"
)
# Números largos sueltos que no son celular (p. ej. cédula sin prefijo).
RE_NUMERO_LARGO = re.compile(r"(?<![\d.,])(?!3\d{9}(?!\d))\d{7,10}(?![\d.,])")

MASCARAS = {"email": "[EMAIL]", "celular": "[CELULAR]", "cedula": "[CEDULA]"}


@dataclass
class ResultadoPII:
    texto: str
    tipos: list[str] = field(default_factory=list)

    @property
    def tiene_pii(self) -> bool:
        return bool(self.tipos)


def enmascarar(texto: str | None, numeros_sueltos_como_cedula: bool = True) -> ResultadoPII:
    """Devuelve el texto con la PII sustituida por tokens y los tipos hallados."""
    if not texto:
        return ResultadoPII(texto or "")
    tipos: list[str] = []
    t = texto
    if RE_EMAIL.search(t):
        tipos.append("email"); t = RE_EMAIL.sub(MASCARAS["email"], t)
    if RE_CELULAR.search(t):
        tipos.append("celular"); t = RE_CELULAR.sub(MASCARAS["celular"], t)
    if RE_CEDULA.search(t):
        tipos.append("cedula")
        t = RE_CEDULA.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + MASCARAS["cedula"], t)
    if numeros_sueltos_como_cedula and RE_NUMERO_LARGO.search(t):
        if "cedula" not in tipos:
            tipos.append("cedula")
        t = RE_NUMERO_LARGO.sub(MASCARAS["cedula"], t)
    return ResultadoPII(t, tipos)


def contiene_pii(texto: str) -> bool:
    return bool(RE_EMAIL.search(texto) or RE_CELULAR.search(texto) or RE_CEDULA.search(texto)
                or RE_NUMERO_LARGO.search(texto))
