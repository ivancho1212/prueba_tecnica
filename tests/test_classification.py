"""data_classification.yaml es un artefacto vivo: debe cubrir exactamente el DDL."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bia.classification import Clasificacion, ErrorClasificacion, NIVELES

RAIZ = Path(__file__).resolve().parent.parent
SQL = "\n".join(p.read_text(encoding="utf-8") for p in sorted((RAIZ / "sql").glob("*.sql")))

_PALABRAS_NO_COLUMNA = ("PRIMARY", "UNIQUE", "CHECK", "CONSTRAINT", "FOREIGN", "--")


def columnas_ddl() -> dict[str, dict[str, str]]:
    """{tabla: {columna: tipo}} parseando CREATE TABLE del DDL."""
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS\s+([\w.]+)\s*\((.*?)\n\);", SQL, re.S):
        tabla, cuerpo = m.group(1), m.group(2)
        cols: dict[str, str] = {}
        for linea in cuerpo.splitlines():
            s = linea.split("--")[0].strip().rstrip(",")
            if not s or s.upper().startswith(_PALABRAS_NO_COLUMNA):
                continue
            partes = s.split()
            cols[partes[0]] = partes[1].lower()
        out[tabla] = cols
    return out


def vistas_ddl() -> dict[str, str]:
    return {m.group(1): m.group(2)
            for m in re.finditer(r"CREATE OR REPLACE VIEW\s+([\w.]+)\s+AS(.*?);", SQL, re.S)}


@pytest.fixture(scope="module")
def clasif() -> Clasificacion:
    return Clasificacion.cargar()


def test_carga_y_politicas_completas(clasif):
    assert set(clasif.politicas) == set(NIVELES)
    assert clasif.politica("Restringido-PII").cifrado_en_reposo is True
    assert clasif.politica("Restringido-PII").en_logs is False
    assert clasif.politica("Confidencial").en_vista_analista is False


def test_toda_columna_del_ddl_esta_clasificada(clasif):
    ddl = columnas_ddl()
    assert ddl, "no se parseó ninguna tabla del DDL"
    faltan = [f"{t}.{c}" for t, cols in ddl.items() for c in cols if c not in clasif.tablas.get(t, {})]
    sobran = [f"{t}.{c}" for t, cols in clasif.tablas.items() if not t.startswith("analytics.")
              for c in cols if c not in ddl.get(t, {})]
    assert not faltan, f"columnas del DDL sin clasificar: {faltan}"
    assert not sobran, f"clasificadas pero no existen en el DDL: {sobran}"


def test_toda_vista_analytics_esta_clasificada_y_es_segura(clasif):
    vistas = vistas_ddl()
    assert vistas
    for v, cuerpo in vistas.items():
        assert v in clasif.tablas, f"vista sin clasificar: {v}"
        for col in clasif.tablas[v]:
            assert re.search(rf"\b{re.escape(col)}\b", cuerpo), f"{v}.{col} no aparece en la definición"
        cuerpo_sin_comentarios = "\n".join(l.split("--")[0] for l in cuerpo.splitlines())
        for pii in ("_cifrado", "_cifrada", "_hmac", "payload"):
            assert pii not in cuerpo_sin_comentarios, f"{v} referencia una columna sensible ({pii})"
    clasif.verificar_vistas_analista()  # no lanza


def test_pii_fisica_siempre_cifrada_en_bytea(clasif):
    ddl = columnas_ddl()
    for t, cols in clasif.tablas.items():
        for c, nivel in cols.items():
            if nivel == "Restringido-PII":
                assert ddl[t][c] == "bytea", f"{t}.{c} es Restringido-PII pero no es bytea"
                assert re.search(r"_cifrad[oa]$", c), f"{t}.{c}: convención *_cifrado"


def test_campos_origen_con_pii_de_clientes(clasif):
    cifrar = clasif.campos_origen_por_politica("clientes.csv", cifrado_en_reposo=True)
    assert set(cifrar) == {"nit", "email_contacto", "celular_contacto", "direccion",
                           "representante_legal", "observaciones"}
    assert "observaciones" in clasif.campos_prohibidos_en_logs()
    assert "frontera_id" not in clasif.campos_prohibidos_en_logs()


def test_columna_desconocida_lanza(clasif):
    with pytest.raises(ErrorClasificacion):
        clasif.nivel("core.dim_cliente", "columna_inventada")
