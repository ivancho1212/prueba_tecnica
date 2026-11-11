"""Cifrado en reposo a nivel de aplicación (C3).

* AES-256-GCM con nonce aleatorio por valor. Formato almacenado (bytea):
  ``b'\\x01' || nonce(12) || ciphertext||tag``. El byte de versión permite rotar
  algoritmo/llave sin migración big-bang.
* HMAC-SHA256 del valor *normalizado* para igualdad y joins sin exponer el dato.
* Ambas llaves se derivan con HKDF de los secretos de .env (BIA_ENC_KEY,
  BIA_HMAC_KEY), que nunca llegan a Postgres: quien lee la tabla cruda, incluso
  como superusuario, ve bytes.
"""
from __future__ import annotations

import hmac
import hashlib
import os
import re

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

VERSION = b"\x01"
_NONCE = 12


def _derivar(secreto: str, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(secreto.encode("utf-8"))


class Cifrador:
    def __init__(self, enc_key: str, hmac_key: str):
        if len(enc_key) < 32 or len(hmac_key) < 32:
            raise ValueError("BIA_ENC_KEY y BIA_HMAC_KEY deben tener al menos 32 caracteres")
        self._aes = AESGCM(_derivar(enc_key, b"bia-enc-v1"))
        self._hmac_key = _derivar(hmac_key, b"bia-hmac-v1")

    # ------------------------------------------------------------ cifrado --
    def cifrar(self, texto: str | None) -> bytes | None:
        if texto is None:
            return None
        nonce = os.urandom(_NONCE)
        return VERSION + nonce + self._aes.encrypt(nonce, texto.encode("utf-8"), VERSION)

    def descifrar(self, blob: bytes | memoryview | None) -> str | None:
        if blob is None:
            return None
        b = bytes(blob)
        if b[:1] != VERSION:
            raise ValueError("versión de cifrado desconocida")
        nonce, ct = b[1:1 + _NONCE], b[1 + _NONCE:]
        return self._aes.decrypt(nonce, ct, VERSION).decode("utf-8")

    # --------------------------------------------------------------- hmac --
    def hmac(self, valor_normalizado: str | None) -> bytes | None:
        if valor_normalizado is None or valor_normalizado == "":
            return None
        return hmac.new(self._hmac_key, valor_normalizado.encode("utf-8"), hashlib.sha256).digest()


# ------------------------------------------------- normalización para HMAC --
def normalizar_nit(nit: str | None) -> str | None:
    """'NIT 900.123.456-7' → '9001234567' (solo dígitos, incluye DV si viene)."""
    if not nit:
        return None
    d = re.sub(r"\D", "", nit)
    return d or None


def normalizar_email(email: str | None) -> str | None:
    if not email:
        return None
    e = email.strip().lower()
    return e or None


def normalizar_telefono(tel: str | None) -> str | None:
    if not tel:
        return None
    d = re.sub(r"\D", "", tel)
    return d[-10:] if len(d) >= 10 else (d or None)


def hash_fila(*valores: object) -> str:
    """Huella estable de una fila de origen (para upserts idempotentes)."""
    h = hashlib.sha256()
    for v in valores:
        h.update(("" if v is None else str(v)).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()
