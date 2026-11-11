# Modelo de amenazas — pipeline de consumos Bia Energy

STRIDE aplicado a la arquitectura que está en este repositorio, no a una
genérica. Cada amenaza nombra el componente real, el control que existe hoy,
dónde está la evidencia y en qué estado está. Lo que todavía no existe
(Terraform en `infra/`) aparece como **No implementado**, no como control.
Los riesgos residuales que ya conocemos están en §4 sin maquillar.

Convenciones: roles `rol_ingesta`, `rol_analista`, `rol_auditor`, superusuario
local `bia_admin`; esquemas `core`, `quarantine`, `audit`, `analytics`.
Los controles se describen en detalle en `SECURITY.md`; aquí se citan.

---

## 1. Alcance y diagrama de flujo de datos

Dentro del alcance: la ingesta batch (`make run`), el modelo en PostgreSQL,
los tres roles, la bitácora, los artefactos `out/` y `logs/`, los secretos en
`.env`, la infraestructura local de `docker-compose.yml` y el consumidor de
streaming (`make stream`, `src/bia/stream/`). La IaC de `infra/` se modela
como amenaza aunque todavía no existe.

```
            FRONTERA 1: entrada no confiable (archivos de terceros)
 ┌──────────────────────────────────────────────────────────────────────┐
 │ data/maestros/clientes.csv  (PII real, entradas hostiles: inyección, │
 │   1.632 chars, comillas anidadas)   fronteras.csv  limites_frontera.csv│
 │ data/consumos/*.csv (4 formatos, duplicados, inválidos, truncados)    │
 │ data/facturacion/*.json (PII duplicada, 12 fronteras huérfanas)       │
 │ data/eventos/mediciones_stream.ndjson → tools/producer.py             │
 └───────────────┬──────────────────────────────────────┬───────────────┘
                 │ lectura con csv/json (stdlib)        │ Kafka sin auth
                 ▼                                      ▼
 ┌─────────────────────────────────────┐   ┌──────────────────────────┐
 │ src/bia/pipeline.py  (`make run`)   │   │ Redpanda  mediciones.raw │
 │  ingesta/{archivos,consumos,        │   │ 127.0.0.1:9092 (compose) │
 │  maestros,facturacion,carga}.py     │   │ consumidor: bia.stream   │
 │  crypto.py (AES-GCM, HMAC)          │   │ (`make stream`)          │
 │  pii.py, logging_json.py            │   └────────────┬─────────────┘
 │  lee data_classification.yaml       │                │ alertas
 │  lee .env  ◄── FRONTERA 4: secretos │                ▼
 │  en el host (BIA_ENC_KEY, BIA_HMAC_KEY, contraseñas)  out/alertas.json│
 └───────┬───────────────────┬─────────┘
         │ como rol_ingesta  │ escribe
         │ (SQL parametrizado)│
         ▼                   ▼
 ┌────────────────────────┐  ┌──────────────────────────────────────────┐
 │ out/consumo_mensual.csv│  │ PostgreSQL 16 (127.0.0.1, scram-sha-256) │
 │ out/dq_report.json     │  │  core.dim_cliente        (PII cifrada)   │
 │ out/metrics.json       │  │  core.fact_facturacion   (PII cifrada)   │
 │ logs/pipeline.log      │  │  core.fact_consumo_horario               │
 │  FRONTERA 3: los lee   │  │  quarantine.registros_rechazados         │
 │  el evaluador/operador │  │  audit.bitacora          (append-only)   │
 └────────────────────────┘  │  analytics.v_*           (sin PII)       │
                             └───────┬──────────┬──────────┬───────────┘
          FRONTERA 2: privilegios    │          │          │
          por rol (sql/90_grants.sql)│          │          │
                                     ▼          ▼          ▼
                               rol_analista  rol_auditor  bia_admin
                               solo analytics solo audit  (DBA / dump)
```

Fronteras de confianza: (1) archivos externos → pipeline; (2) pipeline y
consumidores → PostgreSQL, mediada por roles; (3) artefactos `out/` y
`logs/` que salen del sistema; (4) `.env` y el sistema de archivos del host;
(5) red local de Redpanda, sin autenticación.

---

## 2. Activos y clasificación

| Activo | Dónde vive | Clasificación | Impacto si se compromete |
|---|---|---|---|
| NIT, correo, celular, dirección, representante legal y `observaciones` de 40 clientes | `data/maestros/clientes.csv` → `core.dim_cliente.*_cifrado`; copia en cuarentena `payload_cifrado` | Restringido-PII | Incumplimiento de Ley 1581 y de la eliminatoria 1 del enunciado; daño a personas naturales (representantes, cédulas en texto libre) |
| NIT y correo de facturación de 172 facturas | `data/facturacion/*.json` → `core.fact_facturacion.cliente_*_cifrado` | Restringido-PII | Igual que el anterior; es PII duplicada fuera del maestro y fácil de olvidar |
| Seudónimos HMAC (`*_hmac`) y `observaciones_enmascaradas` | `core.dim_cliente`, `core.fact_facturacion` | Confidencial | Con la llave HMAC permiten confirmar identidades; sin ella, correlación entre tablas |
| Llaves `BIA_ENC_KEY`, `BIA_HMAC_KEY` | `.env` del host (generadas por `scripts/bootstrap_env.py`) | Restringido | Descifrado de toda la PII en reposo |
| Contraseñas de `bia_admin` y los tres roles | `.env`, `entrega.yaml` (efímeras, públicas por requisito) | Interno en evaluación / Restringido en producción | Acceso directo a la base según el rol comprometido |
| Consumos horarios (114.545 filas) y agregados mensuales | `core.fact_consumo_horario`, `analytics.v_consumo_mensual`, `out/consumo_mensual.csv` | Interno | Facturación incorrecta; resultados del evaluador distintos a los esperados |
| Bitácora de corridas | `audit.bitacora` | Interno | Pérdida de trazabilidad: no se puede demostrar qué se cargó, cuándo ni por quién |
| Cuarentena (filas rechazadas con motivo) | `quarantine.registros_rechazados` | Interno / Restringido-PII en `payload_cifrado` | Pérdida de evidencia de calidad; exposición de PII si el payload estuviera en claro |
| Artefactos de salida y logs | `out/*.json`, `out/*.csv`, `logs/pipeline.log` | Interno | Son lo que cruza la frontera 3; cualquier PII aquí es eliminatoria |
| Código y DDL | `src/`, `sql/`, `Makefile`, `docker-compose.yml`, `.git/` | Interno | Manipulación del pipeline o de los grants en la entrega |
| Tópico `mediciones.raw` | Redpanda en compose | Interno | Alertas falsas o ausentes en `out/alertas.json` |

---

## 3. Amenazas (STRIDE)

Probabilidad e impacto en escala B/M/A, pensados para el escenario de
evaluación local y para un despliegue real cuando difieren.

| # | Componente | STRIDE | Amenaza concreta | Prob. | Imp. | Control implementado | ¿Implementado? | ISO 27001:2022 Anexo A |
|---|---|---|---|---|---|---|---|---|
| T1 | `clientes.csv` → `maestros.py` → `carga.py` → PostgreSQL | Tampering / Elevation of privilege | La razón social de `CLI0036` contiene `'); DROP TABLE core.fact_consumo_horario;--`, apuntando al nombre real de la tabla. Si el SQL se construyera por concatenación, la tabla de consumos se vaciaría (eliminatoria 3). `CLI0033` (1.632 chars) y `CLI0013` (comillas y salto de línea) buscan desbordar o romper el parser | A (el dato está en el paquete) | A | Parseo con el módulo `csv` de la stdlib, encoding detectado; columnas `TEXT`; longitud máxima 4.000 chars por campo con cuarentena `CAMPO_EXCESIVO`; **todas** las escrituras con parámetros de psycopg2 (`%(campo)s`), ninguna f-string con datos. `rol_ingesta` no tiene `DROP` ni `DELETE` sobre `core` | **Sí** — `src/bia/ingesta/maestros.py`, `src/bia/ingesta/carga.py`; `tests/test_eliminatorias_db.py::test_eliminatoria_3_maestro_hostil_no_toca_consumos` (conteo antes/después con el archivo real); `sql/90_grants.sql` | A.8.28 Codificación segura; A.8.26 Requisitos de seguridad de aplicaciones; A.8.29 Pruebas de seguridad |
| T2 | `core.dim_cliente`, `core.fact_facturacion` ← `bia_admin` / dump de `pgdata` | Information disclosure | Un DBA, un backup robado o el evaluador leyendo la tabla cruda como superusuario obtienen NIT, correos, celulares, direcciones, representantes y observaciones | A (es exactamente la prueba C3) | A | Cifrado a nivel de aplicación AES-256-GCM por valor, nonce aleatorio, byte de versión; llave derivada con HKDF desde `BIA_ENC_KEY`, que no existe en la base. Cubre también la PII duplicada en facturación y el payload de cuarentena. Igualdad y joins solo por HMAC del valor normalizado | **Sí** — `src/bia/crypto.py`; `tests/test_permisos_db.py::test_superusuario_no_ve_pii_en_claro` (lee como `bia_admin` y busca los valores reales); `test_pii_fisica_siempre_cifrada_en_bytea`; `SECURITY.md` §2 | A.8.24 Uso de criptografía; A.5.34 Privacidad y protección de PII; A.8.11 Enmascaramiento de datos |
| T3 | `.env` en el host ↔ PostgreSQL en el mismo host | Information disclosure | Quien tenga acceso al sistema de archivos de la máquina (no solo a la base) lee `BIA_ENC_KEY` y `BIA_HMAC_KEY` y descifra todo T2. En la evaluación local, base y llave conviven | M | A | Llaves generadas por máquina, nunca versionadas (`.gitignore`), nunca en `entrega.yaml`, separadas de las credenciales de la base; el superusuario **de Postgres** no las conoce. No hay separación física ni KMS | **Parcial** — `scripts/bootstrap_env.py`, `.gitignore`, `tests/test_bootstrap_env.py::test_plantilla_real_no_trae_llaves_ni_env_versionado`. Riesgo aceptado §4 | A.8.24; A.5.17 Información de autenticación; A.8.9 Gestión de la configuración |
| T4 | `logs/pipeline.log`, `out/*.json`, `out/*.csv` ← `logging_json.py`, `reportes.py` | Information disclosure | Un `log()` con la fila cruda, un motivo de cuarentena con el valor rechazado o un `metrics.json` con nombres de cliente filtran PII fuera de la base (eliminatoria 1). El evaluador lo busca con regex de correos, celulares y cédulas reales | M (es el error más fácil de cometer) | A | Doble filtro en el logger: redacción por nombre de campo según `data_classification.yaml` (`en_logs: false`) y enmascarado por regex de **toda** cadena; el pipeline solo loguea ids, conteos, reglas y motivos genéricos; los reportes no contienen columnas de cliente | **Sí** — `src/bia/logging_json.py`, `src/bia/pii.py`; `tests/test_pii_crypto.py::test_logger_json_sin_pii`; prueba con los valores reales del maestro sobre `logs/` y `out/` documentada en `SECURITY.md` §4 y §8 (cero coincidencias) | A.8.15 Registro de eventos; A.8.11; A.8.12 Prevención de fuga de datos |
| T5 | `rol_analista`, `rol_auditor`, `rol_ingesta` ↔ esquemas `core`/`quarantine`/`audit`/`analytics` | Elevation of privilege | El analista lee `core.dim_cliente` o la cuarentena, o escribe; el auditor lee algo más que la bitácora; `rol_ingesta` lee las columnas cifradas y se convierte en un canal de exfiltración; cualquiera crea objetos en `public` | A (el evaluador lo intenta con cada rol) | A | `REVOKE ALL ON SCHEMA public FROM PUBLIC` y sobre la base; roles `NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS`; `rol_analista` solo `USAGE`+`SELECT` en `analytics` y transacciones de solo lectura; `rol_auditor` solo `audit.bitacora`; `rol_ingesta` con `SELECT` **por columna** que excluye todo `*_cifrado` y `payload*`; `verificar_vistas_analista` aborta `make run` si una vista expone un nivel prohibido | **Sí** — `sql/00_roles.sql`, `sql/01_schemas.sql`, `sql/90_grants.sql`, `sql/40_analytics.sql`, `src/bia/classification.py`; `tests/test_permisos_db.py` (33 casos negativos y positivos por rol) | A.5.15 Control de acceso; A.5.18 Derechos de acceso; A.8.2 Derechos de acceso privilegiado; A.8.3 Restricción de acceso a la información |
| T6 | `audit.bitacora` | Repudiation / Tampering | Alguien que cargó datos incorrectos (o un atacante que manipuló `fact_consumo_horario`) borra o edita los eventos de su corrida para que no quede rastro de qué archivo, cuándo y con qué resultado | M | A | Bitácora con `corrida_id`, `usuario_db`, `usuario_os`, `host_origen`, objeto, filas y resultado por evento. Append-only en dos capas: `REVOKE UPDATE, DELETE, TRUNCATE` a `PUBLIC`, a los tres roles **y al dueño**; triggers `BEFORE UPDATE OR DELETE` y `BEFORE TRUNCATE` que abortan con `insufficient_privilege`. `rol_ingesta` solo `INSERT` | **Parcial** — `sql/30_audit.sql`, `sql/90_grants.sql`; `tests/test_permisos_db.py::test_bitacora_append_only_en_sesion_normal_de_superusuario` y `::test_privilegios_bitacora_revocados_hasta_para_el_duenio`. Parcial porque un superusuario administrativo puede desactivar los triggers (§4) y porque en la evaluación todos los `make run` usan la misma identidad `rol_ingesta` (no hay no-repudio por persona) | A.8.15 Registro de eventos; A.5.33 Protección de registros; A.8.16 Actividades de seguimiento |
| T7 | `bia_admin` ↔ `audit.bitacora` (triggers) | Tampering | Un superusuario con capacidad administrativa ejecuta `ALTER TABLE audit.bitacora DISABLE TRIGGER ALL` o `SET session_replication_role = replica` y entonces edita o borra la bitácora sin que nada lo impida. PostgreSQL no ofrece inmutabilidad frente a su propio superusuario | B en evaluación (solo el evaluador tiene `bia_admin`) / M en producción | A | Ninguno dentro de la base que lo impida de verdad. Lo que existe: el pipeline **nunca** corre como superusuario (`db.conectar(cfg.dsn_rol("ingesta"))` en `pipeline.py`); `bia_admin` solo se usa en `scripts/bootstrap_db.py` para aplicar DDL; la propia bitácora deja constancia del DDL aplicado en cada `make up` | **No (riesgo aceptado)** — verificado manualmente que ambas sentencias funcionan; documentado en `SECURITY.md` §6, ADR-004 y §4 de este documento. Mitigación de producción en §5 | A.8.15; A.5.33; A.8.2 |
| T8 | `data/consumos/*.csv` → `consumos.py` → `core.fact_consumo_horario` | Tampering / Denial of service | Un archivo truncado, con basura binaria o con cabecera irreconocible aborta la corrida a mitad y deja meses a medio cargar; o un reenvío corrupto de un mes ya cargado lo modifica (el evaluador mete exactamente eso) | A (está anunciado en el README) | A | Transacción por archivo con `rollback` ante excepción; `core.control_archivos` con `UNIQUE(nombre, hash_sha256)` para no recargar lo ya cargado; cabecera ilegible o > 50 % de filas malformadas ⇒ `ARCHIVO_CORRUPTO`, archivo entero rechazado; bytes NUL ⇒ `FILA_CORRUPTA`; todo rechazo a cuarentena con motivo; el `dq_report` se recalcula de los archivos presentes, la base solo recibe archivos nuevos | **Sí** — `src/bia/pipeline.py`, `src/bia/ingesta/archivos.py`, `src/bia/ingesta/consumos.py`, `sql/10_core.sql`; `tests/test_consumos_reglas.py` (`test_cabecera_irreconocible_rechaza_archivo_entero`, `test_bytes_nulos_se_rechazan_sin_perder_lo_anterior`, `test_filas_malformadas_van_a_rechazo_y_no_detienen_el_archivo`); escenario manual con mes nuevo + archivo truncado + binario: meses previos con hash idéntico (ADR-005) | A.8.9 Gestión de la configuración; A.8.29 Pruebas de seguridad; A.8.6 Gestión de la capacidad |
| T9 | `requirements.txt`, `docker-compose.yml`, PyYAML ← `entrega.yaml`, `data_classification.yaml` | Elevation of privilege (cadena de suministro) | El scaffolding traía `requests 2.19.1`, `urllib3 1.24.1`, `Jinja2 2.11.2` y `PyYAML 5.3.1` con CVE conocidos; `PyYAML.load` sin `Loader` permite ejecución de código desde un YAML que el pipeline lee al arrancar. Imágenes `:latest` cambian bajo los pies | M | A | Dependencias no usadas eliminadas; `PyYAML 6.0.3` y **siempre** `yaml.safe_load`; todo pineado; `pip-audit 2.10.1` limpio con evidencia; imágenes `postgres:16.9-alpine` y `redpandadata/redpanda:v26.2.6` fijas, `no-new-privileges` en ambos contenedores | **Sí** — `requirements.txt`, `docs/evidencias/pip_audit_heredado_osv.txt` (48 avisos en los cuatro paquetes heredados, con IDs), `docs/evidencias/pip_audit_actual.txt` (`No known vulnerabilities found`), `make audit`, `docker-compose.yml` | A.8.8 Gestión de vulnerabilidades técnicas; A.5.21 Seguridad en la cadena de suministro TIC; A.8.19 Instalación de software en sistemas en producción |
| T10 | `config/pipeline.yaml` (heredado), `docker-compose.yml`, `.git/` | Information disclosure | Credenciales de MinIO, contraseña de Postgres, una "llave maestra" y un webhook de Slack venían en claro en el scaffolding; `postgres/postgres` y los defaults de MinIO en compose. Entregar el zip con `.git/` los publicaría para siempre (eliminatoria 2) | A (venían en el paquete) | A | Valores redactados antes del primer commit (`b66f2f2`): no están en ningún punto del historial; `config/pipeline.yaml` eliminado en `3509c47`; MinIO eliminado (ADR-001); compose exige cada credencial con `${VAR:?}`; `.env` ignorado por git; `config.cargar()` no imprime secretos | **Sí** — `git log --all -p` sin los valores (verificado); `SECURITY.md` §5; `tests/test_bootstrap_env.py` | A.5.17 Información de autenticación; A.8.4 Acceso al código fuente; A.8.9 |
| T11 | PostgreSQL en `127.0.0.1:5432` ↔ cualquier proceso del host | Spoofing | Un proceso local se conecta como `rol_ingesta` o `bia_admin` con las credenciales efímeras de `entrega.yaml`, que son públicas por requisito del enunciado, y carga datos falsos o lee la bitácora | M en evaluación / A si se reutilizaran en otro entorno | M | Puerto publicado solo en `127.0.0.1`; `scram-sha-256` para conexiones locales y de red (`POSTGRES_INITDB_ARGS`); roles sin atributos de sistema y con `statement_timeout`; contraseñas inyectadas por `set_config`, no en el DDL; credenciales declaradas efímeras y exclusivas de evaluación (ADR-002) | **Parcial** — `docker-compose.yml`, `sql/00_roles.sql`, `scripts/bootstrap_db.py`; `tests/test_permisos_db.py::test_roles_sin_privilegios_de_sistema`. Parcial porque las contraseñas son conocidas por diseño (§4) | A.8.5 Autenticación segura; A.5.16 Gestión de identidad; A.8.20 Seguridad de redes |
| T12 | Redpanda `mediciones.raw` ← `tools/producer.py` / consumidor de `make stream` | Spoofing / Tampering | Cualquier proceso con acceso a `localhost:9092` publica eventos inventados o reordenados: alertas `CONSUMO_FUERA_DE_BANDA` falsas, `FRONTERA_SIN_REPORTE` ocultadas, o un `ts_medicion` manipulado. El tópico no autentica productores ni cifra en tránsito | M | M | Aislamiento de compose: listener externo publicado solo en `127.0.0.1`, imagen pineada, `no-new-privileges`. El consumidor trata cada mensaje como entrada no confiable: JSON ilegible ⇒ `EVENTO_MALFORMADO`; evento sin `evento_id`, frontera con formato inválido o sin límites, `ts_medicion` ilegible o `kwh` no numérico ⇒ rechazado con motivo, sin contar como medición; reentregas deduplicadas por `evento_id` para que releer el tópico no fabrique `LECTURA_RETROACTIVA`. **No** hay SASL, TLS ni ACLs: un proceso local con acceso a `9092` puede publicar eventos bien formados pero falsos, y el consumidor no puede distinguirlos | **Parcial** — `src/bia/stream/alertas.py` (`_validar`, deduplicación), `src/bia/stream/consumidor.py`; `tests/test_stream_alertas.py` (`test_eventos_invalidos_se_rechazan_sin_detener_ni_contar_como_medicion`, `test_reentrega_del_mismo_evento_no_es_retroactiva`); `docker-compose.yml`. Sin autenticación del productor (§4) | A.8.20 Seguridad de redes; A.8.21 Seguridad de los servicios de red; A.5.16 |
| T13 | `make stream`, `make run` ↔ CPU/memoria/tiempo del evaluador | Denial of service | Un consumidor que nunca termina agota el timeout de 300 s y cuenta como fallo; un CSV con una fila gigantesca o un `clientes.csv` con campos de megabytes agota memoria en el parseo; una consulta lenta bloquea la corrida de 900 s | M | M | Batch: `LONGITUD_MAXIMA = 4000` por campo en maestros, umbral de rechazo estructural, `statement_timeout = 120s` por rol, parseo por filas (sin cargar el CSV en pandas). Streaming: condición de fin explícita (lee hasta el offset final que tenía el tópico cuando el productor terminó), más fin por inactividad (20 s) y plazo total (240 s), ambos por debajo de los 300 s del contrato; asignación manual de particiones sin grupo, así que no espera rebalanceos | **Sí** — `src/bia/ingesta/maestros.py`, `src/bia/pipeline.py` (`UMBRAL_RECHAZO_ESTRUCTURAL`), `sql/00_roles.sql`, `src/bia/stream/consumidor.py` (`INACTIVIDAD_S`, `PLAZO_TOTAL_S`); `make stream` probado contra un broker Kafka local: termina en ~2 s (ADR-006) | A.8.6 Gestión de la capacidad; A.8.26 |
| T14 | `quarantine.registros_rechazados` | Information disclosure | La cuarentena guarda la fila original para que el rechazo sea consultable; para clientes y facturación esa fila es PII completa. Un `SELECT payload` por un rol de soporte, o el analista vía una vista de calidad, la expondría | M | A | Para orígenes con PII el `payload` en claro se guarda `NULL` y la fila va a `payload_cifrado` (AES-GCM); `rol_ingesta` tiene `SELECT` solo sobre los metadatos (ni `payload` ni `payload_cifrado`); `rol_analista` no tiene acceso al esquema; la vista `analytics.v_cuarentena_resumen` agrega por regla sin payload | **Sí** — `src/bia/ingesta/carga.py` (`ORIGENES_CON_PII`), `sql/20_quarantine.sql`, `sql/90_grants.sql`, `sql/40_analytics.sql`; consulta de verificación en `SECURITY.md` §8 (clientes/facturación: 0 payloads en claro) | A.8.11; A.8.24; A.8.3 |

---

## 4. Riesgos aceptados

1. **El superusuario de PostgreSQL puede desactivar los triggers de la
   bitácora** (T7). Argumento: no existe control dentro del motor que resista
   a su propio superusuario; añadir más triggers o reglas sería teatro. Lo que
   sí se hace es no operar nunca la aplicación como superusuario y dejar el
   límite escrito en `SECURITY.md` §6 y ADR-004. En producción se mitiga
   fuera de la base (§5).
2. **La llave de cifrado vive en `.env`, en el mismo host que PostgreSQL**
   (T3). Argumento: el enunciado pide una solución 100 % local sin cuenta de
   nube; un KMS real no es ejecutable por el evaluador. Se preserva lo
   esencial del control (la llave no está en la base ni en el repo, y el
   superusuario de Postgres no la tiene) y se deja la separación física para
   `infra/` y §5.
3. **Las credenciales de `bia_admin` y los roles son públicas** en
   `entrega.yaml` (T11). Argumento: el enunciado las exige para calificar C3 a
   C5; son efímeras, válidas solo para un Postgres en `127.0.0.1` y
   documentadas como comprometidas (ADR-002). Jamás reutilizarlas.
4. **El HMAC es determinístico**: dos filas con el mismo NIT comparten
   seudónimo, y quien tenga `BIA_HMAC_KEY` puede confirmar un NIT por fuerza
   bruta. Es el precio de poder hacer joins y detectar duplicados sin el
   valor; es seudonimización, no anonimización.
5. **La detección de PII en texto libre es por patrón** (`src/bia/pii.py`):
   una cédula sin prefijo y con menos de 7 dígitos o un correo malformado no
   se reconocen. Por eso el texto original se cifra completo y la versión
   enmascarada se trata como Confidencial, no como pública.
6. **Un archivo posterior en orden lexicográfico con la misma
   `version_lectura` y distinto valor sobrescribe la lectura cargada** (T8).
   Es exactamente la regla B3 del enunciado; se acepta porque es el contrato,
   y queda rastro en cuarentena (`DUPLICADO_CONFLICTIVO`) y en
   `control_archivos`.
7. **No hay no-repudio por persona** (T6): en la evaluación todos los
   `make run` se identifican como `rol_ingesta`. La bitácora registra
   `usuario_os` y `host_origen`, que es lo máximo que permite un entorno
   local sin identidades individuales.
8. **Redpanda sin autenticación ni TLS en la evaluación local** (T12). Se
   acepta mientras el listener esté solo en `127.0.0.1`. El consumidor valida
   y deduplica cada evento, pero no puede detectar uno falso bien formado.

---

## 5. Qué haría distinto con más tiempo / en producción real

- **Llaves**: envelope encryption con una llave de datos cifrada por un KMS
  (AWS KMS con política de uso restringida a la identidad del pipeline), el
  byte de versión en `crypto.py` ya permite rotar sin recifrar todo de golpe.
  Es lo que debe modelar `infra/` (Terraform, Hito 3, solo `validate`).
- **Bitácora**: además del append-only en la base, enviar cada evento a un
  destino que PostgreSQL no controle (S3 con Object Lock, o un sink de logs
  inmutable) y activar `pgaudit` para registrar DDL y cambios de triggers;
  separar la identidad DBA de la de aplicación y alertar sobre
  `ALTER TABLE ... DISABLE TRIGGER` y `session_replication_role`.
- **Identidad**: credenciales por despliegue desde un gestor de secretos
  (Vault/Secrets Manager) con rotación; roles por persona o por servicio con
  `SET ROLE`, para que `usuario_db` identifique a alguien.
- **Streaming**: SASL/SCRAM y TLS en Redpanda, ACLs por tópico (el productor
  solo escribe, el consumidor solo lee), firma o esquema registrado por
  evento, y un consumidor continuo con grupo y offsets confirmados en lugar
  del modo "leer el día y terminar" que exige `make stream`.
- **Red**: PostgreSQL y Redpanda en una red privada; nada publicado en el
  host; el pipeline como servicio con identidad propia.
- **Ciclo de vida**: `pip-audit` y `terraform validate` en CI en cada commit;
  escaneo de imágenes; entornos separados de desarrollo y producción
  (A.8.31); pruebas de permisos (`tests/test_permisos_db.py`) como puerta de
  despliegue.
- **PII**: Row-Level Security para los pocos casos en que un analista
  necesite ver un cliente concreto, y un catálogo que genere
  `data_classification.yaml` desde metadatos en lugar de mantenerlo a mano.
