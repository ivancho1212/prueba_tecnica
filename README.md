# Pipeline de consumos de energía — Bia Energy

Solución a la prueba técnica *Data Platform & Security Engineer*. El enunciado original está en `docs/ENUNCIADO.md`.

Pipeline de ingeniería de datos en Python + PostgreSQL que ingiere consumos horarios con datos intencionalmente sucios en cuatro formatos, aplica las reglas de calidad B1–B7, protege la PII y procesa un flujo de mediciones sobre Redpanda para generar alertas.

## Cómo correrlo

### Requisitos

- Docker con Compose v2
- GNU Make
- Python 3.10 o superior
- `curl`

### Entorno Windows

El `Makefile` está diseñado para un entorno Unix/Linux porque utiliza Bash y la estructura `.venv/bin/python`.

En Windows se puede ejecutar mediante **WSL2 + Docker Desktop**, con la integración de Docker Desktop con WSL2 habilitada.

No es necesario modificar el proyecto ni el `Makefile`.

### Contrato de ejecución

```bash
make setup
make up
make run
make run
make stream
make test
make down
```

Descripción de los principales targets:

```text
make setup      Crea el entorno virtual, instala dependencias y genera .env con llaves aleatorias por máquina.
make up         Levanta PostgreSQL 16 + Redpanda, espera healthchecks, aplica SQL y verifica la clasificación.
make run        Ejecuta el pipeline batch completo. Es idempotente.
make stream     Publica y consume las mediciones de Redpanda hasta el offset final y genera alertas.
make test       Ejecuta la suite de pruebas.
make verificar  Comprueba entrega.yaml, conexiones, objetos y artefactos de salida.
make down       Detiene y elimina los contenedores y la red del proyecto.
make audit      Ejecuta pip-audit.
make clean      Limpia salidas, logs y volúmenes conservando .env.
```

La validación final del contrato se realizó en Windows mediante WSL2 + Docker Desktop:

```text
setup → up → run → run → stream → test → down
```

Después se volvió a levantar la infraestructura y `make verificar` confirmó que el evaluador puede conectarse y encontrar los objetos y artefactos declarados.

## Salidas

El pipeline genera:

- `out/consumo_mensual.csv`
- `out/dq_report.json`
- `out/metrics.json`
- `out/alertas.json`
- `logs/pipeline.log`

Los logs se escriben en JSON, una línea por evento, y los controles de PII evitan que los valores reales del maestro aparezcan en `logs/` u `out/`.

## Resultados con los datos del paquete

Estos valores sirven como referencia de regresión:

| `dq_report.json` | Valor | Otros | Valor |
|---|---:|---|---:|
| filas_leidas | 115.340 | filas cargadas | 114.545 |
| duplicados_exactos | 180 | filas en `consumo_mensual.csv` | 160 |
| duplicados_conflictivos | 120 | facturas cargadas | 160 |
| registros_negativos / nulos / outlier | 90 / 60 / 45 | `CONSUMO_FUERA_DE_BANDA` | 22 |
| registros_mwh_convertidos | 400 | `FRONTERA_SIN_REPORTE` | 6 |
| correcciones_tardias_aplicadas | 300 | `LECTURA_RETROACTIVA` | 5 |
| horas_sin_lectura_valida_total | 655 | alertas totales de streaming | 33 |
| fronteras_huerfanas_facturacion | 12 |  | |
| clientes_con_pii_en_texto_libre | 25 |  | |

La ejecución de streaming procesó:

- 3.795 eventos publicados.
- 3.795 eventos consumidos.
- 0 eventos malformados.
- 0 duplicados.
- 0 eventos fuera de la grilla.
- 3.795 eventos procesados.
- 33 alertas generadas.

La segunda ejecución de `make run` mantuvo los mismos resultados y estado final, verificando la idempotencia del procesamiento.

El `consumo_facturado_kwh` de las 160 facturas coincide con `consumo_mensual.csv`, con una diferencia máxima de 0,005 kWh atribuible al redondeo.

## Qué hice

```text
data/ (inmutable) ──► src/bia/pipeline.py ──► PostgreSQL ──► analytics.v_*
   clientes.csv (PII,     maestros → consumos       core.*        (rol_analista)
   entradas hostiles)     → facturación → reportes  quarantine.*
   consumos/*.csv (4 fmt)  como rol_ingesta          audit.bitacora
   facturacion/*.json                              └─► out/ y logs/
   eventos/*.ndjson ──► tools/producer.py ──► Redpanda ──► src/bia/stream
                                                               └─► alertas
```

### Parte A · Ingesta e idempotencia

- Detección del formato por archivo: separador, encoding, columnas, decimal, zona horaria y unidad.
- Cada archivo se procesa dentro de una transacción.
- `core.control_archivos` registra hash y estado de procesamiento.
- Los archivos ya cargados no se vuelven a ingerir.
- Las correcciones de abril reemplazan las lecturas anteriores mediante la clave natural y la versión.
- Las 300 correcciones tardías se distinguen de los 120 duplicados conflictivos de misma versión.
- Los errores estructurales rechazan el archivo cuando superan el umbral definido; los errores de fila se conservan en cuarentena.
- Los datos inválidos no destruyen los datos previamente cargados.

### Parte B · Calidad

Las reglas B1–B7 están implementadas en:

- `src/bia/ingesta/consumos.py`
- `src/bia/ingesta/facturacion.py`
- `src/bia/ingesta/maestros.py`

Se implementan, entre otros:

- normalización de formatos;
- conversión MWh → kWh;
- detección de negativos, nulos y outliers;
- deduplicación;
- aplicación de correcciones tardías;
- detección de horas sin lectura válida;
- identificación de fronteras huérfanas en facturación;
- cuarentena con motivo y trazabilidad.

### Parte C · Seguridad

`data_classification.yaml` gobierna la clasificación de los campos y se utiliza durante la ejecución del pipeline.

El arranque valida que la clasificación cubra las columnas esperadas.

La protección de PII incluye:

- AES-256-GCM para datos cifrados.
- HMAC determinístico para búsquedas y relaciones que necesitan igualdad.
- Enmascaramiento de PII encontrada en texto libre.
- Tres roles con privilegios diferenciados.
- Vistas analíticas sin exposición innecesaria de PII.
- Bitácora append-only mediante privilegios y triggers.
- Eliminación de credenciales heredadas del código y configuración versionada.
- Validación de dependencias mediante `pip-audit`.

Los detalles de los controles y sus evidencias están en `SECURITY.md`.

El modelo de amenazas y los riesgos residuales están documentados en `THREAT_MODEL.md`.

### Parte D · Streaming

Se implementó un consumidor de mediciones sobre Redpanda.

El flujo:

```text
eventos NDJSON
      ↓
tools/producer.py
      ↓
Redpanda / mediciones.raw
      ↓
src/bia/stream/consumidor.py
      ↓
detector de reglas por frontera
      ↓
out/alertas.json
```

El consumidor:

- procesa hasta el offset final;
- termina cuando alcanza ese offset;
- deduplica mediante `evento_id`;
- mantiene estado por frontera;
- detecta lecturas fuera de banda;
- detecta fronteras sin reporte;
- detecta lecturas retroactivas.

### Parte E · Observabilidad

- Logger estructurado en JSON.
- Duración y métricas por etapa.
- Conteo de rechazos por motivo.
- `metrics.json` incorpora resultados del procesamiento batch y streaming.
- Test específico contra los valores reales de PII del maestro para comprobar que no lleguen a `logs/` ni `out/`.
- `RUNBOOK.md` documenta operación, diagnóstico y recuperación.

### Parte F · Infraestructura como código

`infra/` contiene Terraform de referencia para:

- S3
- KMS
- IAM

La configuración pasa `terraform validate`.

No se pretende desplegar esta infraestructura durante la prueba; funciona como referencia de una evolución hacia un entorno cloud.

## Decisiones y límites conocidos

Las decisiones técnicas importantes están documentadas en `DECISIONS.md`.

Los límites deliberados o no implementados están declarados también en:

- `entrega.yaml → no_implementado`
- `SECURITY.md`
- `THREAT_MODEL.md`
- `RUNBOOK.md`

Entre ellos:

- `fact_facturacion.nit_coincide_maestro` existe como campo, pero no se calcula porque el alcance de B6 se resolvió mediante la relación por frontera y el enunciado no exige ese cruce adicional.
- Un superusuario de PostgreSQL puede deshabilitar administrativamente los triggers de la bitácora. La garantía append-only se plantea para sesiones normales y el límite está documentado como riesgo residual.
- La llave de cifrado se mantiene en `.env` en el mismo host en este entorno de prueba. En producción se recomienda un KMS/secret manager.
- Redpanda se ejecuta sin autenticación ni TLS en el entorno local de la prueba. En producción se recomienda SASL/TLS y ACLs.
- El lector de maestros mantiene una rama de manejo de errores estructurales de `csv` específica; los datos entregados no ejercen esa rama.
- `infra/` no modela toda una plataforma productiva: no incluye red, cómputo, RDS, MSK ni CloudTrail.

## Estructura

| Ruta | Contenido |
|---|---|
| `src/bia/` | Pipeline, ingesta, streaming, cifrado, PII, clasificación y logging |
| `sql/` | Roles, esquemas, modelo, cuarentena, auditoría, vistas y grants |
| `tests/` | Parsers, reglas B, streaming, PII, cifrado, permisos y pruebas eliminatorias |
| `scripts/` | Bootstrap de entorno y base de datos |
| `infra/` | Terraform de referencia |
| `tools/` | Producer y verificador de entrega |
| `docs/` | Enunciado, plan y evidencias |

Documentos principales:

- `DECISIONS.md`
- `SECURITY.md`
- `THREAT_MODEL.md`
- `RUNBOOK.md`
- `AI_USAGE.md`
- `data_classification.yaml`
- `entrega.yaml`

## Desarrollo sin Docker

Para entornos donde Docker no esté disponible existe un flujo alternativo documentado mediante PostgreSQL local y un broker Kafka compatible.

Ese flujo se utiliza únicamente como alternativa de desarrollo. El **contrato oficial de entrega fue validado con Docker + Redpanda** mediante WSL2 + Docker Desktop.
