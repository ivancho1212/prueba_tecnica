# Decisiones de arquitectura (ADRs)

> Formato corto. Lo que importa es el trade-off: qué se descartó y por qué.

---

## ADR-001 — Eliminar MinIO del scaffolding

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
El `docker-compose.yml` heredado levantaba MinIO con credenciales por defecto
(`minioadmin/minioadmin`) y `config/pipeline.yaml` lo declaraba como origen S3.
Al perfilar el paquete, **ningún dato pasa por MinIO**: los crudos están en
`data/` como archivos locales y el enunciado exige procesar "lo que encuentre en
`data/consumos/`".

**Opciones consideradas**
1. Mantener MinIO como zona *raw* (subir los CSV y leer desde S3).
2. Mantenerlo levantado "por si acaso", sin usarlo.
3. Eliminarlo del compose y modelar la capa S3 solo en Terraform (Parte F).

**Decisión**
Opción 3.

**Por qué**
La opción 1 añade un salto de red, una dependencia (`boto3`) y una superficie
de ataque (credenciales, bucket policies) sin aportar a ningún criterio
evaluado; cabe mal en 3 días. La opción 2 es lo que castiga la Parte C: un
servicio con credenciales por defecto expuesto sin necesidad. La arquitectura
cloud equivalente (bucket cifrado y versionado con KMS) se describe en
`infra/`, que es donde el enunciado pide verla.

**Consecuencias**
Menos infraestructura que levantar y asegurar; `make up` es más rápido y
robusto. Se pierde la demostración local de "lectura desde almacenamiento de
objetos"; se compensa con el diseño IaC y con una zona *raw* lógica en Postgres
(tabla de control de archivos con hash y linaje).

---

## ADR-002 — Credenciales de evaluación fijas, llaves criptográficas por máquina

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
C6 exige cero credenciales en el código. Pero el evaluador necesita conectarse
con el superusuario y con tres roles usando **exactamente** lo que diga
`entrega.yaml`, y `make setup`/`make up` deben dejar la infraestructura lista
sin pasos manuales, en una máquina que no es la nuestra.

**Opciones consideradas**
1. Generar passwords aleatorios en `make setup` y que `make up` reescriba
   `entrega.yaml` con ellos.
2. Credenciales efímeras fijas en `.env.example` (y en `entrega.yaml`), sin
   ningún valor en `src/`, `sql/` ni compose.
3. Pedir las credenciales por variables de entorno del evaluador.

**Decisión**
Opción 2 para las credenciales de Postgres. Para las llaves criptográficas
(`BIA_ENC_KEY`, `BIA_HMAC_KEY`) la plantilla las deja **vacías** y
`scripts/bootstrap_env.py` genera un valor aleatorio por máquina que nunca se
versiona ni se declara en `entrega.yaml`.

**Por qué**
La opción 1 hace que la entrega modifique su propio manifiesto en tiempo de
ejecución; si el evaluador lee `entrega.yaml` antes de `make up` (o lo copia a
otro lugar), toda la Parte C queda en cero. La opción 3 rompe "sin pasos
manuales". La opción 2 es lo que el propio enunciado describe como
"credenciales EFÍMERAS y SOLO para evaluación local". La separación clave: las
credenciales de **acceso** son conocidas por diseño, pero la llave que protege
la PII no lo es, porque el control C3 se verifica precisamente leyendo como
superusuario. El superusuario conoce la base; no debe conocer la llave.

**Consecuencias**
`.env.example` contiene credenciales de evaluación (explícitamente marcadas),
lo que se documenta en `SECURITY.md` y en el threat model como riesgo aceptado
para el entorno local. En producción estas variables vendrían de un gestor de
secretos (`infra/kms.tf` modela la llave en KMS; el gestor de secretos para las
contraseñas queda fuera de `infra/`). Un `.env` existente nunca
se sobreescribe: regenerar la llave dejaría ilegible la PII ya cifrada.

---

## ADR-003 — Dependencias mínimas, pineadas y auditadas; parseo con la stdlib

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
El `requirements.txt` heredado traía `requests 2.19.1`, `urllib3 1.24.1`,
`PyYAML 5.3.1` y `Jinja2 2.11.2`, con 48 avisos de seguridad acumulados según
OSV (`docs/evidencias/pip_audit_heredado_osv.txt`), más `pandas`. El maestro
de clientes trae entradas hostiles (B7): comillas anidadas, saltos de línea en
campos citados, cadenas de 1.632 caracteres y una inyección SQL.

**Opciones consideradas**
1. Actualizar todo lo heredado a su última versión y seguir con pandas.
2. Dejar solo lo que se usa, pineado, y parsear con `csv`/`json` de la stdlib.
3. Añadir un framework (dbt, Dagster, Spark).

**Decisión**
Opción 2: `psycopg2-binary`, `PyYAML`, `kafka-python`, `cryptography`,
`python-dotenv`, más `pytest` y `pip-audit`. Auditoría limpia
(`docs/evidencias/pip_audit_actual.txt`), repetible con `make audit`.

**Por qué**
`requests`, `urllib3` y `Jinja2` no los usa ningún componente: mantenerlos solo
agranda la superficie de ataque. Con pandas el control sobre `quoting`,
encoding por archivo y filas malformadas es indirecto, y el volumen (≈115 k
filas) no lo justifica; con `csv` cada fila rechazada se explica. La opción 3
no cabe en 3 días y añade servicios que asegurar. `psycopg2` se mantiene porque
`tools/verificar_entrega.py` (intocable) lo importa; usar un solo driver evita
dos pools y dos formas de parametrizar.

**Consecuencias**
Código algo más verboso en la ingesta (parsers explícitos) a cambio de control
total sobre B7 y de un árbol de dependencias pequeño y auditado. `make audit`
debe quedar en cero antes de cada entrega.

---

## ADR-004 — Modelo por esquemas, hora local sin zona, PII en `bytea` con HMAC para joins

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
El modelo debe servir a tres cosas a la vez: agregaciones exactas por
frontera-mes (B), controles verificables por rol y como superusuario (C3, C4,
C5) y una cuarentena consultable sin exponer PII. Además tiene que absorber
datos tardíos sin recargar todo (A).

**Opciones consideradas**
1. Esquema único `public` con tablas planas y PII en claro protegida solo por
   grants.
2. Estrella clásica con `timestamptz` en UTC y vistas que conviertan a Bogotá.
3. Cuatro esquemas por función (`core`, `quarantine`, `audit`, `analytics`),
   hechos en hora local sin zona, PII cifrada en aplicación (`bytea`) más un
   HMAC por campo sensible para igualdad/joins, y tabla de control de archivos
   con hash como zona *raw* lógica.

**Decisión**
Opción 3. DDL en `sql/` numerado, 100 % idempotente, aplicado por
`scripts/bootstrap_db.py` en cada `make up`.

**Por qué**
Los grants por esquema hacen el menor privilegio legible y verificable: el
analista solo tiene `USAGE` sobre `analytics`, el auditor sobre `audit`. El
enunciado define "mes" y "hora" en hora local Colombia sin horario de verano,
así que guardar `timestamp` local evita una conversión en cada consulta y hace
que `date_trunc('month')` sea inmutable (columna generada `mes`). Guardar UTC
habría sido más "correcto" en general, pero aquí añade riesgo de error en el
dato que se factura. La PII en `bytea` cifrado a nivel de aplicación garantiza
que leer la tabla cruda como superusuario no revela nada: la llave vive fuera
de la base (ADR-002). El HMAC con llave propia permite comprobar que el NIT de
la factura coincide con el del maestro sin descifrar ni exponer. La tabla
`control_archivos` (nombre + hash) es lo que permite que un archivo ya cargado
no se reescriba y que uno nuevo o corregido entre solo. Se descartó pgcrypto
porque la llave viajaría en cada sentencia SQL y quedaría en logs/`pg_stat`.

**Consecuencias**
Igualdad y joins sobre PII solo por HMAC (no hay búsquedas parciales ni
ordenación por esos campos: aceptable, el análisis no las necesita). El
superusuario de Postgres sigue pudiendo desactivar el trigger de la bitácora:
riesgo residual documentado en el threat model, mitigable en producción con un
rol de aplicación que no sea superusuario y auditoría del motor. La cuarentena
guarda el payload cifrado cuando la fuente tiene PII, así que depurar un
rechazo de clientes exige la llave: coste asumido a cambio de no tener PII en
claro en ninguna tabla.

---

## ADR-005 — Ingesta: re-parseo total, carga por archivo, B3 antes de B4, y upsert sin `EXCLUDED` en tablas con PII

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
`make run` debe ser idempotente, absorber `consumos_2026-04_correccion.csv` sin
recargar todo, procesar lo que encuentre en `data/consumos/` (incluido un mes
nuevo y un archivo truncado/corrupto) sin alterar meses ya cargados, y producir
un `dq_report.json` cuyos conteos sean exactos y estables entre corridas.

**Opciones consideradas**
1. Incremental puro: parsear y cargar solo archivos nuevos; `dq_report` desde
   la base.
2. Re-parsear todos los archivos presentes en cada corrida (≈115 k filas,
   <1 s), resolver B3/B4/B5 en memoria sobre el conjunto completo, y escribir
   en Postgres solo las filas de archivos nuevos o cambiados (hash), con un
   `ON CONFLICT` que reproduce B3 también entre archivos.
3. Truncar y recargar todo en cada corrida.

**Decisión**
Opción 2, con estas reglas concretas:
- **Orden B3 → B4**, literal al enunciado: "gana la lectura con mayor
  `version_lectura`" se aplica a toda lectura, válida o no; lo que gana y es
  inválido se descarta por B4 y deja hueco (B5). En los datos entregados ningún
  grupo duplicado contiene inválidas, así que ambos órdenes dan los mismos
  números; se documenta para el caso general.
- **Clasificación de sobrantes** (revisada el 2026-11-11, antes 420 conflictivos):
  `duplicados_exactos` = misma versión y mismo valor que la ganadora (180);
  `duplicados_conflictivos` = misma versión y valor distinto, es decir el
  empate real que exige la regla de desempate por orden de archivo (120);
  una fila con **versión menor** que la ganadora es un **reemplazo por dato
  tardío**, va a cuarentena con la regla `B3_SUPERADA_POR_VERSION` y se cuenta
  solo en `correcciones_tardias_aplicadas` (300), no en conflictivos.
  Razones: el enunciado trata la corrección como "relecturas que reemplazan",
  no como duplicados; los datos traen exactamente tres fenómenos (copias,
  empates, versiones) y el reporte tiene exactamente tres llaves; un empate es
  lo único que necesita desempate, una versión mayor ya tiene ganador; y
  contar las 300 en conflictivos haría redundante la llave de correcciones.
  Hay ambigüedad real en "filas sobrantes descartadas"; se eligió la lectura
  que deja cada métrica midiendo una sola cosa.
- **Huecos** sobre la rejilla completa: todas las fronteras del maestro × todos
  los meses detectados en los archivos presentes, días del mes × 24.
- **Un archivo = una transacción.** Cabecera irreconocible ⇒ `ARCHIVO_CORRUPTO`,
  nada se carga. Más del 50 % de filas estructuralmente malformadas ⇒ el
  archivo entero se rechaza (cortacircuito contra basura "casi válida"). Filas
  con bytes nulos ⇒ `FILA_CORRUPTA`. Cualquier excepción al cargar un archivo
  ⇒ rollback de ese archivo, `ARCHIVO_ERROR_CARGA`, y la corrida sigue.
- **Upsert sin `EXCLUDED` en `dim_cliente`/`fact_facturacion`**: Postgres exige
  `SELECT` sobre cada columna referenciada vía `EXCLUDED`, lo que obligaría a
  dar a `rol_ingesta` lectura de las columnas cifradas. Se usa
  `UPDATE … WHERE hash_fila IS DISTINCT FROM` + `INSERT … WHERE NOT EXISTS`.

**Por qué**
La opción 1 hace imposible calcular duplicados y huecos entre archivos sin
reconstruir el estado desde la base y vuelve el reporte dependiente del orden
de llegada. La opción 3 viola "sin recargar todo" y reescribe PII en cada
corrida. Con la opción 2 el reporte es una función pura de los archivos
presentes (dos corridas → bytes idénticos), la base solo recibe escrituras
cuando hay algo nuevo, y la regla B3 queda implementada dos veces coherentes:
en memoria (dentro de la corrida) y en el `ON CONFLICT` (frente a lo que ya
estaba en la tabla, con `COLLATE "C"` para igualar el orden de Python).

**Consecuencias**
Un reenvío idéntico de un archivo con otro nombre genera miles de
`DUPLICADO_EXACTO` en cuarentena (así lo define el enunciado) y, por B3, el
archivo lexicográficamente posterior gana el linaje de esas filas aunque el
valor no cambie. Si un día el volumen hiciera caro re-parsear todo, el diseño
admite pasar a incremental manteniendo los conteos en la base; hoy el costo es
de ~0,6 s. Verificado en `tests/` y con un escenario de mes nuevo + archivo
truncado con binario + archivo basura: meses previos con hash idéntico.

---

## ADR-006 — Streaming: leer el día y terminar en el offset final, sin grupo de consumo

**Estado:** Aceptada
**Fecha:** 2026-11-11

**Contexto**
`make stream` debe crear el tópico si no existe, alimentarlo con
`tools/producer.py`, aplicar tres reglas de alerta exactas y **terminar solo**
antes de 300 s. El productor particiona por `frontera_id`, así que el orden
solo está garantizado por frontera. Kafka entrega "al menos una vez" y el
evaluador o un desarrollador pueden correr `make stream` más de una vez sobre
el mismo tópico.

**Opciones consideradas**
1. Consumidor con `group_id` y offsets confirmados, terminando por
   `consumer_timeout_ms` (inactividad).
2. Asignación manual de todas las particiones, leer desde el principio y
   terminar al alcanzar el offset final que tenía el tópico cuando el
   productor terminó; inactividad y plazo total solo como redes de seguridad.
3. Leer `mediciones_stream.ndjson` directamente, sin broker.

**Decisión**
Opción 2. Además:
- Las reglas viven en un detector puro (`src/bia/stream/alertas.py`) cuyo
  estado es **solo por frontera**; así el resultado no depende de cómo se
  intercalen las particiones (probado con intercalados aleatorios).
- Deduplicación por `evento_id` antes de evaluar: una reentrega o un tópico
  con eventos de una corrida anterior no generan `LECTURA_RETROACTIVA` falsas.
- `FRONTERA_SIN_REPORTE` se emite cuando la frontera vuelve a reportar tras
  un hueco de 3 o más intervalos, o al cerrar el día para las caídas abiertas
  (incluida una frontera que nunca reporte). Una alerta por caída, en el
  tercer intervalo ausente.
- Un evento inválido se rechaza con motivo y **no cuenta como medición**.
- Límites desde `limites_frontera.csv` con el mismo parser del batch.

**Por qué**
La opción 1 termina por inactividad, que es una heurística: con un productor
lento corta antes, con uno rápido espera de más, y el grupo añade un
rebalanceo de varios segundos. Sus offsets confirmados harían que una segunda
corrida no leyera nada y dejara un `out/alertas.json` vacío. La opción 2 tiene
una condición de fin exacta y una segunda corrida produce el mismo archivo.
La opción 3 no cumple el enunciado.

**Consecuencias**
Cada `make stream` vuelve a publicar los 3.795 eventos, así que el tópico
crece y la lectura es lineal en el número de corridas acumuladas: aceptable
para una evaluación, no para producción (ver `THREAT_MODEL.md` §5). En esta
máquina no hay Docker, así que `make stream` se probó contra Apache Kafka 4.3.2
en modo KRaft (mismo protocolo que Redpanda), no contra el contenedor de
`docker-compose.yml`: 33 alertas (22 fuera de banda, 6 sin reporte, 5
retroactivas), sin duplicados, idénticas en corridas repetidas y con el
tópico recién creado, en ~2 s.

