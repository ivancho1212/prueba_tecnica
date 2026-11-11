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
secretos (el `infra/` modela KMS/Secrets Manager). Un `.env` existente nunca
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
