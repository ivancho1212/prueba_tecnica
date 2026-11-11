"""Descubrimiento de archivos y detección de formato por archivo.

No se asume nada por el nombre del mes: cada archivo se inspecciona (encoding,
separador, columnas, zona horaria, unidad). Un archivo cuya cabecera no permita
identificar frontera, timestamp y valor se rechaza ENTERO.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ENCODINGS = ("utf-8-sig", "utf-8", "latin-1")
COL_FRONTERA = ("frontera_id", "frontera", "codigo_frontera")
COL_TS = ("timestamp", "timestamp_utc", "ts", "fecha_hora", "fechahora", "datetime", "fecha")
COL_VALOR = ("consumo_kwh", "kwh", "consumo", "valor", "consumo_mwh", "mwh", "energia_kwh", "energia")
COL_UNIDAD = ("unidad", "unit", "unidades")
COL_VERSION = ("version_lectura", "version", "ver")
COL_CALIDAD = ("calidad_lectura", "calidad", "quality")


class ArchivoIlegible(Exception):
    """El archivo no tiene una cabecera reconocible: se rechaza entero."""


@dataclass
class ArchivoCrudo:
    ruta: Path
    nombre: str
    tamano: int
    sha256: str
    contenido: bytes = field(repr=False)


@dataclass
class Formato:
    encoding: str
    separador: str
    columnas: list[str]
    col_frontera: str
    col_ts: str
    col_valor: str
    col_unidad: str | None
    col_version: str | None
    col_calidad: str | None
    ts_es_utc: bool          # por nombre de columna; además se respeta el offset si la fila lo trae
    unidad_por_defecto: str  # 'kWh' | 'MWh' deducida del nombre de la columna de valor
    decimal_coma: bool       # detectado en muestra: '163,795'

    def como_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def descubrir(directorio: Path, patron: str = "*.csv") -> list[ArchivoCrudo]:
    """Todos los archivos que coincidan, en orden lexicográfico de nombre (B3)."""
    out = []
    for ruta in sorted(directorio.glob(patron), key=lambda p: p.name):
        if not ruta.is_file():
            continue
        contenido = ruta.read_bytes()
        out.append(ArchivoCrudo(ruta, ruta.name, len(contenido),
                                hashlib.sha256(contenido).hexdigest(), contenido))
    return out


def decodificar(contenido: bytes) -> tuple[str, str]:
    for enc in ENCODINGS:
        try:
            return contenido.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return contenido.decode("latin-1", errors="replace"), "latin-1"


def _buscar(columnas: list[str], candidatas: tuple[str, ...]) -> str | None:
    for c in candidatas:
        if c in columnas:
            return c
    return None


def detectar_formato(contenido: bytes) -> Formato:
    texto, enc = decodificar(contenido)
    primera = texto.split("\n", 1)[0].strip("\r")
    if not primera.strip():
        raise ArchivoIlegible("archivo vacío")
    if "\x00" in primera:
        raise ArchivoIlegible("cabecera con bytes nulos")
    separador = max((";", ",", "\t", "|"), key=primera.count)
    if primera.count(separador) == 0:
        raise ArchivoIlegible("cabecera sin separador reconocible")
    columnas = [c.strip().strip('"').lower() for c in next(csv.reader(io.StringIO(primera), delimiter=separador))]
    col_frontera = _buscar(columnas, COL_FRONTERA)
    col_ts = _buscar(columnas, COL_TS)
    col_valor = _buscar(columnas, COL_VALOR)
    if not (col_frontera and col_ts and col_valor):
        raise ArchivoIlegible(f"cabecera sin frontera/timestamp/valor identificables: {columnas}")

    # muestra para decidir el separador decimal
    muestra = texto.split("\n", 51)[1:51]
    idx = columnas.index(col_valor)
    coma = punto = 0
    for linea in muestra:
        campos = linea.rstrip("\r").split(separador)
        if len(campos) > idx:
            v = campos[idx].strip().strip('"')
            if "," in v and "." not in v:
                coma += 1
            elif "." in v:
                punto += 1
    return Formato(
        encoding=enc, separador=separador, columnas=columnas,
        col_frontera=col_frontera, col_ts=col_ts, col_valor=col_valor,
        col_unidad=_buscar(columnas, COL_UNIDAD),
        col_version=_buscar(columnas, COL_VERSION),
        col_calidad=_buscar(columnas, COL_CALIDAD),
        ts_es_utc="utc" in col_ts,
        unidad_por_defecto="MWh" if "mwh" in col_valor else "kWh",
        decimal_coma=coma > punto,
    )
