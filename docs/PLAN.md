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
- [ ] Descubrimiento por glob en `data/consumos/`, orden lexicográfico.
- [ ] Parser por archivo: detecta separador, encoding, nombres de columna,
      coma decimal, UTC vs local, unidad.
- [ ] Staging por archivo dentro de una transacción; archivo malo → rechazo
      entero o filas a cuarentena; meses cargados intactos.
- [ ] Resolución de duplicados (B3) y upsert sobre clave natural que absorbe
      `consumos_2026-04_correccion.csv` sin recarga total.
- [ ] Registro del archivo en la tabla de control; no reprocesar si el hash no
      cambió.

### 1.5 Prueba de idempotencia y robustez
- [ ] `make run` ×2 → mismos conteos en base y mismo `out/`.
- [ ] Soltar copia renombrada de un mes (formato conocido) → se carga, lo demás
      no cambia.
- [ ] Soltar archivo truncado/corrupto → cuarentena o rechazo, lo demás no
      cambia.

---

## Día 2 — Calidad exacta y toda la seguridad

**Objetivo:** `dq_report.json` y `consumo_mensual.csv` cuadran con los conteos
verificados; los tres roles no pueden hacer nada fuera de su contrato.

### 2.1 Reglas B1–B5 y salidas
- [ ] B1 zona horaria `America/Bogota` (febrero viene en UTC).
- [ ] B2 conversión MWh → kWh (esperado: 400 filas).
- [ ] B4 descartes (nulo, negativo, > 1e6) contados por motivo, a cuarentena.
- [ ] B5 huecos por frontera y mes (fila inexistente o descartada).
- [ ] `out/consumo_mensual.csv` (3 decimales, `YYYY-MM` local).
- [ ] `out/dq_report.json` con **todas** las llaves. Validar: 300 correcciones
      aplicadas, 400 MWh convertidos.

### 2.2 Clientes y facturación (B6, B7, C2)
- [ ] `clientes.csv` en latin-1 con módulo `csv`, consultas parametrizadas,
      columnas `TEXT`. Ninguna fila perdida (40).
- [ ] Test explícito: tras cargar `CLI0036` (inyección) la tabla de consumos
      sigue intacta.
- [ ] Detección y enmascarado de PII en `observaciones` (esperado: 25 clientes).
- [ ] Facturación JSON: 12 huérfanas a cuarentena; bloque `cliente` cifrado.

### 2.3 Cifrado en reposo (C3)
- [ ] Cifrado a nivel de aplicación con llave en `.env`; HMAC con sal para NIT
      normalizado (solo dígitos) y correo.
- [ ] Verificación como superusuario: ninguna columna PII legible en claro,
      ni en clientes ni en facturación.

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
      UPDATE/DELETE/TRUNCATE incluso al superusuario (test lo comprueba).

### 2.6 Secretos y dependencias (C6, C7)
- [ ] `config/pipeline.yaml` eliminado o sin ningún valor sensible; todo por
      `.env`.
- [ ] `pip-audit` limpio; salida guardada para `SECURITY.md`.

### 2.7 Tests (G)
- [ ] Parsers de los cuatro formatos, reglas B1–B7, PII, cifrado, permisos.
- [ ] `make test` en verde.

---

## Día 3 — Streaming, operación, infraestructura y documentación

**Objetivo:** el contrato completo corre en limpio desde cero y `make verificar`
pasa.

### 3.1 Streaming (Parte D)
- [ ] `make stream`: crea tópico, lanza `tools/producer.py`, consume hasta el
      offset final con timeout de inactividad propio (< 300 s total).
- [ ] `FRONTERA_SIN_REPORTE` (3 intervalos de 15 min, una alerta por caída,
      `ts` del tercer intervalo; cerrar caídas abiertas al fin del día).
- [ ] `CONSUMO_FUERA_DE_BANDA` (`kwh > max_kwh`, estricto).
- [ ] `LECTURA_RETROACTIVA` (ts anterior al último procesado; ~5 esperadas).
- [ ] `out/alertas.json` sin duplicados, ISO 8601 con offset.

### 3.2 Observabilidad (Parte E)
- [ ] Logger JSON una línea por evento en `logs/pipeline.log`, con filtro que
      rechaza valores de columnas clasificadas.
- [ ] `out/metrics.json`: filas leídas/cargadas, rechazos por motivo, duración
      por etapa, timestamp.
- [ ] Test: regex de correos/celulares/cédulas sobre `logs/` y `out/` → 0.

### 3.3 Terraform (Parte F) — primero en recortarse
- [ ] S3 cifrado + versionado, llave KMS con política, 3 roles IAM
      equivalentes a C4. `terraform validate` pasa.

### 3.4 Documentación (C8, H)
- [ ] `THREAT_MODEL.md`: ≥ 8 amenazas STRIDE sobre esta arquitectura, control,
      estado real, Anexo A ISO 27001:2022.
- [ ] `SECURITY.md` con comandos de verificación concretos.
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
| 2026-11-11 | 1.2 + 1.3 Modelo SQL, roles/grants, bitácora append-only, vistas, clasificación viva, bootstrap_db, 43 tests | (este commit) |
