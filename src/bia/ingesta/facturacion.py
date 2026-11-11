"""Facturación mensual (JSON anidado) y regla B6.

B6: una factura cuya `frontera_id` no existe en el maestro no se carga al
modelo: va a cuarentena (payload cifrado, porque trae PII) y se reporta como
`fronteras_huerfanas_facturacion`. El bloque `cliente` duplica PII del maestro:
`nit` y `email_facturacion` se cifran y llevan HMAC como en `dim_cliente`.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from bia.crypto import Cifrador, hash_fila, normalizar_email, normalizar_nit
from bia.ingesta.archivos import ArchivoCrudo
from bia.ingesta.consumos import Rechazo

RE_PERIODO = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass
class FacturaNormalizada:
    factura_id: str
    periodo: date
    frontera_id: str
    consumo_facturado_kwh: Decimal
    tarifa_cop_kwh: Decimal | None
    valor_total_cop: Decimal | None
    componentes: str | None            # JSON serializado
    metodo_pago_tipo: str | None
    cuenta_enmascarada: str | None
    cliente_razon_social: str | None
    cliente_nit_cifrado: bytes | None
    cliente_nit_hmac: bytes | None
    cliente_email_cifrado: bytes | None
    cliente_email_hmac: bytes | None
    hash_fila: str
    fila: int                          # posición 1-based dentro de `facturas`


@dataclass
class ResultadoFacturacion:
    filas: list[FacturaNormalizada] = field(default_factory=list)
    rechazos: list[Rechazo] = field(default_factory=list)
    filas_leidas: int = 0
    fronteras_huerfanas: set[str] = field(default_factory=set)


def _decimal(v, campo: str) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        raise ValueError(f"{campo} no numérico") from None


def procesar_facturacion(archivo: ArchivoCrudo, cifrador: Cifrador, fronteras_maestro: set[str]) -> ResultadoFacturacion:
    res = ResultadoFacturacion()
    try:
        doc = json.loads(archivo.contenido.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        res.rechazos.append(Rechazo(archivo.nombre, None, "ARCHIVO_CORRUPTO", f"JSON inválido: {str(e)[:120]}", None, {}))
        return res
    if not isinstance(doc, dict) or not isinstance(doc.get("facturas"), list):
        res.rechazos.append(Rechazo(archivo.nombre, None, "ARCHIVO_CORRUPTO", "sin lista 'facturas'", None, {}))
        return res
    m = RE_PERIODO.match(str(doc.get("periodo", "")))
    if not m:
        res.rechazos.append(Rechazo(archivo.nombre, None, "ARCHIVO_CORRUPTO", "periodo ausente o inválido", None, {}))
        return res
    periodo = date(int(m.group(1)), int(m.group(2)), 1)

    vistos: set[str] = set()
    for i, fac in enumerate(doc["facturas"], start=1):
        res.filas_leidas += 1
        if not isinstance(fac, dict):
            res.rechazos.append(Rechazo(archivo.nombre, i, "FILA_MALFORMADA", "factura no es un objeto", None, {}))
            continue
        fid = str(fac.get("factura_id") or "").strip()
        frontera = str(fac.get("frontera_id") or "").strip()
        cliente = fac.get("cliente") if isinstance(fac.get("cliente"), dict) else {}
        if not fid:
            res.rechazos.append(Rechazo(archivo.nombre, i, "FILA_MALFORMADA", "factura_id vacío", frontera or None, fac))
            continue
        if fid in vistos:
            res.rechazos.append(Rechazo(archivo.nombre, i, "DUPLICADO_MAESTRO", "factura_id repetido en el archivo", fid, fac))
            continue
        if frontera not in fronteras_maestro:                                  # B6
            res.fronteras_huerfanas.add(frontera)
            res.rechazos.append(Rechazo(archivo.nombre, i, "B6_HUERFANA",
                                        "frontera_id no existe en el maestro de fronteras", fid, fac))
            continue
        try:
            consumo = _decimal(fac.get("consumo_facturado_kwh"), "consumo_facturado_kwh")
            if consumo is None:
                raise ValueError("consumo_facturado_kwh ausente")
            tarifa = _decimal(fac.get("tarifa_cop_kwh"), "tarifa_cop_kwh")
            total = _decimal(fac.get("valor_total_cop"), "valor_total_cop")
        except ValueError as e:
            res.rechazos.append(Rechazo(archivo.nombre, i, "VALOR_NO_NUMERICO", str(e), fid, fac))
            continue
        vistos.add(fid)
        pago = fac.get("metodo_pago") if isinstance(fac.get("metodo_pago"), dict) else {}
        nit = (cliente.get("nit") or "").strip() or None
        email = (cliente.get("email_facturacion") or "").strip() or None
        res.filas.append(FacturaNormalizada(
            factura_id=fid, periodo=periodo, frontera_id=frontera,
            consumo_facturado_kwh=consumo, tarifa_cop_kwh=tarifa, valor_total_cop=total,
            componentes=json.dumps(fac.get("componentes"), ensure_ascii=False) if fac.get("componentes") is not None else None,
            metodo_pago_tipo=(pago.get("tipo") or None), cuenta_enmascarada=(pago.get("cuenta_enmascarada") or None),
            cliente_razon_social=(cliente.get("razon_social") or "").strip() or None,
            cliente_nit_cifrado=cifrador.cifrar(nit), cliente_nit_hmac=cifrador.hmac(normalizar_nit(nit)),
            cliente_email_cifrado=cifrador.cifrar(email), cliente_email_hmac=cifrador.hmac(normalizar_email(email)),
            hash_fila=hash_fila(doc.get("periodo"), json.dumps(fac, sort_keys=True, ensure_ascii=False)),
            fila=i,
        ))
    return res
