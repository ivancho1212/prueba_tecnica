"""`make stream`: crea el tópico, publica con tools/producer.py, consume y
escribe out/alertas.json. Termina solo.

Condición de fin explícita: se lee desde el principio de cada partición
(asignación manual, sin grupo de consumo ni offsets confirmados) hasta el
offset final que tenía el tópico cuando el productor terminó. Dos redes de
seguridad: inactividad (sin mensajes nuevos durante INACTIVIDAD_S con el
productor ya terminado) y un plazo global, ambos muy por debajo de los 300 s
del contrato. Leer desde el principio + deduplicar por evento_id hace que
correrlo dos veces deje el mismo out/alertas.json.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from bia import config, db
from bia.classification import Clasificacion
from bia.crypto import Cifrador
from bia.ingesta import archivos, maestros
from bia.ingesta.carga import Cargador
from bia.logging_json import configurar, log
from bia.stream.alertas import Detector

RAIZ = Path(__file__).resolve().parents[3]
PARTICIONES = 3
INACTIVIDAD_S = 20
PLAZO_TOTAL_S = 240


def cargar_limites(dir_maestros: Path) -> dict[str, Decimal]:
    """max_kwh por frontera desde limites_frontera.csv, validado con el mismo
    parser del batch contra las fronteras de fronteras.csv (sin PII)."""
    fr = archivos.descubrir(dir_maestros, "fronteras.csv")[0]
    texto, _ = archivos.decodificar(fr.contenido)
    validas = {(r.get("frontera_id") or "").strip() for r in csv.DictReader(io.StringIO(texto, newline=""))}
    res = maestros.procesar_limites(archivos.descubrir(dir_maestros, "limites_frontera.csv")[0], validas)
    return {f["frontera_id"]: f["max_kwh"] for f in res.filas}


def _asegurar_topico(bootstrap: str, topico: str, logger) -> None:
    from kafka import KafkaAdminClient
    from kafka.admin import NewTopic
    from kafka.errors import TopicAlreadyExistsError
    admin = KafkaAdminClient(bootstrap_servers=bootstrap, client_id="bia-stream-admin")
    try:
        if topico in admin.list_topics():
            log(logger, "topico_existente", topico=topico)
            return
        admin.create_topics([NewTopic(topico, num_partitions=PARTICIONES, replication_factor=1)])
        log(logger, "topico_creado", topico=topico, particiones=PARTICIONES)
    except TopicAlreadyExistsError:
        log(logger, "topico_existente", topico=topico)
    finally:
        admin.close()


def _escribir_json(ruta: Path, datos) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, ruta)


def _registrar_metricas(ruta: Path, stream: dict) -> None:
    """Añade la sección `stream` a out/metrics.json sin pisar la del batch."""
    try:
        actual = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}
    except ValueError:
        actual = {}
    actual["stream"] = stream
    _escribir_json(ruta, actual)


def consumir(cfg, detector: Detector, logger) -> dict:
    from kafka import KafkaConsumer, TopicPartition
    t0 = time.monotonic()
    _asegurar_topico(cfg.kafka_bootstrap, cfg.kafka_topic, logger)
    consumidor = KafkaConsumer(bootstrap_servers=cfg.kafka_bootstrap, client_id="bia-stream",
                               group_id=None, enable_auto_commit=False)
    particiones = None
    for _ in range(30):
        particiones = consumidor.partitions_for_topic(cfg.kafka_topic)
        if particiones:
            break
        time.sleep(1)
    if not particiones:
        raise RuntimeError(f"el tópico {cfg.kafka_topic} no expone particiones")
    tps = [TopicPartition(cfg.kafka_topic, p) for p in sorted(particiones)]
    consumidor.assign(tps)
    consumidor.seek_to_beginning(*tps)

    productor = subprocess.Popen(
        [sys.executable, str(RAIZ / "tools" / "producer.py"), "--topic", cfg.kafka_topic,
         "--bootstrap", cfg.kafka_bootstrap, "--rate", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    log(logger, "productor_lanzado", topico=cfg.kafka_topic)

    mensajes = malformados = 0
    fin: dict | None = None
    ultimo_mensaje = time.monotonic()
    motivo_fin = "offset_final"
    while True:
        lote = consumidor.poll(timeout_ms=500)
        for registros in lote.values():
            for r in registros:
                mensajes += 1
                try:
                    evento = json.loads(r.value.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    malformados += 1
                    detector.rechazos["EVENTO_MALFORMADO"] += 1
                    log(logger, "evento_malformado", logging.WARNING, particion=r.partition, offset=r.offset)
                    continue
                detector.procesar(evento)
        if lote:
            ultimo_mensaje = time.monotonic()
        if fin is None and productor.poll() is not None:
            salida, error = productor.communicate()
            if productor.returncode != 0:
                log(logger, "productor_fallo", logging.ERROR, codigo=productor.returncode,
                    detalle=(error or "").strip().splitlines()[-1:] )
                raise RuntimeError(f"tools/producer.py terminó con código {productor.returncode}")
            fin = consumidor.end_offsets(tps)
            log(logger, "productor_terminado", salida=(salida or "").strip(),
                offsets_finales={str(tp.partition): o for tp, o in fin.items()})
        if fin is not None and all(consumidor.position(tp) >= fin[tp] for tp in tps):
            break
        if fin is not None and time.monotonic() - ultimo_mensaje > INACTIVIDAD_S:
            motivo_fin = "inactividad"
            log(logger, "fin_por_inactividad", logging.WARNING, segundos=INACTIVIDAD_S)
            break
        if time.monotonic() - t0 > PLAZO_TOTAL_S:
            motivo_fin = "plazo_total"
            log(logger, "fin_por_plazo", logging.ERROR, segundos=PLAZO_TOTAL_S)
            if productor.poll() is None:
                productor.kill()
            break
    consumidor.close()
    return {"mensajes_leidos": mensajes, "mensajes_malformados": malformados, "motivo_fin": motivo_fin,
            "duracion_ms_consumo": int((time.monotonic() - t0) * 1000)}


def main() -> int:
    t0 = time.monotonic()
    inicio_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    corrida = uuid.uuid4()
    cfg = config.cargar()
    clasif = Clasificacion.cargar()
    logger = configurar(cfg.log_dir / "pipeline.log", str(corrida), "stream",
                        clasif.campos_prohibidos_en_logs(), cfg.log_level)
    log(logger, "inicio", topico=cfg.kafka_topic)

    limites = cargar_limites(cfg.data_dir / "maestros")
    detector = Detector(limites)
    codigo = 0
    try:
        resumen = consumir(cfg, detector, logger)
    except Exception as e:  # noqa: BLE001
        log(logger, "error", logging.ERROR, error=f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
        resumen, codigo = {"motivo_fin": "error"}, 1
    detector.cerrar()
    alertas = detector.resultado()
    if codigo == 0:
        _escribir_json(cfg.out_dir / "alertas.json", alertas)

    por_tipo: dict[str, int] = {}
    for a in alertas:
        por_tipo[a["tipo"]] = por_tipo.get(a["tipo"], 0) + 1
    metricas = {"corrida_id": str(corrida), "inicio": inicio_iso, **resumen,
                "eventos_procesados": detector.procesados, "eventos_duplicados": detector.duplicados,
                "eventos_fuera_de_rejilla": detector.fuera_de_rejilla,
                "eventos_rechazados_por_motivo": dict(detector.rechazos),
                "alertas_por_tipo": por_tipo, "alertas_total": len(alertas),
                "duracion_ms_total": int((time.monotonic() - t0) * 1000)}
    _registrar_metricas(cfg.out_dir / "metrics.json", metricas)
    log(logger, "fin", nivel_resultado="OK" if codigo == 0 else "ERROR", **{k: v for k, v in metricas.items()
                                                                          if k not in ("corrida_id", "inicio")})
    _bitacora(cfg, corrida, metricas, codigo)
    return codigo


def _bitacora(cfg, corrida, metricas: dict, codigo: int) -> None:
    """Deja constancia de la corrida en audit.bitacora (C5). Si la base no
    está disponible, el streaming no falla por eso: queda en el log."""
    logger = logging.getLogger("bia")
    try:
        conn = db.conectar(cfg.dsn_rol("ingesta", "bia_stream"), intentos=2)
    except Exception as e:  # noqa: BLE001
        log(logger, "bitacora_no_disponible", logging.WARNING, error=type(e).__name__)
        return
    try:
        Cargador(conn, corrida, Cifrador(cfg.enc_key, cfg.hmac_key), componente="stream").bitacora(
            "stream", "fin", "OK" if codigo == 0 else "ERROR", cfg.kafka_topic,
            metricas.get("eventos_procesados"), metricas.get("duracion_ms_total"),
            alertas_por_tipo=metricas.get("alertas_por_tipo"), motivo_fin=metricas.get("motivo_fin"),
            rechazados=metricas.get("eventos_rechazados_por_motivo"))
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
