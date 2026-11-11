#!/usr/bin/env python3
"""
Productor de eventos al topico de Kafka/Redpanda.

Te lo damos resuelto para que te concentres en el CONSUMIDOR, que es lo que
se evalua. Reproduce prueba-tecnica/data/eventos/mediciones_stream.ndjson
sobre el topico configurado, en el mismo orden del archivo.

Uso:
    python3 tools/producer.py --topic mediciones.raw --bootstrap localhost:9092
    python3 tools/producer.py --rate 500      # eventos por segundo
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    from kafka import KafkaProducer
except ImportError:
    print("Falta la libreria kafka-python. Agregala a tu requirements.txt.",
          file=sys.stderr)
    raise

RUTA_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "eventos", "mediciones_stream.ndjson",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archivo", default=RUTA_DEFAULT)
    ap.add_argument("--topic", default=os.getenv("KAFKA_TOPIC", "mediciones.raw"))
    ap.add_argument("--bootstrap",
                    default=os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"))
    ap.add_argument("--rate", type=int, default=1000,
                    help="eventos por segundo (0 = sin limite)")
    args = ap.parse_args()

    productor = KafkaProducer(
        bootstrap_servers=args.bootstrap,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )

    n = 0
    intervalo = 1.0 / args.rate if args.rate > 0 else 0
    with open(args.archivo, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea:
                continue
            evento = json.loads(linea)
            # particionado por frontera: garantiza orden por frontera
            productor.send(args.topic, key=evento["frontera_id"], value=evento)
            n += 1
            if intervalo:
                time.sleep(intervalo)

    productor.flush()
    print(f"{n} eventos publicados en {args.topic}")


if __name__ == "__main__":
    main()
