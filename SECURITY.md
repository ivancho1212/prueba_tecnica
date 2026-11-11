# Seguridad de la solución

Este documento describe los controles de seguridad que implementa el pipeline
y cómo reproducir cada verificación. Todo lo que se afirma aquí tiene un
archivo, un commit o un test detrás; las limitaciones conocidas están
marcadas como tales. El modelo de amenazas está en `THREAT_MODEL.md`; las
decisiones de diseño en `DECISIONS.md` (ADR-001 a ADR-005).

Convención de nombres de rol (fija, la exige `src/bia/config.py`):
`rol_ingesta`, `rol_analista`, `rol_auditor`. Superusuario local: `bia_admin`.
Las credenciales efímeras de evaluación están en `entrega.yaml`; nunca en este
documento ni en el código.

---

## 1. Clasificación de datos

`data_classification.yaml` clasifica **151 columnas de 15 objetos** (tablas y
vistas de los esquemas `core`, `quarantine`, `audit`, `analytics`) y además
los **campos de los archivos crudos** (`campos_origen`) en cuatro niveles:

| Nivel | Cifrado en reposo | En logs | En vista de analista | Cuarentena en claro |
|---|---|---|---|---|
| Público | no | sí | sí | sí |
| Interno | no | sí | sí | sí |
| Confidencial | no | **no** | **no** | sí |
| Restringido-PII | **sí** | **no** | **no** | **no** |

No es un documento muerto: el código lo lee en cuatro puntos.

1. **`make up` falla si el modelo y el YAML no coinciden 1:1.**
   `scripts/bootstrap_db.py` llama a `Clasificacion.verificar_contra_db`
   (`src/bia/classification.py`), que compara contra `information_schema.columns`
   y lanza si hay una columna física sin clasificar o una clasificada que ya no
   existe. Añadir una columna sin clasificarla rompe el arranque a propósito.
2. **Las vistas del analista no pueden exponer niveles prohibidos.**
   `verificar_vistas_analista` se ejecuta al inicio de cada `make run`
   (`src/bia/pipeline.py`) y aborta si alguna columna de `analytics.*` tiene
   `en_vista_analista: false`.
3. **Qué se cifra lo decide el YAML, no el código.**
   `maestros.procesar_clientes` y `facturacion.procesar_facturacion` consultan
   `campos_origen_por_politica(<archivo>, cifrado_en_reposo=True)` y se niegan
   a continuar si el conjunto no coincide con las columnas `*_cifrado` del
   modelo (commits `331f014` y `bdd211a`). El test
   `test_cifrado_de_facturacion_lo_dicta_la_clasificacion` reclasifica
   `cliente.nit` a Interno y comprueba que el parser falla en vez de guardar en
   claro.
4. **El logger redacta por nombre de campo.** `campos_prohibidos_en_logs()`
   entrega al filtro de `src/bia/logging_json.py` todos los campos con
   `en_logs: false` (origen y modelo); su valor se sustituye por `[REDACTADO]`.

Tests: `tests/test_classification.py` (6).

---

## 2. Cifrado

| Qué | Dónde | Mecanismo | Gestión de la llave |
|---|---|---|---|
| NIT, correo, celular, dirección, representante legal y `observaciones` originales del maestro de clientes | `core.dim_cliente.*_cifrado` / `direccion_cifrada` (`bytea`) | AES-256-GCM a nivel de aplicación, nonce aleatorio de 12 bytes por valor, byte de versión `0x01` como AAD. Formato: `0x01 ‖ nonce ‖ ciphertext‖tag` | Secreto `BIA_ENC_KEY` en `.env`, generado por máquina con `secrets.token_urlsafe(32)` en `scripts/bootstrap_env.py`; la llave AES se deriva con HKDF-SHA256 (`info=bia-enc-v1`). Nunca entra a la base ni al repo |
| NIT y correo de facturación (`cliente.nit`, `cliente.email_facturacion`), PII duplicada fuera del maestro | `core.fact_facturacion.cliente_nit_cifrado`, `cliente_email_cifrado` | Idéntico al anterior | Misma llave |
| Filas completas rechazadas de clientes y facturación | `quarantine.registros_rechazados.payload_cifrado` | AES-256-GCM sobre el JSON de la fila; `payload` en claro queda `NULL` para esas fuentes (`ORIGENES_CON_PII` en `src/bia/ingesta/carga.py`) | Misma llave |
| Seudónimo para igualdad y joins | `nit_hmac`, `email_hmac`, `cliente_nit_hmac`, `cliente_email_hmac` (`bytea`, Confidencial) | HMAC-SHA256 sobre el valor **normalizado** (NIT solo dígitos; correo en minúsculas) | Secreto `BIA_HMAC_KEY` en `.env`, derivado con HKDF (`info=bia-hmac-v1`), independiente de la de cifrado |

Implementación: `src/bia/crypto.py` (clase `Cifrador`, normalizadores).

**Joins sobre campos sensibles.** Nunca se compara el valor: se compara el
HMAC del valor normalizado. Así el NIT `900.123.456-7`, `NIT 900123456-7` y
`9001234567` producen el mismo seudónimo sin que exista en ninguna tabla el
NIT en claro. La columna `fact_facturacion.nit_coincide_maestro` está
preparada para ese join pero **no se calcula** (queda `NULL`): estructura
lista, lógica fuera del alcance de esta entrega.

**Lo que el superusuario ve.** El test `test_superusuario_no_ve_pii_en_claro`
(`tests/test_permisos_db.py`) hace lo que hará el evaluador: conecta como
`bia_admin`, lee las columnas crudas de `core.dim_cliente` y
`core.fact_facturacion` y comprueba que cada valor empieza por `0x01` y que
ninguno de los correos ni NIT reales del maestro aparece en ellas.

**Limitaciones honestas.**
- El HMAC es determinístico por diseño: dos filas con el mismo NIT tienen el
  mismo seudónimo. Es seudonimización, no anonimización. Quien posea
  `BIA_HMAC_KEY` podría confirmar un NIT por fuerza bruta.
- La llave vive en `.env`, en la misma máquina que la base durante la
  evaluación local. El superusuario de **Postgres** no puede descifrar; quien
  tenga acceso al sistema de archivos del host, sí. En producción la llave iría
  a un KMS (ver `infra/`).
- `razon_social` y `cliente_razon_social` se guardan en claro: son datos de
  empresa clasificados Confidencial, no PII de persona natural. El analista no
  los ve (§3).

Tests: `tests/test_pii_crypto.py` (4), `test_pii_fisica_siempre_cifrada_en_bytea`
y `test_superusuario_no_ve_pii_en_claro` (`tests/test_permisos_db.py`),
`tests/test_facturacion_b6.py` (5).

---

## 3. Control de accesos (menor privilegio)

Roles creados en `sql/00_roles.sql` con `LOGIN NOSUPERUSER NOCREATEDB
NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS`, `statement_timeout = 120s`
y contraseña inyectada por `set_config` desde `.env` (`scripts/bootstrap_db.py`),
nunca por texto en el SQL. `rol_analista` y `rol_auditor` tienen además
`default_transaction_read_only = on`.

Antes de cualquier GRANT, `sql/01_schemas.sql` hace `REVOKE ALL ON SCHEMA
public FROM PUBLIC` y `REVOKE ALL ON DATABASE ... FROM PUBLIC`;
`sql/90_grants.sql` empieza revocando todo sobre los cuatro esquemas a los tres
roles y concede solo lo siguiente.

| Rol | Puede | NO puede | Dónde está el GRANT |
|---|---|---|---|
| `rol_ingesta` | `USAGE` en `core`, `quarantine`, `audit`. `SELECT, INSERT, UPDATE` en `control_archivos`, `dim_frontera`, `limite_frontera`, `fact_consumo_horario`. `INSERT, UPDATE` en `dim_cliente` y `fact_facturacion` con `SELECT` **solo por columna** sobre las no sensibles (ids, razón social, HMAC, `hash_fila`, enmascaradas). `INSERT` en cuarentena con `SELECT` solo sobre metadatos. `INSERT` en `audit.bitacora`. Secuencias | Leer ninguna columna `*_cifrado` / `direccion_cifrada` (un `SELECT *` sobre `dim_cliente` o `fact_facturacion` falla). Leer `payload` ni `payload_cifrado` de cuarentena. `UPDATE`/`DELETE`/`TRUNCATE` en la bitácora. Nada en `analytics`. `DELETE` en ninguna tabla | `sql/90_grants.sql` líneas 20–49 |
| `rol_analista` | `USAGE` en `analytics` y `SELECT` sobre sus 7 vistas (`v_consumo_mensual`, `v_consumo_horario`, `v_frontera`, `v_cliente`, `v_facturacion_mensual`, `v_cuarentena_resumen`, `v_archivos_procesados`). Transacciones de solo lectura | Acceder a `core`, `quarantine` ni `audit` (sin `USAGE`: el esquema "no existe" para él). Ver ninguna columna cifrada, HMAC ni `payload` (las vistas no las incluyen y `verificar_vistas_analista` lo garantiza). Escribir nada (`default_transaction_read_only`) | `sql/90_grants.sql` líneas 52–54; `sql/40_analytics.sql` |
| `rol_auditor` | `USAGE` en `audit` y `SELECT` sobre `audit.bitacora`. Solo lectura | Acceder a `core`, `quarantine` ni `analytics`. Modificar la bitácora. Ver PII: la bitácora solo guarda ids, conteos y motivos (verificado en §6) | `sql/90_grants.sql` líneas 57–58 |

**Consecuencia de diseño en el código.** Como `rol_ingesta` no puede leer las
columnas cifradas, el upsert de `dim_cliente` y `fact_facturacion` no usa
`ON CONFLICT DO UPDATE` (Postgres exige `SELECT` sobre toda columna referida
vía `EXCLUDED`): usa `UPDATE ... WHERE hash_fila IS DISTINCT FROM` seguido de
`INSERT ... WHERE NOT EXISTS`, siempre con parámetros (`src/bia/ingesta/carga.py`).
El rol escribe PII sin poder leerla.

Tests: `tests/test_permisos_db.py` (9 funciones, 32 casos parametrizados), que
se conectan con cada rol e intentan exactamente lo prohibido; se saltan si no
hay base y pasan con la infraestructura arriba.

---

## 4. Enmascaramiento de PII

**Texto libre (C2).** El campo `observaciones` de `clientes.csv` trae
correos, celulares y cédulas escritos a mano. `src/bia/pii.py` los detecta con
expresiones regulares y los sustituye por tokens:

| Tipo | Patrón (resumen) | Token |
|---|---|---|
| Correo | `usuario@dominio.tld` | `[EMAIL]` |
| Celular colombiano | 10 dígitos que empiezan por 3, con o sin `+57`, separadores opcionales | `[CELULAR]` |
| Cédula | `CC`/`C.C.`/`cédula` seguido de 6–10 dígitos; también números sueltos de 7–10 dígitos que no sean celulares | `[CEDULA]` |

Resultado en la base: `dim_cliente.observaciones_enmascaradas` (Confidencial,
texto con tokens), `observaciones_cifrado` (el original, Restringido-PII,
cifrado), `pii_en_observaciones` (booleano) y `tipos_pii_observaciones`
(qué tipos se encontraron). Con los datos del paquete se detectan **25
clientes afectados** de 40, cifra que se reporta en
`out/dq_report.json → clientes_con_pii_en_texto_libre`.

**Logs.** El mismo enmascarador se aplica a **toda cadena** que pase por el
logger, además de la redacción por nombre de campo (§1). Ningún valor de
`clientes.csv` ni de facturación se loguea: solo ids, conteos, reglas y
motivos (`src/bia/logging_json.py`, test `test_logger_json_sin_pii`).

**Prueba con PII real y regex (eliminatoria 1).** Tras una corrida completa se
buscaron en `logs/pipeline.log`, `out/dq_report.json`, `out/metrics.json` y
`out/consumo_mensual.csv` todos los correos de contacto, celulares, NIT (≥ 9
dígitos) y cédulas reales del maestro, más las regex genéricas que usa el
evaluador (correo, `3\d{9}`, `CC\s*\d{6,10}`). Resultado: **cero
coincidencias** en todo `logs/pipeline.log` (acumula varias corridas; cada
línea es JSON válido). La misma
verificación sobre `audit.bitacora` está automatizada en
`test_eliminatoria_1_bitacora_del_auditor_sin_pii` (`tests/test_eliminatorias_db.py`).

**Limitación.** La detección es por patrón: una cédula escrita sin prefijo y
con menos de 7 dígitos, o un correo malformado, no se reconocería. Por eso el
original se cifra completo y la versión enmascarada se trata como
Confidencial (fuera de logs y de la vista del analista), no como pública.

---

## 5. Gestión de secretos

**Cómo se inyectan.** Todo entra por variables de entorno leídas de `.env`
(`src/bia/config.py` con `python-dotenv`); `.env` está en `.gitignore` junto
con `*.pem` y `*.key`. `make setup` ejecuta `scripts/bootstrap_env.py`, que
copia `.env.example` (sin valores de llave) y genera `BIA_ENC_KEY` y
`BIA_HMAC_KEY` aleatorias por máquina; es idempotente y no sobreescribe
valores existentes. `docker-compose.yml` exige cada credencial con
`${VAR:?}`: sin `.env` no arranca. `config.cargar()` no imprime credenciales
y `verificar_entrega.py` las lee solo de `entrega.yaml`.

**Qué había en el scaffolding heredado y qué se hizo.**

| Dónde | Qué venía en claro | Qué se hizo |
|---|---|---|
| `config/pipeline.yaml` | `access_key` y `secret_key` de MinIO, `password` de Postgres del usuario `postgres`, una `llave_maestra` de "cifrado" con `algoritmo: none`, y un `slack_webhook` | Los valores se redactaron **antes del primer commit** (`b66f2f2`), así que nunca entraron al historial `.git/`. El archivo completo se eliminó en `3509c47` (Hito 1.1): nada del pipeline lee YAML de configuración |
| `docker-compose.yml` | `POSTGRES_USER`/`POSTGRES_PASSWORD` por defecto de Postgres y `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` por defecto de MinIO; imágenes sin pinear (`:latest`, `16-alpine`) | En `3509c47` se pasó a `${VAR:?}` desde `.env`, se eliminó MinIO (ADR-001: ningún dato del paquete pasa por él), se pinearon `postgres:16.9-alpine` y `redpandadata/redpanda:v26.2.6`, puertos solo en `127.0.0.1`, healthchecks y `scram-sha-256` |
| `requirements.txt` | Librerías con CVE conocidos | Ver §7 |

Verificación de que los valores no están en el historial: `git log --all -p`
no contiene el webhook ni las contraseñas heredadas; la única aparición de la
palabra `minioadmin` son las dos menciones descriptivas en `CLAUDE.md` y
`DECISIONS.md` (nombre público por defecto de MinIO, no un secreto de esta
entrega). Todos los valores heredados se consideran comprometidos y no se
reutilizan.

**Credenciales de evaluación.** Las contraseñas de `bia_admin` y de los tres
roles que aparecen en `entrega.yaml` y en `.env.example` son **efímeras y
públicas por requisito del enunciado** (el evaluador necesita conectarse);
están pensadas para un Postgres local escuchando solo en `127.0.0.1`
(ADR-002). Lo que sí es secreto por máquina son las dos llaves criptográficas,
que no se comparten con nadie: el evaluador verifica el cifrado precisamente
porque no puede descifrar.

Test: `test_plantilla_real_no_trae_llaves_ni_env_versionado`
(`tests/test_bootstrap_env.py`).

---

## 6. Trazabilidad y auditoría

**Esquema** (`sql/30_audit.sql`, `audit.bitacora`):

| Columna | Contenido |
|---|---|
| `evento_id`, `ts` | secuencia y `clock_timestamp()` |
| `corrida_id` | UUID de la ejecución (`make run`, bootstrap o test) |
| `usuario_db`, `usuario_os`, `host_origen` | quién: `current_user`, usuario del SO, `inet_client_addr()` |
| `componente`, `etapa`, `evento`, `objeto` | qué: `batch`/`bootstrap`, etapa del pipeline, evento, archivo o tabla afectada (nunca datos) |
| `filas`, `duracion_ms`, `resultado` | cuánto y con qué resultado (`OK`, `ERROR`, `PARCIAL`, `INFO`) |
| `detalle` | `jsonb` con conteos y metadatos; prohibido PII |

Cada corrida registra inicio, cada archivo procesado o rechazado (con filas
leídas, cargadas, rechazadas, huérfanas, clientes con PII), y fin. Una corrida
completa del paquete deja 14 eventos.

**Append-only, dos capas.**

1. **Privilegios.** `sql/90_grants.sql` revoca `UPDATE, DELETE, TRUNCATE`
   sobre `audit.bitacora` a `PUBLIC`, a los tres roles **y al propio dueño de
   la tabla**. `rol_ingesta` solo tiene `INSERT`; `rol_auditor` solo `SELECT`.
   Test `test_privilegios_bitacora_revocados_hasta_para_el_duenio`.
2. **Triggers.** `trg_bitacora_solo_append` (`BEFORE UPDATE OR DELETE`, por
   fila) y `trg_bitacora_sin_truncate` (`BEFORE TRUNCATE`, por sentencia)
   ejecutan `audit.fn_solo_append`, que lanza `insufficient_privilege` con el
   mensaje `audit.bitacora es append-only`. Los privilegios no aplican al
   superusuario; el trigger sí. Test
   `test_bitacora_append_only_en_sesion_normal_de_superusuario`.

**Límite real, dicho sin rodeos.** Esto frena cualquier sesión normal,
incluida la del superusuario. **No** frena a un superusuario con capacidad
administrativa que decida `ALTER TABLE audit.bitacora DISABLE TRIGGER ALL` o
`SET session_replication_role = replica` antes de modificar. En PostgreSQL no
existe inmutabilidad absoluta frente al superusuario del propio motor; el
control honesto para ese caso es externo (replicar la bitácora a un destino
que la base no controle, auditoría del motor, y no operar la aplicación como
superusuario). Está registrado como riesgo residual en ADR-004 y en
`THREAT_MODEL.md`.

**La bitácora no contiene PII.** El pipeline solo le pasa ids, conteos,
reglas y motivos; los motivos de cuarentena de clientes y facturación son
genéricos (no incluyen el valor rechazado). Verificado con datos reales en
`test_eliminatoria_1_bitacora_del_auditor_sin_pii`.

---

## 7. Dependencias

**Herramienta.** `pip-audit 2.10.1` (base de datos PyPI Advisory / OSV),
instalado en el mismo venv y ejecutable con `make audit`, que guarda la salida
en `docs/evidencias/pip_audit_actual.txt`. Para el `requirements.txt`
heredado no fue posible resolverlo con `pip-audit -r` en el entorno de
desarrollo (Python sin `ensurepip`), así que se consultó la **API de OSV**
(`api.osv.dev`) para cada pin exacto; la evidencia está en
`docs/evidencias/pip_audit_heredado_osv.txt`.

**Qué se encontró (heredado, 2026-11-11).**

| Paquete heredado | Avisos OSV | Ejemplos de IDs | ¿Lo usa algún componente? |
|---|---:|---|---|
| `requests==2.19.1` | 10 | GHSA-9hjg-9r4m-mvj7, GHSA-9wx4-h78v-vm56, GHSA-x84v-xcm2-53pg, PYSEC-2018-28 | No |
| `urllib3==1.24.1` | 26 | GHSA-2xpw-w6gg-jr37, GHSA-34jh-p97f-mpxf, GHSA-g4mx-q9vg-27p4, GHSA-gwvm-45gx-3cf8 | No |
| `PyYAML==5.3.1` | 2 | GHSA-8q59-q68h-6hv4, PYSEC-2021-142 (ejecución de código vía `full_load`) | Sí: `entrega.yaml`, `data_classification.yaml` |
| `Jinja2==2.11.2` | 10 | GHSA-cpwx-vrp4-4pq7, GHSA-g3rq-g295-4j3m, GHSA-h5c8-rqwp-cp95, PYSEC-2021-66 | No |
| `psycopg2-binary==2.9.9` | 0 | — | Sí |
| `pandas==2.2.3` | 0 | — | No (se usa `csv` de la stdlib para controlar el parseo hostil, ADR-003) |

**Qué se hizo** (`3509c47`, `requirements.txt` actual):

- Eliminadas `requests`, `urllib3`, `Jinja2` y `pandas`: ningún módulo las importa.
- `PyYAML` actualizado a `6.0.3` y usado **siempre** con `yaml.safe_load`.
- `psycopg2-binary` a `2.9.13`.
- Añadidas y pineadas las que sí se necesitan: `cryptography==50.0.2`
  (AES-GCM, HKDF), `python-dotenv==1.2.4`, `kafka-python==3.0.13`
  (`tools/producer.py` lo importa y no venía declarado), `pytest==9.2.0`,
  `pip-audit==2.10.1`.
- Resultado sobre el entorno nuevo: **`No known vulnerabilities found`**
  (`docs/evidencias/pip_audit_actual.txt`).

Los números de avisos son los de la consulta a OSV del 2026-11-11; una
auditoría posterior puede sumar avisos nuevos, por eso `make audit` existe.

---

## 8. Cómo verificar todo lo anterior

Con la infraestructura arriba (`make up`) y al menos una corrida
(`make run`). Las contraseñas están en `entrega.yaml`; abajo se toman de
`.env` para no repetirlas. Códigos de error esperados: `42501`
insufficient_privilege, `3F000` esquema invisible para el rol, `25006`
transacción de solo lectura.

```bash
set -a; source .env; set +a
PSQL_SU="postgresql://$PG_SUPERUSER:$PG_SUPERPASSWORD@localhost:$PG_PORT/$PG_DATABASE"
PSQL_IN="postgresql://rol_ingesta:$PG_ROL_INGESTA_PASSWORD@localhost:$PG_PORT/$PG_DATABASE"
PSQL_AN="postgresql://rol_analista:$PG_ROL_ANALISTA_PASSWORD@localhost:$PG_PORT/$PG_DATABASE"
PSQL_AU="postgresql://rol_auditor:$PG_ROL_AUDITOR_PASSWORD@localhost:$PG_PORT/$PG_DATABASE"
```

**Todo de una vez.** `make test` ejecuta 69 pruebas (45 funciones; las 33 de
permisos y las 2 de eliminatorias con datos reales necesitan la base y se
saltan sin ella); `make verificar` confirma que `entrega.yaml` describe lo que
`make up` levantó.

**C3 · Cifrado en reposo, leído como superusuario.** Debe verse `bytea` que
empieza por `\x01`, nunca texto:

```sql
-- psql "$PSQL_SU"
SELECT cliente_id, left(encode(nit_cifrado,'hex'),10) AS nit, left(encode(email_cifrado,'hex'),10) AS email,
       left(encode(observaciones_cifrado,'hex'),10) AS obs, observaciones_enmascaradas
  FROM core.dim_cliente ORDER BY cliente_id LIMIT 5;
SELECT factura_id, left(encode(cliente_nit_cifrado,'hex'),10), left(encode(cliente_email_cifrado,'hex'),10)
  FROM core.fact_facturacion LIMIT 5;
-- Ningún valor del maestro aparece como bytes dentro de las columnas cifradas
-- (sustituir <nit_solo_digitos> / <correo> por cualquier valor real de clientes.csv):
SELECT count(*) FROM core.dim_cliente
 WHERE position(convert_to('<nit_solo_digitos>','UTF8') IN nit_cifrado) > 0
    OR position(convert_to('<correo>','UTF8') IN email_cifrado) > 0;        -- esperado: 0
-- La cuarentena de fuentes con PII no guarda payload en claro:
SELECT origen, count(*) FILTER (WHERE payload IS NOT NULL) AS en_claro,
       count(*) FILTER (WHERE payload_cifrado IS NOT NULL) AS cifrado
  FROM quarantine.registros_rechazados GROUP BY origen ORDER BY origen;
-- esperado: clientes/facturacion con en_claro = 0
```

**C4 · `rol_analista`.** Lo permitido devuelve filas; cada línea prohibida
debe fallar:

```sql
-- psql "$PSQL_AN"
SELECT * FROM analytics.v_consumo_mensual LIMIT 3;          -- ok
SELECT * FROM analytics.v_cliente LIMIT 3;                  -- ok: sin NIT, correo ni HMAC
SELECT * FROM core.dim_cliente LIMIT 1;                     -- FALLA 42501 (sin USAGE en core)
SELECT * FROM core.fact_consumo_horario LIMIT 1;            -- FALLA 42501
SELECT * FROM quarantine.registros_rechazados LIMIT 1;      -- FALLA 42501
SELECT * FROM audit.bitacora LIMIT 1;                       -- FALLA 42501
INSERT INTO audit.bitacora (corrida_id, componente, etapa, evento, resultado)
  VALUES (gen_random_uuid(),'x','x','x','INFO');            -- FALLA 25006 / 42501
CREATE TABLE public.t (x int);                              -- FALLA 25006 (sesión de solo lectura)
```

**C4 · `rol_auditor`.**

```sql
-- psql "$PSQL_AU"
SELECT evento_id, ts, usuario_db, componente, etapa, evento, objeto, filas, resultado
  FROM audit.bitacora ORDER BY evento_id DESC LIMIT 10;      -- ok
SELECT * FROM core.dim_cliente LIMIT 1;                      -- FALLA 42501
SELECT * FROM analytics.v_consumo_mensual LIMIT 1;           -- FALLA 42501
UPDATE audit.bitacora SET resultado = 'OK' WHERE false;      -- FALLA 25006 / 42501
DELETE FROM audit.bitacora WHERE false;                      -- FALLA 25006 / 42501
```

**C4 · `rol_ingesta`.** Escribe el modelo pero no lee PII, ni siquiera cifrada:

```sql
-- psql "$PSQL_IN"
SELECT cliente_id, razon_social, pii_en_observaciones FROM core.dim_cliente LIMIT 3;   -- ok (columnas concedidas)
SELECT * FROM core.dim_cliente LIMIT 1;                      -- FALLA 42501 (columnas cifradas)
SELECT nit_cifrado FROM core.dim_cliente LIMIT 1;            -- FALLA 42501
SELECT cliente_email_cifrado FROM core.fact_facturacion LIMIT 1;   -- FALLA 42501
SELECT payload_cifrado FROM quarantine.registros_rechazados LIMIT 1;  -- FALLA 42501
SELECT * FROM analytics.v_consumo_mensual LIMIT 1;           -- FALLA 42501 (sin USAGE en analytics)
DELETE FROM core.fact_consumo_horario WHERE false;           -- FALLA 42501 (sin DELETE)
UPDATE audit.bitacora SET resultado = 'OK' WHERE false;      -- FALLA 42501
CREATE TABLE public.t (x int);                              -- FALLA 42501 (REVOKE ALL ON SCHEMA public FROM PUBLIC)
```

**C5 · Append-only, incluso en sesión de superusuario.** Las tres sentencias
deben fallar con `audit.bitacora es append-only`:

```sql
-- psql "$PSQL_SU"
UPDATE audit.bitacora SET resultado = 'ERROR' WHERE evento_id = (SELECT min(evento_id) FROM audit.bitacora);
DELETE FROM audit.bitacora WHERE evento_id = (SELECT min(evento_id) FROM audit.bitacora);
TRUNCATE audit.bitacora;
-- Privilegios: nadie, ni el dueño, conserva UPDATE/DELETE/TRUNCATE
SELECT grantee, privilege_type FROM information_schema.role_table_grants
 WHERE table_schema='audit' AND table_name='bitacora' AND privilege_type IN ('UPDATE','DELETE','TRUNCATE');
-- esperado: 0 filas
-- Roles sin atributos de sistema:
SELECT rolname FROM pg_roles WHERE rolname LIKE 'rol_%'
   AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls OR rolreplication);
-- esperado: 0 filas
```

**Eliminatoria 3 · La entrada hostil es dato.** `clientes.csv` se lee con
el módulo `csv` de la stdlib (encoding detectado, campos `TEXT`, longitud
máxima 4.000 caracteres por campo con cuarentena si se excede) y se escribe
solo con consultas parametrizadas (`%(campo)s` de psycopg2). La razón social
de `CLI0036` contiene `'); DROP TABLE core.fact_consumo_horario;--`, apuntando
al nombre real de la tabla; viaja como texto y queda almacenada tal cual:

```sql
-- psql "$PSQL_SU"
SELECT count(*) FROM core.fact_consumo_horario;              -- 114545 con el paquete entregado
SELECT cliente_id, position('DROP TABLE' in razon_social) > 0 AS contiene_inyeccion, length(razon_social)
  FROM core.dim_cliente WHERE cliente_id IN ('CLI0036','CLI0033');
-- esperado: CLI0036 true; CLI0033 longitud 1632 (no truncada)
```

Automatizado en `test_eliminatoria_3_maestro_hostil_no_toca_consumos`, que
recarga el `clientes.csv` real por el mismo camino que `make run` y comprueba
que la tabla de consumos existe y conserva su conteo.

**Eliminatoria 1 · Sin PII en logs ni salidas.** Con los valores reales del
maestro (no se incluyen aquí) y las regex genéricas:

```bash
grep -cE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' logs/pipeline.log out/*.json out/*.csv   # 0
grep -cE '\b3[0-9]{9}\b' logs/pipeline.log out/*.json                                             # 0
grep -cE '\bCC\s*[0-9]{6,10}\b' logs/pipeline.log out/*.json                                      # 0
python3 -c "import json,sys; [json.loads(l) for l in open('logs/pipeline.log')]; print('JSON ok')"
```

**C6 · Secretos.**

```bash
git ls-files | grep -E '^\.env$|\.pem$|\.key$'                 # vacío
grep -rnE 'minioadmin|hooks\.slack\.com|password:\s*postgres' --exclude-dir=.git --exclude-dir=.venv --exclude-dir=data . \
  | grep -vE 'CLAUDE.md|DECISIONS.md|SECURITY.md'              # vacío
grep -nE '\$\{[A-Z_]+:\?' docker-compose.yml                   # toda credencial exigida desde .env
```

**C7 · Dependencias.**

```bash
make audit            # pip-audit sobre el venv → "No known vulnerabilities found"
cat docs/evidencias/pip_audit_heredado_osv.txt
```
