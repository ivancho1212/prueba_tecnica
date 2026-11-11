"""Parte D: reglas de alerta del streaming, sin broker (el detector es puro)."""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from bia.stream.alertas import BOGOTA, INICIO_DIA, PASO, Detector

RAIZ = Path(__file__).resolve().parents[1]
F, G = "CO1111111111", "CO2222222222"


def ev(eid, frontera, k, kwh=1.0, ts=None):
    t = ts or (INICIO_DIA + PASO * k).isoformat()
    return {"evento_id": eid, "frontera_id": frontera, "ts_medicion": t, "kwh": kwh,
            "medidor": {"serial": "X", "firmware": "1"}}


def det(*fronteras, maximo="100"):
    return Detector({f: Decimal(maximo) for f in fronteras or (F,)})


def ts(k):
    return (INICIO_DIA + PASO * k).isoformat()


def todos_menos(huecos, frontera=F, n=96):
    return [ev(f"{frontera}-{k}", frontera, k) for k in range(n) if k not in huecos]


# --------------------------------------------------- CONSUMO_FUERA_DE_BANDA --
def test_banda_es_estrictamente_mayor():
    d = det()
    d.procesar(ev("a", F, 0, kwh=100.0))
    d.procesar(ev("b", F, 1, kwh=100.001))
    assert d.resultado() == [{"tipo": "CONSUMO_FUERA_DE_BANDA", "frontera_id": F, "ts": ts(1)}]


# ----------------------------------------------------- FRONTERA_SIN_REPORTE --
@pytest.mark.parametrize("huecos, esperado", [
    ({10, 11}, []),                          # 2 intervalos: no es caída
    ({10, 11, 12}, [12]),                    # 3: alerta en el tercero
    (set(range(27, 36)), [29]),              # 9 seguidos: una sola alerta por caída
    ({0, 1, 2}, [2]),                        # al inicio del día
    ({93, 94, 95}, [95]),                    # abierta al final: se evalúa al cerrar
    ({5, 6, 7, 50, 51, 52, 53}, [7, 52]),    # dos caídas distintas
])
def test_sin_reporte(huecos, esperado):
    d = det()
    for e in todos_menos(huecos):
        d.procesar(e)
    d.cerrar()
    assert [a["ts"] for a in d.resultado() if a["tipo"] == "FRONTERA_SIN_REPORTE"] == [ts(k) for k in esperado]


def test_frontera_que_nunca_reporta_cae_al_tercer_intervalo():
    d = det(F, G)
    for e in todos_menos(set(), frontera=F):
        d.procesar(e)
    d.cerrar()
    assert d.resultado() == [{"tipo": "FRONTERA_SIN_REPORTE", "frontera_id": G, "ts": ts(2)}]


# ------------------------------------------------------ LECTURA_RETROACTIVA --
def test_retroactiva_usa_el_ts_de_la_medicion_tardia():
    d = det()
    for k in (0, 1, 2, 3):
        d.procesar(ev(f"e{k}", F, k))
    d.procesar(ev("tarde", F, 1, kwh=7.0))      # ts anterior al último procesado (k=3)
    d.procesar(ev("igual", F, 3, kwh=8.0))      # mismo ts que el último: no es anterior
    assert d.resultado() == [{"tipo": "LECTURA_RETROACTIVA", "frontera_id": F, "ts": ts(1)}]


def test_reentrega_del_mismo_evento_no_es_retroactiva():
    """Kafka entrega al menos una vez: releer el tópico no puede inventar alertas."""
    d = det()
    eventos = [ev(f"e{k}", F, k) for k in range(5)]
    for e in eventos + eventos:
        d.procesar(e)
    assert d.resultado() == [] and d.duplicados == 5 and d.procesados == 5


# --------------------------------------------------------------- robustez --
def test_eventos_invalidos_se_rechazan_sin_detener_ni_contar_como_medicion():
    d = det()
    for e in ["texto", {"frontera_id": F}, ev("x1", "XX", 0), ev("x2", "CO9999999999", 0),
              ev("x3", F, 0, kwh=None), ev("x4", F, 0, kwh=True), ev("x5", F, 0, kwh="nan"),
              ev("x6", F, 0, ts="ayer"), ev("ok", F, 0, kwh="150")]:
        d.procesar(e)
    assert d.procesados == 1 and sum(d.rechazos.values()) == 8
    assert d.resultado() == [{"tipo": "CONSUMO_FUERA_DE_BANDA", "frontera_id": F, "ts": ts(0)}]


def test_ts_en_utc_sale_con_offset_de_bogota():
    d = det()
    d.procesar(ev("a", F, 0, kwh=500, ts="2026-06-01T05:15:00Z"))
    assert d.resultado()[0]["ts"] == "2026-06-01T00:15:00-05:00"
    assert datetime.fromisoformat(d.resultado()[0]["ts"]).utcoffset() == timedelta(hours=-5)


# ---------------------------------------------------- datos reales del paquete --
def _real():
    ruta = RAIZ / "data" / "eventos" / "mediciones_stream.ndjson"
    if not ruta.exists():
        pytest.skip("sin data/eventos")
    from bia.stream.consumidor import cargar_limites
    limites = cargar_limites(RAIZ / "data" / "maestros")
    return [json.loads(l) for l in ruta.read_text(encoding="utf-8").splitlines() if l.strip()], limites


def _correr(eventos, limites):
    d = Detector(limites)
    for e in eventos:
        d.procesar(e)
    d.cerrar()
    return d.resultado()


def test_dataset_real_conteos_y_formato():
    eventos, limites = _real()
    assert len(limites) == 40
    alertas = _correr(eventos, limites)
    assert Counter(a["tipo"] for a in alertas) == {"CONSUMO_FUERA_DE_BANDA": 22, "FRONTERA_SIN_REPORTE": 6,
                                                   "LECTURA_RETROACTIVA": 5}
    assert len({(a["tipo"], a["frontera_id"], a["ts"]) for a in alertas}) == len(alertas), "sin duplicados"
    for a in alertas:
        assert set(a) == {"tipo", "frontera_id", "ts"}
        assert datetime.fromisoformat(a["ts"]).utcoffset() == timedelta(hours=-5)


def test_dataset_real_no_depende_del_intercalado_entre_particiones():
    """Solo el orden por frontera está garantizado: cualquier intercalado que lo
    respete (lo que entregan varias particiones) debe dar las mismas alertas."""
    eventos, limites = _real()
    base = _correr(eventos, limites)
    colas = defaultdict(list)
    for e in eventos:
        colas[e["frontera_id"]].append(e)
    rnd = random.Random(7)
    for _ in range(3):
        pendientes = {f: list(c) for f, c in colas.items()}
        mezcla = []
        while pendientes:
            f = rnd.choice(sorted(pendientes))
            mezcla.append(pendientes[f].pop(0))
            if not pendientes[f]:
                del pendientes[f]
        assert _correr(mezcla, limites) == base
