"""B6: facturas con frontera inexistente no se cargan, van a cuarentena y se reportan."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bia.crypto import Cifrador
from bia.ingesta.archivos import ArchivoCrudo
from bia.ingesta.facturacion import procesar_facturacion

CIF = Cifrador("a" * 43, "b" * 43)
MAESTRO = {"CO1111111111", "CO2222222222"}


def crudo(doc, nombre="facturacion_2026-01.json") -> ArchivoCrudo:
    b = json.dumps(doc, ensure_ascii=False).encode("utf-8") if not isinstance(doc, bytes) else doc
    return ArchivoCrudo(Path(nombre), nombre, len(b), hashlib.sha256(b).hexdigest(), b)


def factura(fid, frontera, nit="900.123.456-7", email="pagos@empresa.com"):
    return {"factura_id": fid, "frontera_id": frontera,
            "cliente": {"nit": nit, "razon_social": "Empresa S.A.S.", "email_facturacion": email},
            "consumo_facturado_kwh": 1234.5, "tarifa_cop_kwh": 500.1, "valor_total_cop": 617000.0,
            "componentes": [{"concepto": "G", "valor_cop_kwh": 200.0}],
            "metodo_pago": {"tipo": "pse", "cuenta_enmascarada": "****1234"}}


def test_huerfana_a_cuarentena_valida_al_modelo():
    doc = {"periodo": "2026-01", "facturas": [factura("F-1", "CO1111111111"), factura("F-2", "CO9999999999")]}
    res = procesar_facturacion(crudo(doc), CIF, MAESTRO)
    assert res.filas_leidas == 2 and [f.factura_id for f in res.filas] == ["F-1"]
    assert res.fronteras_huerfanas == {"CO9999999999"}
    assert [(r.regla, r.clave, r.fila) for r in res.rechazos] == [("B6_HUERFANA", "F-2", 2)]
    assert res.rechazos[0].payload["cliente"]["nit"], "el payload completo va a cuarentena (se cifra al cargar)"


def test_pii_cifrada_y_hmac_normalizado():
    doc = {"periodo": "2026-02", "facturas": [factura("F-1", "CO1111111111", nit="NIT 900123456-7", email="Pagos@Empresa.com")]}
    f = procesar_facturacion(crudo(doc), CIF, MAESTRO).filas[0]
    assert f.periodo.isoformat() == "2026-02-01"
    # No comparar bytes sueltos ("@", "900"): el cifrado es aleatorio y un byte
    # concreto aparece por azar ~16 % de las veces en 45 bytes. Se comprueba
    # que el valor completo (crudo y normalizado) no esté en claro.
    for claro in (b"900123456", b"9001234567", b"Pagos@Empresa.com", b"pagos@empresa.com"):
        assert claro not in f.cliente_nit_cifrado and claro not in f.cliente_email_cifrado
    assert CIF.descifrar(f.cliente_nit_cifrado) == "NIT 900123456-7"
    assert f.cliente_nit_hmac == CIF.hmac("9001234567") and f.cliente_email_hmac == CIF.hmac("pagos@empresa.com")
    assert json.loads(f.componentes)[0]["concepto"] == "G"


def test_hash_fila_estable_entre_corridas():
    doc = {"periodo": "2026-01", "facturas": [factura("F-1", "CO1111111111")]}
    a = procesar_facturacion(crudo(doc), CIF, MAESTRO).filas[0]
    b = procesar_facturacion(crudo(doc), CIF, MAESTRO).filas[0]
    assert a.hash_fila == b.hash_fila, "mismo contenido → mismo hash → no se recifra"
    assert a.cliente_nit_cifrado != b.cliente_nit_cifrado, "el cifrado sí cambia (nonce), por eso el hash manda"
    doc["facturas"][0]["valor_total_cop"] = 1.0
    assert procesar_facturacion(crudo(doc), CIF, MAESTRO).filas[0].hash_fila != a.hash_fila


def test_archivo_corrupto_y_facturas_malformadas():
    assert procesar_facturacion(crudo(b"\x89PNG no json"), CIF, MAESTRO).rechazos[0].regla == "ARCHIVO_CORRUPTO"
    assert procesar_facturacion(crudo({"periodo": "2026-01"}), CIF, MAESTRO).rechazos[0].regla == "ARCHIVO_CORRUPTO"
    assert procesar_facturacion(crudo({"periodo": "enero", "facturas": []}), CIF, MAESTRO).rechazos[0].regla == "ARCHIVO_CORRUPTO"
    doc = {"periodo": "2026-01", "facturas": [
        factura("F-1", "CO1111111111"), factura("F-1", "CO1111111111"),           # repetida
        {**factura("F-3", "CO1111111111"), "consumo_facturado_kwh": "abc"},        # no numérico
        {**factura("", "CO1111111111")}, "no soy un objeto"]}
    res = procesar_facturacion(crudo(doc), CIF, MAESTRO)
    assert [f.factura_id for f in res.filas] == ["F-1"]
    assert sorted(r.regla for r in res.rechazos) == ["DUPLICADO_MAESTRO", "FILA_MALFORMADA", "FILA_MALFORMADA", "VALOR_NO_NUMERICO"]
