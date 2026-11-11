"""Reglas B1–B5 y parsers por formato, sin base de datos."""
from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from bia.ingesta import archivos, consumos
from bia.ingesta.archivos import ArchivoCrudo, ArchivoIlegible


def crudo(nombre: str, contenido: bytes) -> ArchivoCrudo:
    return ArchivoCrudo(Path(nombre), nombre, len(contenido), hashlib.sha256(contenido).hexdigest(), contenido)


def parsear(nombre: str, texto: str, enc: str = "utf-8"):
    a = crudo(nombre, texto.encode(enc))
    fmt = archivos.detectar_formato(a.contenido)
    return fmt, consumos.parsear_archivo(a, fmt, 0)


# ------------------------------------------------------------ formatos ----
def test_formato_enero_coma_local():
    fmt, res = parsear("a.csv", "frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\r\n"
                                "CO1114174638,2026-01-01 00:00:00,275.283,1,OK\r\n")
    assert (fmt.separador, fmt.ts_es_utc, fmt.decimal_coma, fmt.unidad_por_defecto) == (",", False, False, "kWh")
    l = res.lecturas[0]
    assert l.ts_local == datetime(2026, 1, 1, 0, 0) and l.valor_kwh == Decimal("275.283")


def test_formato_febrero_puntocoma_utc_coma_decimal():
    fmt, res = parsear("b.csv", "frontera_id;timestamp_utc;consumo_kwh;version_lectura;calidad_lectura\r\n"
                                "CO1114174638;2026-02-01T05:00:00Z;163,795;1;OK\r\n"
                                "CO1114174638;2026-03-01T04:00:00Z;81,77;1;OK\r\n")
    assert fmt.separador == ";" and fmt.ts_es_utc and fmt.decimal_coma
    assert res.lecturas[0].ts_local == datetime(2026, 2, 1, 0, 0), "B1: 05:00Z es medianoche en Bogotá"
    assert res.lecturas[1].ts_local == datetime(2026, 2, 28, 23, 0), "B1: 01-mar 04:00Z sigue siendo febrero local"
    assert res.lecturas[0].valor_kwh == Decimal("163.795")


def test_formato_marzo_unidad_mwh_se_convierte():
    _, res = parsear("c.csv", "frontera_id,timestamp,kwh,unidad,version_lectura,calidad_lectura\n"
                              "CO1114174638,2026-03-01 00:00:00,126.985,kWh,1,OK\n"
                              "CO1114174638,2026-03-01 01:00:00,0.152522,MWh,1,OK\n")
    assert res.mwh_convertidos == 1
    assert res.lecturas[1].valor_kwh == Decimal("152.522000") and res.lecturas[1].unidad_origen == "MWh"


def test_timestamp_con_offset_explicito_y_latin1():
    _, res = parsear("d.csv", "frontera_id,timestamp,consumo_kwh\nCO1114174638,2026-01-01T03:00:00+03:00,1\n", "latin-1")
    assert res.lecturas[0].ts_local == datetime(2025, 12, 31, 19, 0)
    assert res.lecturas[0].version == 1, "sin columna de versión → 1"


def test_cabecera_irreconocible_rechaza_archivo_entero():
    with pytest.raises(ArchivoIlegible):
        archivos.detectar_formato(b"\x89PNG\r\n\x1a\n" + bytes(range(256)))
    with pytest.raises(ArchivoIlegible):
        archivos.detectar_formato(b"a,b,c\n1,2,3\n")
    with pytest.raises(ArchivoIlegible):
        archivos.detectar_formato(b"")


def test_filas_malformadas_van_a_rechazo_y_no_detienen_el_archivo():
    _, res = parsear("e.csv", "frontera_id,timestamp,consumo_kwh,version_lectura,calidad_lectura\n"
                              "CO1114174638,2026-01-01 00:00:00,1,1,OK\n"
                              "CO1114174638,2026-01-01 01:00:00\n"            # campos de menos
                              "XX,2026-01-01 02:00:00,1,1,OK\n"                 # frontera inválida
                              "CO1114174638,ayer,1,1,OK\n"                      # timestamp inválido
                              "CO1114174638,2026-01-01 04:00:00,abc,1,OK\n"     # valor no numérico
                              "CO1114174638,2026-01-01 05:00:00,2,1,OK\n")
    assert res.filas_leidas == 6 and len(res.lecturas) == 2
    assert sorted(r.regla for r in res.rechazos) == ["FILA_MALFORMADA", "FRONTERA_INVALIDA", "TIMESTAMP_INVALIDO", "VALOR_NO_NUMERICO"]


def test_bytes_nulos_se_rechazan_sin_perder_lo_anterior():
    _, res = parsear("f.csv", "frontera_id,timestamp,consumo_kwh\nCO1114174638,2026-01-01 00:00:00,1\nCO11\x00\x00,2026\n"
                              "CO1114174638,2026-01-01 02:00:00,3\n")
    assert len(res.lecturas) == 2 and res.rechazos[0].regla == "FILA_CORRUPTA"
    assert res.rechazos[0].payload == {}, "una fila corrupta no se guarda tal cual en cuarentena"


# ------------------------------------------------------------ B3 / B4 ----
def L(f, ts, v, ver=1, archivo="a.csv", orden=0):
    return consumos.Lectura(f, ts, None if v is None else Decimal(v), ver, None, "kWh", archivo, orden, orden, str(v))


def test_b3_mayor_version_gana_y_cuenta_correccion():
    t = datetime(2026, 4, 1)
    dd = consumos.deduplicar([L("CO1", t, "10", 1, "consumos_2026-04.csv", 1), L("CO1", t, "12", 2, "consumos_2026-04_correccion.csv", 2)])
    assert [g.valor_kwh for g in dd.ganadoras] == [Decimal("12")]
    assert (dd.duplicados_exactos, dd.duplicados_conflictivos, dd.correcciones_tardias) == (0, 1, 1)


def test_b3_empate_gana_la_ultima_en_orden_de_archivo():
    t = datetime(2026, 1, 1)
    dd = consumos.deduplicar([L("CO1", t, "1428.956", 1, orden=1), L("CO1", t, "2161.0", 1, orden=2)])
    assert dd.ganadoras[0].valor_kwh == Decimal("2161.0") and dd.duplicados_conflictivos == 1


def test_b3_exactos_vs_conflictivos_cuentan_filas_sobrantes():
    t = datetime(2026, 1, 1)
    dd = consumos.deduplicar([L("CO1", t, "5", orden=1), L("CO1", t, "5", orden=2), L("CO1", t, "7", orden=3)])
    # ganadora: la última (7); sobran dos: una exacta entre sí pero distinta a la ganadora → ambas conflictivas
    assert len(dd.ganadoras) == 1 and dd.duplicados_exactos + dd.duplicados_conflictivos == 2
    dd2 = consumos.deduplicar([L("CO1", t, "5", orden=1), L("CO1", t, "5", orden=2)])
    assert (dd2.duplicados_exactos, dd2.duplicados_conflictivos) == (1, 0)


def test_b4_descarta_nulo_negativo_outlier_sin_imputar():
    t = datetime(2026, 1, 1)
    va = consumos.validar([L("CO1", t, None), L("CO1", t, "-1"), L("CO1", t, "1000000.001"), L("CO1", t, "1000000"), L("CO1", t, "0")])
    assert (va.nulos, va.negativos, va.outliers) == (1, 1, 1)
    assert [v.valor_kwh for v in va.validas] == [Decimal("1000000"), Decimal("0")], "límite inclusivo: > 1e6 es outlier, 1e6 no"


def test_b5_huecos_cuentan_filas_ausentes_y_descartadas():
    validas = [L("CO1", datetime(2026, 2, 1, h), "1") for h in range(24)]       # 1 día válido de 28
    h = consumos.huecos(validas, {"CO1", "CO2"}, {(2026, 2)})
    assert h == {"CO1": 28 * 24 - 24, "CO2": 28 * 24}
