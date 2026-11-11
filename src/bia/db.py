"""Conexiones a Postgres. psycopg2, siempre con parámetros; nunca SQL interpolado."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extras

psycopg2.extras.register_uuid()


def conectar(dsn: str, intentos: int = 1, espera_s: float = 2.0):
    ultimo: Exception | None = None
    for _ in range(max(1, intentos)):
        try:
            conn = psycopg2.connect(dsn)
            conn.autocommit = False
            return conn
        except psycopg2.OperationalError as e:  # base aún arrancando
            ultimo = e
            time.sleep(espera_s)
    raise RuntimeError(f"no se pudo conectar a Postgres tras {intentos} intentos: "
                       f"{' | '.join(l.strip() for l in str(ultimo).strip().splitlines() if 'failed' in l or 'FATAL' in l) or (str(ultimo).strip().splitlines()[0] if ultimo else '?')}")


@contextmanager
def transaccion(conn) -> Iterator[psycopg2.extensions.cursor]:
    """Cursor dentro de una transacción: commit al salir, rollback ante error."""
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
