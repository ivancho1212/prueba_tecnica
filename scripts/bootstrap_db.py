#!/usr/bin/env python3
"""Aplica sql/*.sql en orden como superusuario. Idempotente: `make up` lo corre
siempre. Las contraseñas de los roles entran por set_config, nunca por texto.
Al final verifica que data_classification.yaml cubre 1:1 el modelo y deja un
evento en audit.bitacora.
"""
from __future__ import annotations

import getpass
import sys
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from bia import config, db  # noqa: E402
from bia.classification import Clasificacion  # noqa: E402

SQL_DIR = RAIZ / "sql"


def main() -> int:
    t0 = time.monotonic()
    cfg = config.cargar()
    corrida = uuid.uuid4()
    conn = db.conectar(cfg.dsn_superusuario(), intentos=45, espera_s=2.0)
    archivos = sorted(SQL_DIR.glob("*.sql"))
    if not archivos:
        print("no hay archivos en sql/", file=sys.stderr)
        return 1
    try:
        for ruta in archivos:
            with db.transaccion(conn) as cur:
                if ruta.name.startswith("00_"):
                    cur.execute(
                        "SELECT set_config('bia.pwd_ingesta', %s, false),"
                        "       set_config('bia.pwd_analista', %s, false),"
                        "       set_config('bia.pwd_auditor', %s, false)",
                        (cfg.roles["ingesta"][1], cfg.roles["analista"][1], cfg.roles["auditor"][1]),
                    )
                cur.execute(ruta.read_text(encoding="utf-8"))
            print(f"  [ok] {ruta.name}")

        clasif = Clasificacion.cargar()
        clasif.verificar_vistas_analista()
        resumen = clasif.verificar_contra_db(conn)
        print(f"  [ok] data_classification.yaml cubre {resumen['columnas']} columnas "
              f"en {resumen['tablas']} objetos")

        with db.transaccion(conn) as cur:
            cur.execute(
                """
                INSERT INTO audit.bitacora (corrida_id, usuario_os, componente, etapa, evento,
                                            objeto, resultado, duracion_ms, detalle)
                VALUES (%s, %s, 'bootstrap', 'ddl', 'fin', 'sql/*.sql', 'OK', %s,
                        jsonb_build_object('archivos', %s, 'columnas_clasificadas', %s))
                """,
                (corrida, getpass.getuser(), int((time.monotonic() - t0) * 1000),
                 [a.name for a in archivos], resumen["columnas"]),
            )
    finally:
        conn.close()
    print(">> base de datos lista")
    return 0


if __name__ == "__main__":
    sys.exit(main())
