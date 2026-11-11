# Plan de acción — Prueba técnica Bia Energy

> Basado en la sugerencia de ritmo del enunciado (§10) y en los hallazgos
> verificados de los datos (ver `CLAUDE.md` §5). Presupuesto: 12–16 h efectivas
> en 3 días. Marcar `[x]` al completar cada hito y commitear.

## Prioridades que gobiernan el plan

1. **Nunca romper una eliminatoria**: PII en logs/out/analista, secreto heredado
   vivo, inyección ejecutada como código. Se protegen desde el primer DDL.
2. **Parte C (31) y Parte A (24) primero**: más de la mitad de la nota, 100 %
   automática.
3. **Parte B (16) se gana con exactitud**, no con volumen. Conteos esperados ya
   conocidos: 25 clientes con PII, 12 facturas huérfanas, 300 correcciones,
   400 filas MWh.
4. **D, E, F y documentación (29)** el día 3. Si falta tiempo se recorta F y se
   declara en `entrega.yaml → no_implementado`.
5. **Commit pequeño por hito.** El historial es parte de la evaluación y
   alimenta `AI_USAGE.md` y la entrevista.

---

## Día 1 — Base ejecutable, modelo seguro e ingesta idempotente

**Objetivo:** `make setup`, `make up`, `make run` ×2 y `make down` funcionan de
punta a punta con los cuatro meses cargados y el mismo estado en ambas corridas.

### 1.1 Entorno y contrato
- [x] `requirements.txt` reescrito con versiones actuales auditadas
      (psycopg2, kafka-python, cryptography, pyyaml, dotenv, pytest, pip-audit).
      Evidencia en `docs/evidencias/`.
- [x] `.env` generado por `scripts/bootstrap_env.py` (credenciales de
      evaluación fijas = `entrega.yaml`; llaves criptográficas aleatorias por
      máquina). Ver ADR-002.
- [x] `docker-compose.yml` endurecido: credenciales por `.env`, imágenes
      pineadas, puertos en 127.0.0.1, healthchecks, scram-sha-256,
      no-new-privileges.
- [x] ADR-001: MinIO eliminado. ADR-003: dependencias mínimas y csv stdlib.
- [x] Targets `setup`, `up` (`--wait`), `down`, `clean`, `audit`, `help`
      implementados. `setup` probado ×2 en limpio. **`up` pendiente de probar:
      este entorno no tiene Docker** (ver nota abajo).

> **Nota de entorno (2026-11-11):** la máquina de desarrollo no tiene Docker ni
> sudo sin contraseña. Todo lo que no depende de contenedores se prueba aquí
> (`make setup`, `make test`, parsers). `make up`/`run`/`stream` de punta a
> punta requieren instalar Docker (`sudo apt install docker.io docker-compose-v2`
> y añadir el usuario al grupo `docker`) o correr en otra máquina.
> **Alternativa en uso:** `scripts/dev_pg_sin_docker.sh start` levanta un
> Postgres 16.15 real sin root (binarios Zonky) en `127.0.0.1:8432`; con
> `PG_PORT=8432` todo lo que es Postgres (bootstrap, run, tests) se prueba aquí.
> Solo Redpanda/streaming queda pendiente de Docker.

### 1.2 Modelo de datos y SQL (`sql/`)
- [x] Esquemas: `core` (hechos/dimensiones), `quarantine` (rechazos con motivo),
      `audit` (bitácora), `analytics` (vistas del analista). `sql/01_schemas.sql`.
- [x] `core.fact_consumo_horario`: PK `(frontera_id, ts_local)`, `mes` generado,
      `version_lectura`, `archivo_nombre` (desempate B3), `archivo_id`, `fila_origen`.
      CHECK 0 ≤ kwh ≤ 1e6. `sql/10_core.sql`.
- [x] `core.dim_cliente`: PII en `bytea` `*_cifrado` + `nit_hmac`/`email_hmac`;
      `observaciones_enmascaradas`. También `fact_facturacion` con PII cifrada.
- [x] `core.control_archivos` (nombre, hash, formato detectado, filas, estado,
      corridas) para idempotencia y datos tardíos.
- [x] Roles `rol_ingesta`/`rol_analista`/`rol_auditor` (`sql/00_roles.sql`, sin
      secretos: contraseñas por `set_config`) y grants (`sql/90_grants.sql`).
      **Probados ya** (ver 2.4/2.5) contra Postgres 16.15 local.

### 1.3 Clasificación viva (C1)
- [x] `data_classification.yaml`: 150 columnas en 15 objetos + campos de origen
      + políticas por nivel.
- [x] `src/bia/classification.py`: consulta por tabla/columna y por campo de
      origen; `verificar_contra_db` (information_schema 1:1) y
      `verificar_vistas_analista` corren en `make up`. Tests offline parsean el DDL.

### 1.4 Ingesta de consumos (Parte A)
- [x] Descubrimiento por glob en `data/consumos/`, orden lexicográfico (`ingesta/archivos.py`).
- [x] Parser por archivo: separador, encoding, columnas, coma decimal, UTC vs
      local, unidad por columna o por fila (`ingesta/consumos.py`).
- [x] Una transacción por archivo; cabecera ilegible o >50 % malformado →
      rechazo entero; filas malas → cuarentena; error inesperado → RECHAZADO
      sin abortar la corrida. Meses cargados intactos (verificado por hash).
- [x] B3 en memoria + `ON CONFLICT` con la misma regla entre archivos; la
      corrección de abril (300) se absorbe sin recarga. ADR-005.
- [x] `core.control_archivos`: un archivo ya OK con el mismo hash no se
      reescribe (sí se re-parsea para que el reporte sea función de los archivos).

### 1.5 Prueba de idempotencia y robustez
- [x] `make run` ×2 → hashes por mes y `max(actualizado_en)` idénticos; `dq_report`
      y `consumo_mensual` byte a byte iguales.
- [x] Mes nuevo (mayo sintetizado desde enero) → 29.556 filas cargadas, meses
      previos sin cambio.
- [x] Archivo truncado a mitad de línea con bytes binarios → PARCIAL (filas
      corruptas a cuarentena); archivo PNG → ARCHIVO_CORRUPTO. Meses previos con
      hash idéntico. Segunda corrida del escenario: sin cambios.

---

## Día 2 — Calidad exacta y toda la seguridad

**Objetivo:** `dq_report.json` y `consumo_mensual.csv` cuadran con los conteos
verificados; los tres roles no pueden hacer nada fuera de su contrato.

### 2.1 Reglas B1–B5 y salidas
- [x] B1 zona horaria UTC−5 fijo (febrero en UTC con `Z`; también offsets).
- [x] B2 conversión MWh → kWh: **400** ✔.
- [x] B4: nulos 60, negativos 90, outliers 45 (coinciden con el perfilado).
- [x] B5: 655 huecos totales = 115.200 horas esperadas − 114.545 válidas ✔.
- [x] `out/consumo_mensual.csv`: 160 filas (40 fronteras × 4 meses), 3 decimales.
- [x] `out/dq_report.json` con las 12 llaves. Correcciones **300** ✔, MWh **400** ✔,
      duplicados 180 exactos / **120** conflictivos (empate de versión); las 300
      v1 reemplazadas por la corrección van a cuarentena como
      `B3_SUPERADA_POR_VERSION` y solo cuentan en correcciones (ADR-005, revisado).
      `fronteras_huerfanas_facturacion` **12** ✔.

### 2.2 Clientes y facturación (B6, B7, C2)
- [x] `clientes.csv` latin-1 con `csv`, SQL parametrizado, `TEXT`; 40/40 cargadas
      (incluida la razón social de 1.632 chars y la inyección).
- [x] Test explícito (`tests/test_eliminatorias_db.py`): tras cargar el
      `clientes.csv` real por el mismo camino que `make run`, la tabla de
      consumos existe y tiene las mismas filas; `CLI0036` (inyección) y
      `CLI0033` (1.632 chars) quedan en `dim_cliente` como texto. También:
      la bitácora que lee `rol_auditor` no contiene PII real ni patrones de
      correo/celular/cédula; y `facturacion.py` cifra lo que dicta
      `data_classification.yaml`, no una lista fija en código.
- [x] PII en `observaciones` enmascarada: **25** clientes ✔ (`bia/pii.py`).
- [x] Facturación JSON (`ingesta/facturacion.py`): 160 facturas en
      `fact_facturacion`, **12** huérfanas a cuarentena con payload cifrado
      (B6), `cliente.nit`/`email` cifrados + HMAC, `hash_fila` para no recifrar.
      `consumo_facturado_kwh` coincide con `consumo_mensual.csv` (dif. máx.
      0,005 kWh por redondeo a 2 decimales): validación externa de B1–B5.

### 2.3 Cifrado en reposo (C3)
- [x] AES-256-GCM por valor + HMAC-SHA256 de NIT normalizado y correo, llaves
      derivadas con HKDF desde `.env` (`bia/crypto.py`).
- [x] Verificación como superusuario (`test_superusuario_no_ve_pii_en_claro`):
      ningún correo ni NIT real del maestro aparece en columnas cifradas ni en
      claro de `dim_cliente` y `fact_facturacion`; versión de cifrado 0x01.

### 2.4 Menor privilegio (C4)
- [x] Vistas `analytics.*` sin PII (`sql/40_analytics.sql`).
- [x] `REVOKE ALL ON SCHEMA public FROM PUBLIC`, `REVOKE ... ON DATABASE`,
      grants mínimos por esquema y **por columna** (ingesta no lee `*_cifrado`).
- [x] Tests negativos por rol en `tests/test_permisos_db.py` (32 casos). Se
      saltan si no hay base; pasan contra PG 16.15 local.

### 2.5 Bitácora append-only (C5)
- [x] `audit.bitacora`: quién (db/os/host), componente, etapa, evento, objeto,
      filas, resultado, duración, detalle jsonb sin PII.
- [x] `REVOKE UPDATE, DELETE, TRUNCATE` incluido el dueño + triggers que abortan
      UPDATE/DELETE/TRUNCATE en cualquier sesión normal, también la del
      superusuario (test lo comprueba). Límite conocido: un superusuario con
      capacidad administrativa puede desactivar el trigger; riesgo residual
      documentado en ADR-004 y pendiente de reflejar en `THREAT_MODEL.md`.

### 2.6 Secretos y dependencias (C6, C7)
- [x] `config/pipeline.yaml` eliminado (`3509c47`); todo por `.env`
      (documentado en `SECURITY.md` §5).
- [x] `pip-audit` limpio; evidencia en `docs/evidencias/` y `SECURITY.md` §7.

### 2.7 Tests (G)
- [x] Parsers de los cuatro formatos (`test_consumos_reglas.py`), reglas B1–B7
      (incl. B6 en `test_facturacion_b6.py` y B7 en `test_eliminatorias_db.py`),
      PII y cifrado (`test_pii_crypto.py`), clasificación, permisos por rol
      (33 casos). 69 pruebas recolectadas, 45 funciones.
- [x] `make test` en verde con y sin base (sin base se saltan las 35 de Postgres).

---

## Día 3 — Streaming, operación, infraestructura y documentación

**Objetivo:** el contrato completo corre en limpio desde cero y `make verificar`
pasa.

### 3.1 Streaming (Parte D)
- [x] `make stream` (`src/bia/stream/`): crea tópico, lanza `tools/producer.py`,
      consume hasta el offset final; inactividad 20 s y plazo 240 s (ADR-006).
      ~2 s contra Kafka 4.3.2 local (sin Docker aquí; falta probar con Redpanda).
- [x] `FRONTERA_SIN_REPORTE`: **6** (una por caída, `ts` del tercer intervalo,
      caídas abiertas al cierre).
- [x] `CONSUMO_FUERA_DE_BANDA` (`kwh > max_kwh`, estricto): **22**.
- [x] `LECTURA_RETROACTIVA`: **5**.
- [x] `out/alertas.json`: 33 alertas, sin duplicados, ISO 8601 con `-05:00`,
      idéntico en corridas repetidas (deduplicación por `evento_id`).

### 3.2 Observabilidad (Parte E)
- [x] Logger JSON una línea por evento (`bia/logging_json.py`) con doble filtro:
      campos `en_logs: false` de la clasificación + regex de PII.
- [x] `out/metrics.json`: filas, rechazos por motivo, duración por etapa,
      timestamps, formato detectado por archivo.
- [x] Test: PII real del maestro + regex de correos/celulares/cédulas sobre
      `logs/` y `out/` → 0; cada línea del log es JSON; `metrics.json` con los
      campos mínimos de la Parte E (`tests/test_eliminatorias_db.py`).

### 3.3 Terraform (Parte F) — primero en recortarse
- [x] `infra/`: 3 buckets S3 (versionado, SSE-KMS, sin acceso público, solo
      TLS; `auditoria` con Object Lock COMPLIANCE), 2 llaves KMS con política
      (`datos`, `pii` sin `Decrypt` para los roles), 3 roles IAM equivalentes a
      C4. `terraform fmt` y `terraform validate` pasan (Terraform 1.17.2, aws 6.x).

### 3.4 Documentación (C8, H)
- [x] `THREAT_MODEL.md`: 14 amenazas STRIDE sobre esta arquitectura, control,
      estado real, Anexo A ISO 27001:2022.
- [x] `SECURITY.md` con comandos de verificación concretos (ocho secciones de
      la plantilla; cada comando de §8 ejecutado contra la base antes de documentarlo).
- [ ] `DECISIONS.md` ≥ 5 ADRs: modelo, MinIO, cifrado, duplicados,
      idempotencia por archivo.
- [ ] `RUNBOOK.md`.
- [ ] `AI_USAGE.md`: prompts reales, qué se cambió, error concreto de la IA
      con archivo y línea.
- [ ] `README.md` propio; enunciado movido a `docs/ENUNCIADO.md`.

### 3.5 Cierre
- [ ] `entrega.yaml` completo: roles, credenciales efímeras, objetos **con
      esquema**, `no_implementado`.
- [ ] `make clean` + borrar volúmenes → correr los 7 targets en orden desde
      cero → `make verificar` en 0.
- [ ] Zip `apellido_nombre.zip` sin `data/`, `.venv/`, volúmenes; < 10 MB;
      con `.git/`.

---

## Qué se recorta si el tiempo se acaba (en este orden)

1. Terraform completo (F, 5 pts).
2. `FRONTERA_SIN_REPORTE` si el resto del streaming ya funciona.
3. Runbook extenso.

**Nunca se recortan:** idempotencia, cuarentena, cifrado, roles, bitácora,
formatos exactos de salida. Lo recortado se declara explícitamente.

## Registro de avance

| Fecha | Hito | Commit |
|---|---|---|
| 2026-11-11 | Punto de partida: scaffolding auditado, CLAUDE.md, plan | `b66f2f2`, `5f8126f` |
| 2026-11-11 | 1.1 Entorno y contrato: setup/up/down/clean, compose endurecido, deps auditadas, ADR-001..003 | `3509c47` |
| 2026-11-11 | 1.2 + 1.3 Modelo SQL, roles/grants, bitácora append-only, vistas, clasificación viva, bootstrap_db, 43 tests | `331f014` |
| 2026-11-11 | 1.4 + 1.5 Pipeline batch completo (maestros con cifrado/PII, consumos B1–B5, cuarentena, bitácora, reportes, logs JSON), idempotencia y robustez verificadas, 59 tests, ADR-005 | `1878ff5` |
| 2026-11-11 | 2.2 Facturación y B6: 160 facturas, 12 huérfanas, dq=12, idempotente, 64 tests | `c63035d` |
| 2026-11-11 | B3 revisado: 120 conflictivos (empate de versión), 300 reemplazos por versión como `B3_SUPERADA_POR_VERSION`; ADR-005 actualizado; test B6 sin aserción probabilística; 66 tests | `0ab51f6`, `19da3cc` |
| 2026-11-11 | Hito 2 (código y tests): facturación gobernada por la clasificación (C1), tests de eliminatorias 1 y 3 con datos reales contra la base, redacción honesta del append-only; 69 tests | `bdd211a`, `d18895e`, `87267cf` |
| 2026-11-11 | `entrega.yaml`: email y los cuatro objetos reales; `make verificar` exit 0 con la base en 5432 (Postgres local sin Docker, mismo `.env`); `make test` y `make run` x2 en verde. Pendiente: `horas_invertidas` y `no_implementado` al cierre | `bf1967c` |
| 2026-11-11 | `SECURITY.md` completo con evidencia real (commits, tests, pip-audit, comandos por rol verificados); 2.6 cerrado | `5ae4071` |
| 2026-11-11 | `THREAT_MODEL.md`: DFD con 5 fronteras, 11 activos, 14 amenazas STRIDE con evidencia y estado real (Hito 3 marcado como no implementado), 8 riesgos aceptados; 2.7 cerrado | `b3606f3` |
| 2026-11-11 | Portabilidad Windows / Python < 3.11 (encoding en test, NUL como `FILA_CORRUPTA`) | `4656c74` |
| 2026-11-11 | 3.1 Streaming: detector puro + consumidor Kafka, 33 alertas idénticas entre corridas, ADR-006, 14 tests (83 en total) | `9b3949d`, `1d2a71b` |
