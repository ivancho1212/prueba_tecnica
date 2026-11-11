"""Maestros: clientes (PII → cifrado/HMAC/enmascarado), fronteras y límites.

El maestro de clientes se trata como entrada hostil (B7): lector csv real,
encoding detectado, columnas TEXT, SQL parametrizado. Nada de su contenido se
loguea; lo que se rechaza va a cuarentena con el payload cifrado.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from bia import pii
from bia.classification import Clasificacion
from bia.crypto import Cifrador, hash_fila, normalizar_email, normalizar_nit, normalizar_telefono
from bia.ingesta.archivos import ArchivoCrudo, decodificar
from bia.ingesta.consumos import Rechazo

COLS_CLIENTE = ["cliente_id", "razon_social", "nit", "email_contacto", "celular_contacto", "direccion",
                "ciudad", "departamento", "representante_legal", "observaciones"]
COLS_FRONTERA = ["frontera_id", "codigo_sic", "cliente_id", "mercado", "operador_red", "nivel_tension",
                 "fecha_registro", "estado"]
COLS_LIMITE = ["frontera_id", "min_kwh", "max_kwh"]
LONGITUD_MAXIMA = 4000   # por campo; más que eso se cuarentena como hostil, no se trunca en silencio


@dataclass
class ClienteNormalizado:
    cliente_id: str
    razon_social: str
    nit_cifrado: bytes
    nit_hmac: bytes
    email_cifrado: bytes | None
    email_hmac: bytes | None
    celular_cifrado: bytes | None
    direccion_cifrada: bytes | None
    ciudad: str | None
    departamento: str | None
    representante_legal_cifrado: bytes | None
    observaciones_cifrado: bytes | None
    observaciones_enmascaradas: str | None
    pii_en_observaciones: bool
    tipos_pii: list[str]
    hash_fila: str
    fila: int


@dataclass
class ResultadoMaestro:
    filas: list = field(default_factory=list)
    rechazos: list[Rechazo] = field(default_factory=list)
    filas_leidas: int = 0
    clientes_con_pii_texto_libre: int = 0


def _leer_csv(archivo: ArchivoCrudo, columnas_esperadas: list[str]) -> tuple[list[dict], list[Rechazo], int]:
    texto, _ = decodificar(archivo.contenido)
    lector = csv.DictReader(io.StringIO(texto, newline=""))
    if lector.fieldnames is None:
        return [], [Rechazo(archivo.nombre, None, "ARCHIVO_CORRUPTO", "sin cabecera", None, {})], 0
    cab = [c.strip().lower() for c in lector.fieldnames]
    faltan = [c for c in columnas_esperadas if c not in cab]
    if faltan:
        return [], [Rechazo(archivo.nombre, None, "ARCHIVO_CORRUPTO", f"faltan columnas {faltan}", None, {})], 0
    lector.fieldnames = cab
    filas, rechazos, leidas = [], [], 0
    while True:
        try:
            fila = next(lector)
        except StopIteration:
            break
        except csv.Error as e:
            rechazos.append(Rechazo(archivo.nombre, lector.line_num, "ARCHIVO_TRUNCADO", str(e), None, {}))
            break
        leidas += 1
        fila["_linea"] = lector.line_num
        if None in fila or any(v is None for k, v in fila.items() if k != "_linea"):
            rechazos.append(Rechazo(archivo.nombre, lector.line_num, "FILA_MALFORMADA",
                                    "número de campos distinto a la cabecera", None,
                                    {k: v for k, v in fila.items() if k is not None}))
            continue
        filas.append(fila)
    return filas, rechazos, leidas


def procesar_clientes(archivo: ArchivoCrudo, cifrador: Cifrador, clasif: Clasificacion) -> ResultadoMaestro:
    res = ResultadoMaestro()
    filas, res.rechazos, res.filas_leidas = _leer_csv(archivo, COLS_CLIENTE)
    campos_a_cifrar = set(clasif.campos_origen_por_politica("clientes.csv", cifrado_en_reposo=True))
    vistos: set[str] = set()
    for f in filas:
        linea = f.pop("_linea")
        payload = dict(f)
        cid = (f.get("cliente_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", cid):
            res.rechazos.append(Rechazo(archivo.nombre, linea, "CLIENTE_ID_INVALIDO", "cliente_id vacío o inválido", None, payload))
            continue
        if cid in vistos:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "DUPLICADO_MAESTRO", "cliente_id repetido", cid, payload))
            continue
        largos = [k for k, v in f.items() if v and len(v) > LONGITUD_MAXIMA]
        if largos:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "CAMPO_EXCESIVO", f"campos > {LONGITUD_MAXIMA} chars: {largos}", cid, payload))
            continue
        nit_norm = normalizar_nit(f.get("nit"))
        if not nit_norm:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "NIT_INVALIDO", "NIT vacío o sin dígitos", cid, payload))
            continue
        vistos.add(cid)
        # La clasificación manda: lo que no deba cifrarse no se cifra, y viceversa.
        assert {"nit", "email_contacto", "celular_contacto", "direccion", "representante_legal", "observaciones"} <= campos_a_cifrar
        obs = f.get("observaciones") or None
        mascara = pii.enmascarar(obs) if obs else pii.ResultadoPII("")
        if mascara.tiene_pii:
            res.clientes_con_pii_texto_libre += 1
        res.filas.append(ClienteNormalizado(
            cliente_id=cid,
            razon_social=(f.get("razon_social") or "").strip(),
            nit_cifrado=cifrador.cifrar(f["nit"].strip()),
            nit_hmac=cifrador.hmac(nit_norm),
            email_cifrado=cifrador.cifrar(f.get("email_contacto") or None),
            email_hmac=cifrador.hmac(normalizar_email(f.get("email_contacto"))),
            celular_cifrado=cifrador.cifrar(f.get("celular_contacto") or None),
            direccion_cifrada=cifrador.cifrar(f.get("direccion") or None),
            ciudad=(f.get("ciudad") or "").strip() or None,
            departamento=(f.get("departamento") or "").strip() or None,
            representante_legal_cifrado=cifrador.cifrar(f.get("representante_legal") or None),
            observaciones_cifrado=cifrador.cifrar(obs),
            observaciones_enmascaradas=mascara.texto if obs else None,
            pii_en_observaciones=mascara.tiene_pii,
            tipos_pii=mascara.tipos,
            hash_fila=hash_fila(*(f.get(c) for c in COLS_CLIENTE)),
            fila=linea,
        ))
    return res


def procesar_fronteras(archivo: ArchivoCrudo, clientes_validos: set[str]) -> ResultadoMaestro:
    res = ResultadoMaestro()
    filas, res.rechazos, res.filas_leidas = _leer_csv(archivo, COLS_FRONTERA)
    vistos: set[str] = set()
    for f in filas:
        linea = f.pop("_linea"); payload = dict(f)
        fid = (f.get("frontera_id") or "").strip()
        if not re.fullmatch(r"CO\d{10}", fid):
            res.rechazos.append(Rechazo(archivo.nombre, linea, "FRONTERA_INVALIDA", "frontera_id inválido", fid or None, payload)); continue
        if fid in vistos:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "DUPLICADO_MAESTRO", "frontera_id repetido", fid, payload)); continue
        cid = (f.get("cliente_id") or "").strip()
        if cid not in clientes_validos:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "CLIENTE_DESCONOCIDO", "cliente_id no existe en el maestro", fid, payload)); continue
        try:
            nivel = int(f["nivel_tension"]) if (f.get("nivel_tension") or "").strip() else None
            fecha = date.fromisoformat(f["fecha_registro"].strip()) if (f.get("fecha_registro") or "").strip() else None
        except ValueError as e:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "FILA_MALFORMADA", str(e), fid, payload)); continue
        vistos.add(fid)
        res.filas.append({
            "frontera_id": fid, "codigo_sic": (f.get("codigo_sic") or "").strip(), "cliente_id": cid,
            "mercado": (f.get("mercado") or "").strip() or None, "operador_red": (f.get("operador_red") or "").strip() or None,
            "nivel_tension": nivel, "fecha_registro": fecha, "estado": (f.get("estado") or "").strip() or None,
            "hash_fila": hash_fila(*(f.get(c) for c in COLS_FRONTERA)), "fila": linea,
        })
    return res


def procesar_limites(archivo: ArchivoCrudo, fronteras_validas: set[str]) -> ResultadoMaestro:
    res = ResultadoMaestro()
    filas, res.rechazos, res.filas_leidas = _leer_csv(archivo, COLS_LIMITE)
    vistos: set[str] = set()
    for f in filas:
        linea = f.pop("_linea"); payload = dict(f)
        fid = (f.get("frontera_id") or "").strip()
        if fid not in fronteras_validas:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "FRONTERA_DESCONOCIDA", "frontera_id no existe en el maestro", fid or None, payload)); continue
        if fid in vistos:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "DUPLICADO_MAESTRO", "frontera_id repetido", fid, payload)); continue
        try:
            mn, mx = Decimal(f["min_kwh"].strip()), Decimal(f["max_kwh"].strip())
        except (InvalidOperation, AttributeError):
            res.rechazos.append(Rechazo(archivo.nombre, linea, "VALOR_NO_NUMERICO", "min/max no numérico", fid, payload)); continue
        if mn < 0 or mx < mn:
            res.rechazos.append(Rechazo(archivo.nombre, linea, "LIMITE_INCOHERENTE", "min_kwh > max_kwh o negativo", fid, payload)); continue
        vistos.add(fid)
        res.filas.append({"frontera_id": fid, "min_kwh": mn, "max_kwh": mx,
                          "hash_fila": hash_fila(*(f.get(c) for c in COLS_LIMITE)), "fila": linea})
    return res
