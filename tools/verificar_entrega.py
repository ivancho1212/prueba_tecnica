#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verificador de entrega. Ya esta resuelto: no tenes que tocarlo.

Comprueba UNA sola cosa, y es la que mas puntos hace perder por accidente:
que tu `entrega.yaml` describa de verdad lo que levanta tu `make up`. El
evaluador se conecta con esos datos para calificar los controles de
seguridad; si un nombre de rol, un puerto o un nombre de objeto no coinciden,
esos checks no se pueden correr y quedan en cero.

NO te dice si tus controles estan bien ni cuantos puntos llevas. Solo:
me conecte con los roles que declaraste y encontre los objetos que nombraste.

    make verificar          (o: python3 tools/verificar_entrega.py)

Corrrelo con la infraestructura arriba (`make up`) y antes de comprimir.
Sale con 0 si todo cuadra, con 1 si algo no.
"""
from __future__ import annotations

import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_MANIFIESTO = os.path.join(RAIZ, "entrega.yaml")

OBJETOS = {
    "tabla_consumos": "tabla de consumos",
    "tabla_clientes": "tabla de clientes (la que tiene PII)",
    "vista_analytics": "vista analitica",
    "tabla_auditoria": "bitacora de auditoria",
}
ARTEFACTOS = ["out/consumo_mensual.csv", "out/dq_report.json",
              "out/metrics.json", "out/alertas.json", "logs/pipeline.log"]

OK, FALLO, AVISO = "  [ok]   ", "  [FALLA]", "  [aviso]"


def _salir(mensaje):
    print(f"\n{mensaje}\n", file=sys.stderr)
    return 2


def main():
    try:
        import yaml
    except ImportError:
        return _salir("Falta PyYAML. Corré `make setup` primero.")
    try:
        import psycopg2
    except ImportError:
        return _salir("Falta psycopg2. Corré `make setup` primero.")

    if not os.path.exists(RUTA_MANIFIESTO):
        return _salir(f"No existe {RUTA_MANIFIESTO}. Es obligatorio.")
    try:
        with open(RUTA_MANIFIESTO, encoding="utf-8") as fh:
            man = yaml.safe_load(fh)
    except Exception as e:  # noqa: BLE001
        return _salir(f"entrega.yaml no parsea: {e}")
    if not isinstance(man, dict):
        return _salir("entrega.yaml no es un mapeo YAML.")

    problemas = []
    print("\n== entrega.yaml ==")

    cand = man.get("candidato") or {}
    for campo in ("nombre_completo", "email"):
        if not cand.get(campo) or cand[campo] == "TODO":
            problemas.append(f"candidato.{campo} sin llenar")
            print(f"{FALLO} candidato.{campo}: sin llenar")
        else:
            print(f"{OK} candidato.{campo}")

    pg = man.get("postgres") or {}
    roles = pg.get("roles") or {}
    objetos = man.get("objetos") or {}
    su = pg.get("superusuario") or {}

    def declarado(d, campo="usuario"):
        v = (d or {}).get(campo)
        return bool(v) and v != "TODO"

    print("\n== conexiones ==")

    def conectar(etiqueta, usuario, password):
        try:
            conn = psycopg2.connect(
                host=pg.get("host", "localhost"),
                port=int(pg.get("port", 5432)),
                dbname=pg.get("database", "bia_datos"),
                user=usuario, password=password, connect_timeout=10)
            print(f"{OK} {etiqueta} ({usuario})")
            return conn
        except Exception as e:  # noqa: BLE001
            detalle = str(e).strip().splitlines()[0] if str(e).strip() else "?"
            problemas.append(f"no se pudo conectar como {etiqueta}: {detalle}")
            print(f"{FALLO} {etiqueta} ({usuario}): {detalle}")
            return None

    conn_su = None
    if declarado(su):
        conn_su = conectar("superusuario", su["usuario"], su.get("password"))
    else:
        problemas.append("postgres.superusuario sin declarar: sin eso el "
                         "cifrado en reposo no se puede verificar")
        print(f"{FALLO} superusuario: sin declarar "
              "(sin eso no se puede verificar el cifrado en reposo)")

    for nombre in ("ingesta", "analista", "auditor"):
        r = roles.get(nombre)
        if not declarado(r):
            problemas.append(f"postgres.roles.{nombre} sin declarar")
            print(f"{FALLO} rol {nombre}: sin declarar")
            continue
        c = conectar(f"rol {nombre}", r["usuario"], r.get("password"))
        if c:
            c.close()

    print("\n== objetos declarados ==")
    if conn_su is None:
        print(f"{AVISO} sin conexion de superusuario: no se pueden buscar")
    else:
        with conn_su.cursor() as cur:
            for clave, descripcion in OBJETOS.items():
                nombre = objetos.get(clave)
                if not nombre or nombre == "TODO":
                    problemas.append(f"objetos.{clave} sin declarar")
                    print(f"{FALLO} {clave}: sin declarar ({descripcion})")
                    continue
                cur.execute("SELECT to_regclass(%s)", (nombre,))
                existe = cur.fetchone()[0] is not None
                if existe:
                    cur.execute(f"SELECT count(*) FROM {nombre}")
                    print(f"{OK} {clave}: {nombre} "
                          f"({cur.fetchone()[0]} filas)")
                else:
                    problemas.append(
                        f"objetos.{clave} apunta a '{nombre}', que no existe. "
                        "Escribilo con esquema, p.ej. core.fact_consumo_horario")
                    print(f"{FALLO} {clave}: '{nombre}' no existe")
        conn_su.close()

    print("\n== artefactos de salida ==")
    for rel in ARTEFACTOS:
        ruta = os.path.join(RAIZ, rel)
        if os.path.exists(ruta) and os.path.getsize(ruta) > 0:
            print(f"{OK} {rel}")
        else:
            # alertas.json lo produce `make stream`, no `make run`
            print(f"{AVISO} {rel}: no esta o esta vacio")

    print()
    if problemas:
        print(f"{len(problemas)} cosa(s) por arreglar antes de entregar:")
        for p in problemas:
            print(f"  - {p}")
        print("\nEl evaluador no las puede adivinar: lo que no se puede "
              "conectar o encontrar se califica en cero.\n")
        return 1

    print("Manifiesto consistente con lo que levantaste. "
          "El evaluador va a poder conectarse.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
