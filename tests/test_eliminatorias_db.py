"""Las dos eliminatorias que se comprueban contra la base, con los datos reales
del paquete: (3) la entrada hostil de clientes.csv es dato, no código, y la
tabla de consumos no cambia; (1) la bitácora que lee el auditor no contiene PII.
Se saltan sin Postgres o sin data/ (el evaluador tiene ambas)."""
from __future__ import annotations

import csv
import re
import uuid
from pathlib import Path

import pytest

from bia.classification import Clasificacion
from bia.crypto import Cifrador
from bia.ingesta import archivos, maestros
from bia.ingesta.carga import Cargador

RAIZ = Path(__file__).resolve().parents[1]
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
RE_CELULAR = re.compile(r"\b3\d{9}\b")
RE_CEDULA = re.compile(r"\bCC\s*\d{6,10}\b")


def _pii_real() -> dict[str, set[str]]:
    """Valores reales de PII del maestro (no se imprimen nunca)."""
    ruta = RAIZ / "data" / "maestros" / "clientes.csv"
    if not ruta.exists():
        pytest.skip("sin data/maestros/clientes.csv")
    with open(ruta, encoding="iso-8859-1", newline="") as f:
        filas = list(csv.DictReader(f))
    obs = " ".join(r.get("observaciones") or "" for r in filas)
    return {
        "emails": {(r["email_contacto"] or "").strip().lower() for r in filas if (r["email_contacto"] or "").strip()}
                  | {e.lower() for e in RE_EMAIL.findall(obs)},
        "celulares": {(r["celular_contacto"] or "").strip() for r in filas if (r["celular_contacto"] or "").strip()}
                     | set(RE_CELULAR.findall(obs)),
        "nits": {re.sub(r"\D", "", r["nit"] or "") for r in filas if len(re.sub(r"\D", "", r["nit"] or "")) >= 9},
        "cedulas": set(RE_CEDULA.findall(obs)),
    }


def _assert_sin_pii(texto: str, pii: dict[str, set[str]], donde: str) -> None:
    bajo = texto.lower()
    assert not any(e in bajo for e in pii["emails"]), f"correo real del maestro en {donde}"
    assert not any(c in texto for c in pii["celulares"]), f"celular real del maestro en {donde}"
    assert not any(n in texto for n in pii["nits"]), f"NIT real del maestro en {donde}"
    assert not any(c in texto for c in pii["cedulas"]), f"cédula real del maestro en {donde}"
    # y lo que haría el evaluador con regex genéricas
    assert not RE_EMAIL.search(texto), f"algo con forma de correo en {donde}"
    assert not RE_CELULAR.search(texto), f"algo con forma de celular en {donde}"
    assert not RE_CEDULA.search(texto), f"algo con forma de cédula en {donde}"


def test_eliminatoria_3_maestro_hostil_no_toca_consumos(cfg, conn_su, conn_ingesta):
    dir_m = RAIZ / "data" / "maestros"
    if not (dir_m / "clientes.csv").exists():
        pytest.skip("sin data/maestros/clientes.csv")
    with conn_su.cursor() as cur:
        cur.execute("SELECT count(*) FROM core.fact_consumo_horario")
        antes = cur.fetchone()[0]
    conn_su.rollback()

    # Mismo camino que `make run`: parseo como dato + upsert parametrizado como rol_ingesta.
    a = archivos.descubrir(dir_m, "clientes.csv")[0]
    cif = Cifrador(cfg.enc_key, cfg.hmac_key)
    res = maestros.procesar_clientes(a, cif, Clasificacion.cargar())
    assert res.filas_leidas == 40 and len(res.filas) == 40 and not res.rechazos, "ninguna fila se pierde (B7)"
    hostil = next(c for c in res.filas if c.cliente_id == "CLI0036")
    assert "DROP TABLE" in hostil.razon_social, "la inyección viaja como texto, no se ejecuta"
    larga = next(c for c in res.filas if c.cliente_id == "CLI0033")
    assert len(larga.razon_social) > 1000, "la razón social larga no se trunca"

    cargador = Cargador(conn_ingesta, uuid.uuid4(), cif)
    estado = cargador.registrar_archivo(a, "clientes", None)
    cargador.upsert_clientes(res.filas, estado.archivo_id)
    conn_ingesta.commit()

    with conn_su.cursor() as cur:
        cur.execute("SELECT to_regclass('core.fact_consumo_horario') IS NOT NULL")
        assert cur.fetchone()[0], "la tabla de consumos desapareció"
        cur.execute("SELECT count(*) FROM core.fact_consumo_horario")
        assert cur.fetchone()[0] == antes, "la tabla de consumos cambió al cargar el maestro"
        cur.execute("SELECT cliente_id, razon_social FROM core.dim_cliente WHERE cliente_id IN ('CLI0036', 'CLI0033') ORDER BY 1")
        filas = dict(cur.fetchall())
    conn_su.rollback()
    assert set(filas) == {"CLI0033", "CLI0036"}
    assert "DROP TABLE" in filas["CLI0036"] and len(filas["CLI0033"]) > 1000


def test_eliminatoria_1_bitacora_del_auditor_sin_pii(conn_auditor):
    pii = _pii_real()
    with conn_auditor.cursor() as cur:
        cur.execute("""SELECT usuario_db, usuario_os, host_origen, componente, etapa, evento, objeto,
                              resultado, detalle::text
                       FROM audit.bitacora""")
        filas = cur.fetchall()
    conn_auditor.rollback()
    if not filas:
        pytest.skip("bitácora vacía: correr make run antes")
    texto = "\n".join(" ".join(str(c) for c in f if c is not None) for f in filas)
    _assert_sin_pii(texto, pii, "audit.bitacora")
