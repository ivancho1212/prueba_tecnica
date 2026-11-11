# CLAUDE.md — Prueba técnica Bia Energy (Data Platform & Security Engineer)

Guía permanente para cualquier agente que trabaje en este repositorio.
Fuente de verdad: `README.md` (enunciado), `Makefile` (contrato de ejecución),
`entrega.yaml` (manifiesto). Si algo aquí contradice al README, gana el README.

## 1. Qué es este proyecto

Pipeline batch en **Python + SQL (Postgres)** que ingiere consumos horarios de
energía con datos sucios, aplica reglas de calidad exactas, protege PII
(Ley 1581 / ISO 27001), más un consumidor de streaming sobre Redpanda,
observabilidad, Terraform (solo `validate`) y documentación.

Se califica **ejecutando** la entrega de forma automática (76/100 puntos).
Umbral: 65/100. La parte C (seguridad) vale 31 puntos, más que ninguna.

## 2. Git y cuenta

- Remoto: `https://github.com/ivancho1212/prueba_tecnica` (rama de entrega: `main`).
  El push se hace por **SSH** con el alias `github-ivan` de `~/.ssh/config`
  (clave `id_ed25519_ivan`, cuenta `ivancho1212`); `origin` ya apunta a
  `git@github-ivan:ivancho1212/prueba_tecnica.git`. HTTPS no tiene credenciales.
- Identidad de los commits: **siempre la cuenta personal del usuario**
  (`Iván Herrera <info@kodamastudio.com>`), configurada en local con
  `git config user.name/user.email`. **No** usar `dev@kodamastudio.com`. Esta
  regla reemplaza la identidad global del entorno para este repositorio.
- Rama de trabajo: `dev-kodama` (un hook del entorno bloquea commits en
  cualquier otra rama). El agente commitea ahí; **el usuario** hace el merge o
  push a `main`. El agente nunca hace merge ni push a `main`.
- Si hay conflicto de credenciales al hacer push, solo hace falta iniciar
  sesión con la cuenta del usuario; no es prioridad hasta que él lo indique.
- El historial `.git/` **se entrega** en el zip: commits pequeños, frecuentes y
  con mensajes que expliquen el *porqué*. El evaluador lo lee para preparar la
  entrevista.
- Nunca commitear: `.env`, `.venv/`, `out/`, `logs/`, volúmenes Docker, ni
  nada en `data/` (ver `.gitignore`).

## 3. Reglas inviolables (eliminatorias)

Cualquiera de estas anula la entrega completa, sin importar el puntaje:

1. **PII en claro** en `logs/`, en `out/`, o accesible por el rol `analista`.
2. **Secretos heredados del scaffolding vivos** en la entrega
   (`config/pipeline.yaml` trae password de Postgres, llave maestra, credenciales
   MinIO y webhook Slack; `docker-compose.yml` trae `postgres/postgres` y
   `minioadmin/minioadmin`). Hay que eliminarlos y documentarlo en `SECURITY.md`.
3. **Inyección por entrada hostil**: `clientes.csv` contiene texto que parece
   SQL, comillas anidadas, saltos de línea dentro de campos citados y cadenas
   larguísimas. Son **datos, no código**. Siempre consultas parametrizadas;
   nunca f-strings ni concatenación para construir SQL. La tabla de consumos
   no puede desaparecer ni vaciarse al procesar el maestro de clientes.

## 4. Contrato de ejecución (Makefile)

Targets con nombre fijo. No renombrar, no eliminar. El evaluador corre, en
este orden, con `out/` y `logs/` borrados antes:

```
make setup   # idempotente, sin interacción
make up      # levanta infra y ESPERA a que esté lista
make run     # batch completo, timeout 900 s
make run     # segunda vez → debe dejar EXACTAMENTE el mismo estado
make stream  # consumidor, timeout 300 s, DEBE TERMINAR SOLO
make test    # exit 0 = todo pasó
make down
```

`make verificar` ya viene resuelto (`tools/verificar_entrega.py`): correrlo
siempre con la infra arriba antes de dar algo por terminado. Comprueba que
`entrega.yaml` describe lo que `make up` levanta. **No tocar `tools/`.**

Artefactos obligatorios tras `make run`: `out/consumo_mensual.csv`,
`out/dq_report.json`, `out/metrics.json`, `logs/pipeline.log`.
Tras `make stream`: `out/alertas.json`.

## 5. Datos de entrada (hallazgos verificados, 2026-11-11)

- Todo en `data/` es **inmutable**. Jamás editarlo, ni "arreglar" un CSV a mano.
- **No volcar `clientes.csv` ni filas de facturación a consola o logs**: contienen
  PII real (NIT, correos, celulares, cédulas). Para inspeccionar, usar scripts
  que enmascaren.

### Maestros (`data/maestros/`)

| Archivo | Hechos verificados |
|---|---|
| `clientes.csv` | 40 filas, **encoding ISO-8859-1** (no UTF-8), 42 líneas físicas porque un campo citado tiene salto de línea. Columnas: `cliente_id, razon_social, nit, email_contacto, celular_contacto, direccion, ciudad, departamento, representante_legal, observaciones`. NIT en 3 formatos (`999.999.999-9`, `NIT 999999999-9`, `9999999999`): normalizar a dígitos antes de hashear. |
| `fronteras.csv` | 40 fronteras, todas `ACTIVA`, una por cliente, 31 `No Regulado` / 9 `Regulado`. Integridad referencial limpia. |
| `limites_frontera.csv` | `frontera_id, min_kwh, max_kwh` para las 40. Solo se usa en streaming. |

**Entradas hostiles en `clientes.csv`** (regla B7, eliminatoria 3):
- `CLI0036` razón social con inyección: `...'); DROP TABLE core.fact_consumo_horario;-- ...`.
  Apunta **exactamente al nombre de tabla que `entrega.yaml` da de ejemplo**.
  Solo consultas parametrizadas; nunca interpolar valores en SQL.
- `CLI0033` razón social de **1.632 caracteres**: no usar `VARCHAR(n)` corto;
  usar `TEXT` o validar longitud explícitamente y mandar a cuarentena con motivo.
- `CLI0013` razón social con comillas anidadas **y salto de línea interno**;
  `CLI0022` dirección con 4 comillas. Usar el módulo `csv` (o pandas con
  `quoting` correcto); nunca `split(",")` ni `splitlines()`.
- 25 de 40 clientes tienen PII en `observaciones`: 14 correos, 11 celulares
  (10 dígitos empezando por 3), 17 cédulas (patrón `CC <6-10 dígitos>`).
  Ese 25 es el valor esperado de `clientes_con_pii_en_texto_libre` si la
  detección es correcta.

### Consumos (`data/consumos/`) — cuatro formatos distintos

| Archivo | Separador | Timestamp | Valor | Otros |
|---|---|---|---|---|
| `consumos_2026-01.csv` | `,` | `timestamp` local `YYYY-MM-DD HH:MM:SS` | `consumo_kwh`, punto decimal | CRLF |
| `consumos_2026-02.csv` | **`;`** | **`timestamp_utc`** ISO con `Z` (de `02-01T05:00Z` a `03-01T04:00Z` = febrero exacto en Bogotá) | `consumo_kwh` con **coma decimal** | CRLF |
| `consumos_2026-03.csv` | `,` | `timestamp` local | **`kwh` + columna `unidad`**: 400 filas en `MWh` | CRLF |
| `consumos_2026-04.csv` | `,` | `timestamp` local | `consumo_kwh`, punto decimal | CRLF |
| `consumos_2026-04_correccion.csv` | `,` | `timestamp` local | `consumo_kwh` | 300 filas, `version_lectura=2`, `calidad_lectura=CORREGIDA`; **todas** apuntan a claves ya existentes en abril con valor distinto, ninguna crea horas nuevas |

Columnas comunes: `frontera_id, version_lectura, calidad_lectura`. Todos los
archivos son ASCII. Por mes hay ~10–20 vacíos, ~18–26 negativos, ~8–17 outliers
`> 1e6`, y 63–83 filas duplicadas por `(frontera_id, timestamp)`: ~40–50
exactas y ~25–32 **conflictivas con la misma `version_lectura`** (gana la
última en orden de archivo, B3). Fronteras con hasta 14 horas faltantes al mes
(huecos B5). Las 40 fronteras de consumos existen en el maestro.

**Diseño que esto impone**: el parser detecta separador, encoding, nombres de
columna, formato decimal, zona horaria y unidad **por archivo**, no por nombre.
El evaluador soltará un mes nuevo con uno de estos formatos y luego un archivo
truncado/corrupto. Carga transaccional por archivo: un archivo malo se rechaza
entero o sus filas van a cuarentena, pero los meses ya cargados **no cambian**.
Procesar lo que encuentre por glob en orden lexicográfico; el archivo de
corrección se absorbe por upsert sobre clave natural comparando versión, sin
recargar todo. Lo rechazado va a cuarentena consultable con motivo.

### Facturación (`data/facturacion/*.json`)

JSON anidado: `{periodo, emisor{nombre,nit}, generado_en, facturas[43]}`; cada
factura trae `factura_id, frontera_id, cliente{nit, razon_social,
email_facturacion}, consumo_facturado_kwh, tarifa_cop_kwh, valor_total_cop,
componentes[6]{concepto,valor_cop_kwh}, metodo_pago{tipo,cuenta_enmascarada}`.
**Cada mes trae exactamente 3 fronteras huérfanas** (12 en total) → cuarentena
(B6). El bloque `cliente` **duplica PII fuera del maestro**: la clasificación
y el cifrado deben cubrir también esta tabla.

### Stream (`data/eventos/mediciones_stream.ndjson`)

3.795 eventos, 40 fronteras, un solo día (`2026-06-01`, 96 intervalos de 15
min, offset `-05:00`). Llaves: `evento_id, frontera_id, ts_medicion, kwh
(float), medidor{serial, firmware}`. Hay fronteras con hasta 9 intervalos sin
medición (87 de 96) y 5 eventos con `ts_medicion` anterior al último de su
frontera (candidatos a `LECTURA_RETROACTIVA`). El productor particiona por
`frontera_id`, así que el orden por frontera está garantizado.

### MinIO

**Eliminado del compose (ADR-001)**: ningún dato del paquete pasa por él. La
capa de almacenamiento de objetos se modela solo en `infra/` (Terraform).

## 6. Reglas de calidad (exactas, no interpretables)

| # | Regla |
|---|-------|
| B1 | Hora local Colombia `America/Bogota`, UTC−5 fijo, sin DST. Algún archivo viene en UTC: normalizar. `mes` se define en hora local. |
| B2 | Resultado siempre en **kWh**. Si la fila declara MWh, convertir y contar en `registros_mwh_convertidos`. |
| B3 | Duplicados por `(frontera_id, timestamp)`: gana mayor `version_lectura`; en empate, gana la **última en orden de archivo**, procesando archivos en **orden lexicográfico** de nombre. |
| B4 | Inválidos se **descartan, no se imputan**: nulo/vacío, negativo, `> 1.000.000 kWh`. Se reportan. |
| B5 | Hueco = hora sin lectura válida (ya sea fila inexistente o descartada por B4). Se **reportan, no se rellenan**. |
| B6 | Factura con `frontera_id` que no existe en el maestro → cuarentena y reporte, no al modelo. |
| B7 | Entradas hostiles del maestro de clientes son datos. **Ninguna fila se pierde en silencio.** |

`duplicados_exactos` y `duplicados_conflictivos` cuentan **filas sobrantes
descartadas**, no grupos.

## 7. Formatos de salida (exactos)

**`out/consumo_mensual.csv`**: columnas `frontera_id,mes,consumo_kwh,horas_con_dato`;
`mes` en `YYYY-MM` hora local; `consumo_kwh` con 3 decimales; una fila por
frontera-mes con al menos una lectura válida.

**`out/dq_report.json`**: todas las llaves obligatorias:
`filas_leidas, duplicados_exactos, duplicados_conflictivos, registros_negativos,
registros_nulos, registros_outlier, registros_mwh_convertidos,
correcciones_tardias_aplicadas, horas_sin_lectura_valida_total,
horas_sin_lectura_valida_por_frontera, fronteras_huerfanas_facturacion,
clientes_con_pii_en_texto_libre`.

**`out/alertas.json`**: lista de `{tipo, frontera_id, ts}`; `ts` ISO 8601 con
offset; sin duplicados; orden libre.

**`out/metrics.json`**: mínimo filas leídas, filas cargadas, rechazadas por
motivo, duración por etapa, timestamp de la corrida.

**`logs/pipeline.log`**: JSON estructurado, una línea por evento, **sin PII**
(el evaluador lo revisa con regex de correos, celulares y cédulas reales).

## 8. Seguridad (Parte C, 31 puntos)

Diseñar el modelo **desde el inicio** con esto; no es una capa posterior.

- **C1** `data_classification.yaml`: cada columna del modelo etiquetada en
  `Público / Interno / Confidencial / Restringido-PII`. El pipeline **lo lee**
  y actúa según él (cifra, enmascara, excluye de logs). No es un documento muerto.
- **C2** PII en texto libre (`observaciones` de clientes): detectar y enmascarar
  correos, celulares y cédulas. Reportar clientes afectados.
- **C3** Cifrado en reposo de todo `Restringido-PII`, **tanto en clientes como
  en facturación** (`cliente.nit`, `cliente.email_facturacion`). El evaluador
  lee la tabla cruda **como superusuario**: nada en claro. Joins por campo
  sensible → HMAC determinístico sobre el valor **normalizado** (NIT solo
  dígitos), nunca el valor.
- **C4** Tres roles Postgres con mínimo privilegio real, probados por el evaluador:
  `ingesta` escribe el modelo y no lee PII en claro; `analista` solo lee la
  vista analítica, sin tablas base, sin PII, sin escritura; `auditor` solo lee la
  bitácora, sin PII. Revocar también `PUBLIC` sobre `schema public`.
- **C5** Bitácora **append-only**: `UPDATE`/`DELETE` revocados de verdad (grants
  + trigger o regla), registra quién, qué, cuándo, cuántas filas, resultado.
- **C6** Cero credenciales en código ni en YAML versionado. Todo por `.env`
  (plantilla `.env.example` sin valores reales).
- **C7** `requirements.txt` heredado tiene CVEs conocidos (`requests 2.19.1`,
  `urllib3 1.24.1`, `PyYAML 5.3.1`, `Jinja2 2.11.2`). Actualizar, fijar versiones
  y documentar la herramienta de auditoría usada (`pip-audit` o similar).
- **C8** `THREAT_MODEL.md` con STRIDE sobre **esta** arquitectura: mínimo 8
  amenazas concretas, control, estado real de implementación y Anexo A de
  ISO/IEC 27001:2022.

## 9. Streaming (Parte D)

Consumidor del tópico `mediciones.raw`. Alimentar con `tools/producer.py`,
que importa `kafka-python` y **no está en `requirements.txt`**: agregarlo.
`make stream` debe crear el tópico si no existe, lanzar el productor y luego
consumir. Rejilla del día: 96 intervalos de 15 min desde
`2026-06-01T00:00:00-05:00`. El dataset es de un solo día con 3.795 eventos.

| Tipo | Condición | `ts` de la alerta |
|------|-----------|-------------------|
| `FRONTERA_SIN_REPORTE` | 3 intervalos consecutivos de 15 min sin medición. Una alerta por caída. | el del **tercer** intervalo ausente |
| `CONSUMO_FUERA_DE_BANDA` | `kwh` **estrictamente mayor** que `max_kwh` de `limites_frontera.csv` | el de la medición |
| `LECTURA_RETROACTIVA` | `ts_medicion` anterior a la última ya procesada para esa frontera | el de la medición tardía |

`make stream` debe **terminar solo**: condición de fin explícita (consumir
hasta el offset final del tópico tras publicar, o timeout de inactividad
propio, muy por debajo de 300 s). Al terminar, evaluar también las caídas
abiertas al final del día para `FRONTERA_SIN_REPORTE`.

## 10. Infra y entorno local

- `docker-compose.yml` ya está endurecido: credenciales por `.env` con
  `${VAR:?}`, imágenes pineadas (`postgres:16.9-alpine`,
  `redpandadata/redpanda:v26.2.6`), puertos solo en `127.0.0.1`, healthchecks
  y `make up` con `--wait`. Mantener esas propiedades al modificarlo.
- **Esta máquina de desarrollo no tiene Docker ni sudo sin contraseña.** Para
  todo lo que es Postgres usar `scripts/dev_pg_sin_docker.sh start` (Postgres
  16.15 sin root, puerto 8432, datos en `/tmp/bia-pg16` o `$PGLOCAL_DIR`) y
  correr con `PG_PORT=8432 ...` (el entorno pisa a `.env`). Ejemplo:
  `PG_PORT=8432 .venv/bin/python scripts/bootstrap_db.py` y
  `PG_PORT=8432 make test`. El streaming (Redpanda) sí requiere Docker. No
  intentar instalar nada con sudo.
- **El disco raíz (`/`) está casi lleno** (8 GB, <30 MB libres el 2026-11-11)
  por directorios del usuario fuera del repo. No crear archivos grandes fuera
  de `/tmp`; si pip o git fallan por espacio, avisar al usuario.
- El Python del sistema no trae `pip`/`ensurepip`; `make setup` lo arranca con
  `get-pip.py`. Usar siempre `.venv/bin/python`, nunca `python3` a secas para
  correr código del proyecto.
- Dependencias: solo las de `requirements.txt` (ADR-003). Antes de añadir una,
  justificarla y correr `make audit`.
- Los valores por defecto de host/puerto/base deben coincidir con lo declarado
  en `entrega.yaml`; `verificar_entrega.py` lee de ahí y `producer.py` lee
  `KAFKA_BOOTSTRAP`/`KAFKA_TOPIC` del entorno. No cablear puertos en el código.
- Este entorno de desarrollo está aislado: no intentar acceder a IPs de red
  local. Probar siempre con Docker local.
- Terraform en `infra/` **no se despliega**: S3 cifrado + versionado, KMS con
  política, IAM equivalentes a C4. Debe pasar `terraform validate`.

## 10b. Modelo y SQL (ya implementado)

- `sql/` numerado y **100 % idempotente**; `scripts/bootstrap_db.py` lo aplica
  en cada `make up` como superusuario y luego verifica la clasificación contra
  `information_schema`. Si añades una columna, **clasifícala** en
  `data_classification.yaml` o `make up` falla (a propósito).
- Nombres de rol fijos: `rol_ingesta`, `rol_analista`, `rol_auditor`
  (`src/bia/config.py` lo exige). Contraseñas solo por `set_config` desde `.env`.
- Convenciones: `*_cifrado` → `bytea` AES-GCM (Restringido-PII); `*_hmac` →
  `bytea` seudónimo para joins (Confidencial); hora en `ts_local` sin zona.
- `rol_ingesta` tiene grants **por columna**: no puede `SELECT *` en
  `core.dim_cliente`, `core.fact_facturacion` ni leer `payload*` de cuarentena.
  El pipeline debe listar columnas explícitamente, nunca `SELECT *`.
- La bitácora `audit.bitacora` es append-only por privilegios + trigger; el
  pipeline solo hace `INSERT`.
- Tests de permisos en `tests/test_permisos_db.py` (se saltan sin base).

## 11. Estructura objetivo de la entrega

```
entrega.yaml   README.md   Makefile   docker-compose.yml   .env.example
src/           sql/        infra/     tests/               out/
data_classification.yaml   THREAT_MODEL.md   SECURITY.md
DECISIONS.md   AI_USAGE.md   RUNBOOK.md   .git/
```

Las plantillas de los `.md` están en `plantillas/`; al completarlas se mueven
a la raíz con esos nombres. `DECISIONS.md` exige **mínimo 5 ADRs** centrados en
el trade-off (qué se descartó y por qué).

## 12. Cómo trabajar en este repo

- **Plan de acción**: `docs/PLAN.md`. Al completar un hito, marcar su casilla,
  anotar el commit en el registro de avance y commitear junto con el cambio.
- **Priorizar**: 70 % bien hecho vale más que 100 % a medias. Lo que no se
  alcance se declara en `entrega.yaml → no_implementado`. Ocultarlo resta.
- **Orden sugerido**: datos y modelo con seguridad incorporada → ingesta
  idempotente con cuarentena → reglas B → parte C completa → streaming →
  observabilidad → IaC → documentación. Reservar tiempo para una corrida limpia
  completa del contrato de la sección 4 y `make verificar`.
- **Idempotencia**: upserts por clave natural, nunca `INSERT` ciego. Antes de
  dar por cerrada una etapa, correr `make run` dos veces y comparar `out/` y
  conteos en base.
- **Tests** (`make test`): unitarios para parsers de cada formato, reglas B1–B7,
  detección/enmascarado de PII, y pruebas de permisos por rol. Nunca subir sin
  tests en verde.
- **Logs**: nunca loguear valores de columnas `Confidencial` o
  `Restringido-PII`, ni filas crudas de `clientes.csv`. Loguear ids, conteos y
  motivos.
- **AI_USAGE.md**: ir anotando *durante* el trabajo los prompts que movieron la
  aguja, lo que se cambió de lo generado y **al menos un error concreto de la IA
  corregido, con archivo y línea**. No reconstruirlo al final.
- Si el usuario pide solo lectura o análisis, no modificar archivos.
- No leer ni listar más de lo necesario de `data/maestros/clientes.csv` ni de
  `data/facturacion/` en salidas de consola: contienen PII real. Perfilar con
  scripts que enmascaren correos, NIT, celulares y cédulas.
- Los conteos esperados que ya conocemos (25 clientes con PII, 12 facturas
  huérfanas, 300 correcciones, 400 filas MWh) sirven como **tests de
  regresión** del pipeline; si un número cambia, algo se rompió.
