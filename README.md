# Pipeline de consumos de energía — Bia Energy

Solución a la prueba técnica *Data Platform & Security Engineer* (el enunciado
original está en `docs/ENUNCIADO.md`). Pipeline batch en Python + PostgreSQL
que ingiere consumos horarios sucios en cuatro formatos, aplica las reglas de
calidad B1–B7, protege la PII (cifrado en reposo, seudónimos HMAC,
enmascarado, mínimo privilegio, bitácora append-only) y un consumidor de
streaming sobre Redpanda que emite alertas.

## Cómo correrlo

Requisitos: Docker con Compose v2, GNU make, Python 3.10 o superior, `curl`.

```bash
make setup     # venv, dependencias pineadas, .env con llaves aleatorias por máquina
make up        # Postgres 16 + Redpanda, espera healthchecks, aplica sql/ y verifica la clasificación
make run       # batch completo (~1 s); idempotente: correrlo dos veces deja el mismo estado
make stream    # crea el tópico, publica con tools/producer.py, consume y termina solo (~2-5 s)
make test      # 85 pruebas; las de base de datos se saltan si no hay Postgres
make verificar # entrega.yaml describe lo que levantó make up
make down
```

Otros targets: `make audit` (pip-audit), `make clean` (borra `out/`, `logs/` y
volúmenes; conserva `.env` porque contiene las llaves).

**Salidas**: `out/consumo_mensual.csv`, `out/dq_report.json`,
`out/metrics.json` (con una sección `stream` tras `make stream`),
`out/alertas.json` y `logs/pipeline.log` (JSON, una línea por evento, sin PII).

**Resultados con los datos del paquete** (sirven como prueba de regresión):

| `dq_report.json` | Valor | | Otros | Valor |
|---|---:|---|---|---:|
| filas_leidas | 115.340 | | filas cargadas | 114.545 |
| duplicados_exactos | 180 | | filas en `consumo_mensual.csv` | 160 |
| duplicados_conflictivos | 120 | | facturas cargadas | 160 |
| registros_negativos / nulos / outlier | 90 / 60 / 45 | | `CONSUMO_FUERA_DE_BANDA` | 22 |
| registros_mwh_convertidos | 400 | | `FRONTERA_SIN_REPORTE` | 6 |
| correcciones_tardias_aplicadas | 300 | | `LECTURA_RETROACTIVA` | 5 |
| horas_sin_lectura_valida_total | 655 | | | |
| fronteras_huerfanas_facturacion | 12 | | | |
| clientes_con_pii_en_texto_libre | 25 | | | |

Validación externa: el `consumo_facturado_kwh` de las 160 facturas coincide
con `consumo_mensual.csv` (diferencia máxima 0,005 kWh, por redondeo).

## Qué hice

```
data/ (inmutable) ──► src/bia/pipeline.py ──► PostgreSQL ──► analytics.v_* (rol_analista)
   clientes.csv (PII,     maestros → consumos       core.*        (cifrado AES-GCM + HMAC)
   entradas hostiles)     → facturación → reportes  quarantine.*  (rechazos con motivo)
   consumos/*.csv (4 fmt)  como rol_ingesta          audit.bitacora (append-only, rol_auditor)
   facturacion/*.json                            └─► out/ y logs/ (sin PII)
   eventos/*.ndjson ──► tools/producer.py ──► Redpanda ──► src/bia/stream ──► out/alertas.json
```

- **Parte A · Ingesta e idempotencia.** El formato se detecta por archivo
  (separador, encoding, columnas, coma decimal, UTC, unidad), no por nombre.
  Cada archivo es una transacción; `core.control_archivos` registra su hash y
  no se recarga lo ya cargado. La corrección de abril se absorbe por upsert
  sobre la clave natural, sin recargar. Un mes nuevo se procesa por glob; un
  archivo truncado o binario va a cuarentena sin tocar los meses cargados.
  ADR-005.
- **Parte B · Calidad.** B1–B7 exactas en `src/bia/ingesta/consumos.py`,
  `facturacion.py` y `maestros.py`. Las 300 lecturas que la corrección
  reemplaza cuentan como correcciones, no como conflictivas (ADR-005).
- **Parte C · Seguridad.** `data_classification.yaml` gobierna qué se cifra,
  qué se loguea y qué ve el analista, y `make up` falla si el modelo y el YAML
  no coinciden. Cifrado AES-256-GCM a nivel de aplicación con llaves fuera de
  la base, también para la PII duplicada en facturación. Tres roles con grants
  por columna. Bitácora append-only por privilegios y triggers. Secretos
  heredados eliminados y dependencias con CVE sustituidas. Detalle y comandos
  para verificar cada control: `SECURITY.md`. Amenazas: `THREAT_MODEL.md`.
- **Parte D · Streaming.** Detector puro con estado por frontera y consumidor
  que lee hasta el offset final y termina solo; deduplica por `evento_id`.
  ADR-006.
- **Parte E · Observabilidad.** Logger JSON con doble filtro de PII,
  `metrics.json` con duración por etapa y rechazos por motivo, y un test que
  busca la PII real del maestro en `logs/` y `out/`. Operación: `RUNBOOK.md`.
- **Parte F · IaC.** `infra/` con S3, KMS e IAM equivalentes a los roles
  locales; pasa `terraform validate`. No se despliega.

## Qué no hice o tiene límites

Declarado también en `entrega.yaml → no_implementado` y en los documentos
citados.

- **Docker no estaba disponible en la máquina de desarrollo.** Probé con un
  PostgreSQL 16 sin root (`scripts/dev_pg_sin_docker.sh`) y con Apache Kafka
  4.3.2 en lugar de Redpanda. `setup`, `run`, `stream`, `test` y `verificar`
  se corrieron contra esos sustitutos; `make up` y `make down` dependen de
  `docker-compose.yml`, que no se ejecutó aquí. Hay que correr el contrato
  completo en una máquina con Docker antes de entregar.
- **`fact_facturacion.nit_coincide_maestro` existe pero no se calcula**: la
  estructura para cruzar por HMAC está, la lógica no.
- **Límites de seguridad aceptados**: un superusuario de PostgreSQL puede
  desactivar los triggers de la bitácora; la llave de cifrado vive en `.env`
  en el mismo host; Redpanda sin autenticación ni TLS en local. Razones y
  mitigaciones de producción en `THREAT_MODEL.md` §4 y §5.
- **El lector de maestros** (`src/bia/ingesta/maestros.py`) todavía corta el
  archivo ante un error de `csv` en Python < 3.11 en lugar de seguir fila a
  fila como ya hace el de consumos. Con los datos entregados no se ejerce.
- **`infra/`** no modela red, cómputo, RDS, MSK ni CloudTrail (`infra/README.md`).

## Estructura

| Ruta | Contenido |
|---|---|
| `src/bia/` | Pipeline: `pipeline.py` (orquestador), `ingesta/`, `stream/`, `crypto.py`, `pii.py`, `classification.py`, `logging_json.py` |
| `sql/` | DDL numerado e idempotente: roles, esquemas, modelo, cuarentena, bitácora, vistas, grants |
| `tests/` | Parsers, reglas B y de streaming, PII y cifrado, permisos por rol, eliminatorias con datos reales |
| `scripts/` | `bootstrap_env.py` (genera `.env`), `bootstrap_db.py` (aplica `sql/`), `dev_pg_sin_docker.sh` |
| `infra/` | Terraform de referencia |
| `tools/` | `producer.py` y `verificar_entrega.py`, provistos por el enunciado, sin cambios |
| `docs/` | `ENUNCIADO.md`, `PLAN.md` (plan y registro de avance), `evidencias/` (pip-audit) |

Documentos: `DECISIONS.md` (6 ADRs), `SECURITY.md`, `THREAT_MODEL.md`,
`RUNBOOK.md`, `AI_USAGE.md`, `data_classification.yaml`.

## Desarrollo sin Docker

```bash
scripts/dev_pg_sin_docker.sh start                    # Postgres 16 sin root en 127.0.0.1:8432
PG_PORT=8432 .venv/bin/python scripts/bootstrap_db.py
PG_PORT=8432 make run && PG_PORT=8432 make test
```
