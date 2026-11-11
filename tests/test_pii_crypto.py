from __future__ import annotations

import json
import logging
from pathlib import Path

from bia import pii
from bia.crypto import Cifrador, normalizar_email, normalizar_nit, normalizar_telefono
from bia.logging_json import configurar, log

K1, K2 = "a" * 43, "b" * 43


def test_enmascara_email_celular_cedula():
    r = pii.enmascarar("Contacto alterno: maria.p@empresa.com.co tel 3104567890")
    assert r.tipos == ["email", "celular"] and r.texto == "Contacto alterno: [EMAIL] tel [CELULAR]"
    r = pii.enmascarar("Autorizo debito. CC 80123456 del rep legal.")
    assert "cedula" in r.tipos and "80123456" not in r.texto and "CC [CEDULA]" in r.texto
    r = pii.enmascarar("Cel. de la planta: +57 300-123-4567")
    assert r.tipos == ["celular"] and "300" not in r.texto
    assert not pii.enmascarar("Cliente al día. Factura 2026-01 pagada.").tiene_pii
    assert not pii.enmascarar("").tiene_pii


def test_cifrado_roundtrip_y_no_determinista_hmac_determinista():
    c = Cifrador(K1, K2)
    a, b = c.cifrar("900.123.456-7"), c.cifrar("900.123.456-7")
    assert a != b and c.descifrar(a) == c.descifrar(b) == "900.123.456-7"
    assert a[:1] == b"\x01" and b"900" not in a
    assert c.hmac(normalizar_nit("NIT 900.123.456-7")) == c.hmac(normalizar_nit("9001234567"))
    assert c.hmac("x") != Cifrador(K1, "c" * 43).hmac("x"), "otra llave, otro HMAC"
    assert c.cifrar(None) is None and c.hmac(None) is None


def test_normalizaciones():
    assert normalizar_nit("NIT 900.123.456-7") == "9001234567"
    assert normalizar_email("  Juan.Perez@Mail.com ") == "juan.perez@mail.com"
    assert normalizar_telefono("+57 310 456 7890") == "3104567890"


def test_logger_json_sin_pii(tmp_path: Path):
    ruta = tmp_path / "p.log"
    logger = configurar(ruta, "corrida-1", "test", {"observaciones", "nit"})
    log(logger, "evento_prueba", archivo="x.csv", observaciones="correo juan@x.com", nit="900.1",
        detalle={"email_contacto": "ana@y.co", "cel": "3001234567", "ok": 3})
    logger.handlers[0].flush()
    linea = json.loads(ruta.read_text().splitlines()[-1])
    assert linea["evento"] == "evento_prueba" and linea["archivo"] == "x.csv" and linea["corrida_id"] == "corrida-1"
    assert linea["observaciones"] == "[REDACTADO]" and linea["nit"] == "[REDACTADO]"
    assert linea["detalle"]["email_contacto"] == "[EMAIL]" and linea["detalle"]["cel"] == "[CELULAR]"
    assert "juan@x.com" not in ruta.read_text() and "3001234567" not in ruta.read_text()
    logging.getLogger("bia").handlers.clear()
