"""Parseo y normalización de lecturas horarias (B1, B2, B4) y reglas B3/B5.

Flujo por fila: parsear → normalizar zona horaria (B1) → convertir unidad (B2).
Después, sobre el conjunto: deduplicar (B3) → descartar inválidas (B4) →
huecos (B5). El orden B3→B4 sigue el del enunciado: "gana la lectura con mayor
version_lectura" se aplica a todas las lecturas, válidas o no (ADR-005).
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from bia.ingesta.archivos import ArchivoCrudo, Formato, decodificar

BOGOTA = timezone(timedelta(hours=-5), "America/Bogota")   # UTC-5 fijo, sin DST (B1)
UTC = timezone.utc
LIMITE_OUTLIER = Decimal("1000000")                        # > 1.000.000 kWh (B4)
NULOS = {"", "null", "none", "na", "nan", "n/a", "-"}
MWH = Decimal("1000")

_RE_TS = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$"
)


# ---------------------------------------------------------------- tipos ----
@dataclass
class Lectura:
    frontera_id: str
    ts_local: datetime            # naive, hora Colombia
    valor_kwh: Decimal | None     # None = nulo/vacío
    version: int
    calidad: str | None
    unidad_origen: str            # 'kWh' | 'MWh'
    archivo: str
    fila: int                     # línea física en el archivo
    orden: int                    # orden global de llegada (archivo lexicográfico, luego fila)
    valor_crudo: str

    @property
    def clave(self) -> tuple[str, datetime]:
        return (self.frontera_id, self.ts_local)


@dataclass
class Rechazo:
    archivo: str
    fila: int | None
    regla: str
    motivo: str
    clave: str | None
    payload: dict


@dataclass
class ResultadoParseo:
    lecturas: list[Lectura] = field(default_factory=list)
    rechazos: list[Rechazo] = field(default_factory=list)
    filas_leidas: int = 0
    mwh_convertidos: int = 0


# ------------------------------------------------------------- parseo ------
def parsear_timestamp(texto: str, asumir_utc: bool) -> datetime:
    """Devuelve hora local Colombia sin zona. Acepta 'YYYY-MM-DD HH:MM[:SS]',
    ISO con 'T', sufijo 'Z' u offset. Sin zona: UTC si la columna es *_utc,
    local en caso contrario."""
    m = _RE_TS.match(texto.strip())
    if not m:
        raise ValueError(f"timestamp no reconocido: {texto!r}")
    y, mo, d, h, mi, s, tz = m.groups()
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s or 0))
    if tz == "Z":
        return dt.replace(tzinfo=UTC).astimezone(BOGOTA).replace(tzinfo=None)
    if tz:
        signo = 1 if tz[0] == "+" else -1
        hh, mm = int(tz[1:3]), int(tz[-2:])
        off = timezone(signo * timedelta(hours=hh, minutes=mm))
        return dt.replace(tzinfo=off).astimezone(BOGOTA).replace(tzinfo=None)
    if asumir_utc:
        return dt.replace(tzinfo=UTC).astimezone(BOGOTA).replace(tzinfo=None)
    return dt


def parsear_valor(texto: str, decimal_coma: bool) -> Decimal | None:
    """None si es nulo/vacío; ValueError si no es numérico."""
    v = texto.strip().strip('"')
    if v.lower() in NULOS:
        return None
    if decimal_coma or ("," in v and "." not in v):
        v = v.replace(".", "").replace(",", ".") if decimal_coma and "." in v and "," in v else v.replace(",", ".")
    try:
        return Decimal(v)
    except InvalidOperation:
        raise ValueError(f"valor no numérico: {texto!r}") from None


def parsear_archivo(archivo: ArchivoCrudo, fmt: Formato, orden_base: int) -> ResultadoParseo:
    res = ResultadoParseo()
    texto, _ = decodificar(archivo.contenido)
    lector = csv.reader(io.StringIO(texto, newline=""), delimiter=fmt.separador)
    try:
        next(lector)  # cabecera ya validada en detectar_formato
    except StopIteration:
        return res
    ncols = len(fmt.columnas)
    idx = {c: i for i, c in enumerate(fmt.columnas)}
    orden = orden_base
    while True:
        try:
            campos = next(lector)
        except StopIteration:
            break
        except csv.Error as e:
            # Python < 3.11 lanza "line contains NUL"; >= 3.11 entrega la fila y
            # la detecta el chequeo de NUL de abajo. Ambos caminos deben dar el
            # mismo resultado: la fila es FILA_CORRUPTA y el parser continúa (el
            # lector reinicia su estado en cada iteración). Lo mismo para
            # cualquier otro csv.Error de una fila (p. ej. campo > field_size_limit).
            res.filas_leidas += 1
            res.rechazos.append(Rechazo(archivo.nombre, lector.line_num, "FILA_CORRUPTA",
                                        f"fila ilegible para csv en línea {lector.line_num}: {e}", None, {}))
            continue
        fila = lector.line_num
        if not campos or all(not c.strip() for c in campos):
            continue
        res.filas_leidas += 1
        payload = {fmt.columnas[i]: campos[i] for i in range(min(ncols, len(campos)))}
        if any("\x00" in c for c in campos):   # Python ≥ 3.11 ya no lanza csv.Error por NUL
            res.rechazos.append(Rechazo(archivo.nombre, fila, "FILA_CORRUPTA",
                                        "bytes nulos en la fila (archivo truncado o binario)", None, {}))
            continue
        if len(campos) != ncols:
            res.rechazos.append(Rechazo(archivo.nombre, fila, "FILA_MALFORMADA",
                                        f"{len(campos)} campos, se esperaban {ncols}", None, payload))
            continue
        frontera = campos[idx[fmt.col_frontera]].strip()
        if not re.fullmatch(r"CO\d{10}", frontera):
            res.rechazos.append(Rechazo(archivo.nombre, fila, "FRONTERA_INVALIDA",
                                        f"frontera_id con formato inválido", frontera or None, payload))
            continue
        try:
            ts = parsear_timestamp(campos[idx[fmt.col_ts]], fmt.ts_es_utc)
        except ValueError as e:
            res.rechazos.append(Rechazo(archivo.nombre, fila, "TIMESTAMP_INVALIDO", str(e), frontera, payload))
            continue
        crudo = campos[idx[fmt.col_valor]]
        try:
            valor = parsear_valor(crudo, fmt.decimal_coma)
        except ValueError as e:
            res.rechazos.append(Rechazo(archivo.nombre, fila, "VALOR_NO_NUMERICO", str(e),
                                        f"{frontera}|{ts.isoformat()}", payload))
            continue
        unidad = fmt.unidad_por_defecto
        if fmt.col_unidad is not None:
            u = campos[idx[fmt.col_unidad]].strip().lower()
            if u in ("mwh", "megavatio hora", "megawatt hour"):
                unidad = "MWh"
            elif u in ("kwh", "", "kilovatio hora", "kilowatt hour"):
                unidad = "kWh"
            else:
                res.rechazos.append(Rechazo(archivo.nombre, fila, "UNIDAD_DESCONOCIDA", f"unidad {u!r}",
                                            f"{frontera}|{ts.isoformat()}", payload))
                continue
        if unidad == "MWh" and valor is not None:
            valor = valor * MWH                                   # B2
            res.mwh_convertidos += 1
        version = 1
        if fmt.col_version is not None:
            vtxt = campos[idx[fmt.col_version]].strip()
            if vtxt:
                try:
                    version = int(Decimal(vtxt))
                except InvalidOperation:
                    res.rechazos.append(Rechazo(archivo.nombre, fila, "VERSION_INVALIDA", f"version {vtxt!r}",
                                                f"{frontera}|{ts.isoformat()}", payload))
                    continue
        calidad = campos[idx[fmt.col_calidad]].strip() or None if fmt.col_calidad is not None else None
        orden += 1
        res.lecturas.append(Lectura(frontera, ts, valor, version, calidad, unidad,
                                    archivo.nombre, fila, orden, crudo))
    return res


# ---------------------------------------------------------------- B3 -------
@dataclass
class ResultadoDedupe:
    ganadoras: list[Lectura]
    duplicados_exactos: int
    duplicados_conflictivos: int
    correcciones_tardias: int
    descartadas: list[tuple[Lectura, str]]   # (lectura, DUPLICADO_EXACTO | DUPLICADO_CONFLICTIVO | B3_SUPERADA_POR_VERSION)


def deduplicar(lecturas: list[Lectura]) -> ResultadoDedupe:
    """Por (frontera_id, ts_local): gana mayor version_lectura; en empate, la
    última en orden de llegada (archivos en orden lexicográfico, luego fila).

    Clasificación de las filas sobrantes (ADR-005):
    - misma versión y mismo valor que la ganadora  → DUPLICADO_EXACTO
    - misma versión y valor distinto (empate real)  → DUPLICADO_CONFLICTIVO
    - versión menor que la ganadora                 → B3_SUPERADA_POR_VERSION:
      es un reemplazo (dato tardío), se cuenta en correcciones_tardias y NO en
      duplicados_conflictivos.
    """
    grupos: dict[tuple[str, datetime], list[Lectura]] = defaultdict(list)
    for l in lecturas:
        grupos[l.clave].append(l)
    ganadoras, descartadas = [], []
    exactos = conflictivos = correcciones = 0
    for grupo in grupos.values():
        ganadora = max(grupo, key=lambda l: (l.version, l.orden))
        ganadoras.append(ganadora)
        if len(grupo) == 1:
            continue
        if ganadora.version > min(l.version for l in grupo):
            correcciones += 1
        for l in grupo:
            if l is ganadora:
                continue
            if l.version < ganadora.version:
                descartadas.append((l, "B3_SUPERADA_POR_VERSION"))
            elif l.valor_kwh == ganadora.valor_kwh:
                exactos += 1
                descartadas.append((l, "DUPLICADO_EXACTO"))
            else:
                conflictivos += 1
                descartadas.append((l, "DUPLICADO_CONFLICTIVO"))
    ganadoras.sort(key=lambda l: (l.frontera_id, l.ts_local))
    return ResultadoDedupe(ganadoras, exactos, conflictivos, correcciones, descartadas)


# ---------------------------------------------------------------- B4 -------
@dataclass
class ResultadoValidacion:
    validas: list[Lectura]
    nulos: int
    negativos: int
    outliers: int
    descartadas: list[tuple[Lectura, str]]


def validar(lecturas: list[Lectura]) -> ResultadoValidacion:
    validas, desc = [], []
    nulos = negativos = outliers = 0
    for l in lecturas:
        if l.valor_kwh is None:
            nulos += 1; desc.append((l, "B4_NULO"))
        elif l.valor_kwh < 0:
            negativos += 1; desc.append((l, "B4_NEGATIVO"))
        elif l.valor_kwh > LIMITE_OUTLIER:
            outliers += 1; desc.append((l, "B4_OUTLIER"))
        else:
            validas.append(l)
    return ResultadoValidacion(validas, nulos, negativos, outliers, desc)


# ---------------------------------------------------------------- B5 -------
def huecos(validas: list[Lectura], fronteras: set[str], meses: set[tuple[int, int]]) -> dict[str, int]:
    """Horas sin lectura válida por frontera, sobre la rejilla completa de cada
    mes presente (días del mes × 24) y para TODAS las fronteras del maestro."""
    horas_validas: dict[tuple[str, tuple[int, int]], set[datetime]] = defaultdict(set)
    for l in validas:
        horas_validas[(l.frontera_id, (l.ts_local.year, l.ts_local.month))].add(l.ts_local.replace(minute=0, second=0))
    out: dict[str, int] = {}
    for f in sorted(fronteras):
        total = 0
        for (y, m) in meses:
            esperadas = calendar.monthrange(y, m)[1] * 24
            total += esperadas - len(horas_validas.get((f, (y, m)), ()))
        out[f] = total
    return out


def meses_presentes(lecturas: list[Lectura]) -> set[tuple[int, int]]:
    return {(l.ts_local.year, l.ts_local.month) for l in lecturas}


def resumen_rechazos(rechazos: list[Rechazo]) -> dict[str, int]:
    return dict(sorted(Counter(r.regla for r in rechazos).items()))
