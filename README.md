# Prueba técnica — Data Platform & Security Engineer (Mid)

**Bia Energy · Equipo de Data**

---

## 1. Antes de empezar: cómo leer esta prueba

Esto no es un ejercicio de algoritmos. Es una versión reducida y honesta de lo
que vas a hacer si entrás: recibir datos que llegan mal, convertirlos en algo
confiable, y que eso que construiste **no sea el hueco de seguridad** por donde
se escapa la información de nuestros clientes.

Tres cosas que queremos que tengas claras desde ya:

**Podés usar IA, y esperamos que la uses.** Copilot, Claude, Cursor, lo que
uses normalmente. No es trampa, es la herramienta. Lo único que te pedimos es
que lo documentes (sección 8) y que **entiendas lo que entregás**: después de
esta prueba hay una entrevista técnica donde vamos a abrir *tu* código y
preguntarte por decisiones específicas. Quien no pueda defender lo que entregó,
no pasa — por bueno que se vea el repo.

**Preferimos 70% hecho bien a 100% hecho a medias.** Hay más trabajo acá del
que cabe cómodo en tres días. Es a propósito: queremos ver qué priorizás. Si
dejás algo sin hacer, declaralo en `entrega.yaml` → `no_implementado`. Ser
explícito sobre lo que falta **suma puntos**. Que lo descubramos nosotros,
resta.

**Los datos están sucios a propósito.** Todos los defectos que vas a encontrar
salieron de cosas que nos pasaron de verdad. No son errores del enunciado.

> ⏱️ **Tiempo:** 3 días calendario desde que recibís el paquete.
> Estimamos entre 12 y 16 horas de trabajo efectivo. Si estás yendo muy por
> encima de eso, parás, documentás qué falta y entregás. Eso también lo
> evaluamos.

---

## 2. El contexto

Bia comercializa energía a clientes no regulados en Colombia. Cada punto de
medición es una **frontera comercial**, identificada por un código `CO##########`
y un código SIC (`frtXXXXX`). Cada frontera reporta **consumo horario en kWh**.

Con eso facturamos. Si el dato está mal, la factura está mal. Si la factura
está mal, el cliente se va — o nos demanda. Y como en el camino manejamos NITs,
correos, celulares y direcciones de empresas reales, estamos bajo el régimen de
protección de datos personales (Ley 1581) y en proceso de certificación
ISO/IEC 27001.

Tu trabajo: construir la plataforma que toma los archivos crudos y produce
información confiable **y bien custodiada**.

---

## 3. Qué te damos

```
prueba-tecnica/
├── README.md                  ← este archivo
├── data/
│   ├── maestros/
│   │   ├── clientes.csv              40 clientes. CONTIENE PII.
│   │   ├── fronteras.csv             40 fronteras y su cliente
│   │   └── limites_frontera.csv      banda operativa por frontera
│   ├── consumos/
│   │   ├── consumos_2026-01.csv
│   │   ├── consumos_2026-02.csv
│   │   ├── consumos_2026-03.csv
│   │   ├── consumos_2026-04.csv
│   │   └── consumos_2026-04_correccion.csv
│   ├── facturacion/
│   │   └── facturacion_2026-{01..04}.json     JSON anidado
│   └── eventos/
│       └── mediciones_stream.ndjson           3.790 eventos para el stream
├── scaffolding/
│   ├── docker-compose.yml     Postgres + MinIO + Redpanda
│   ├── Makefile               ⚠️ CONTRATO DE EJECUCIÓN — leelo
│   ├── entrega.yaml           ⚠️ MANIFIESTO — obligatorio
│   ├── config/pipeline.yaml
│   ├── requirements.txt
│   ├── .env.example
│   └── tools/
│       ├── producer.py               productor de Kafka, ya resuelto
│       └── verificar_entrega.py      `make verificar`, ya resuelto
└── plantillas/
    ├── THREAT_MODEL.md
    ├── SECURITY.md
    ├── DECISIONS.md
    └── AI_USAGE.md
```

> ⚠️ **El scaffolding viene de un PoC interno y no pasó revisión de seguridad.**
> Auditarlo hace parte del alcance de la prueba. Tratalo como tratarías
> cualquier código heredado que te toca poner en producción.

Los archivos de `data/` son **inmutables**: no los edites a mano. Tu pipeline
tiene que arreglárselas con ellos tal como están.

---

## 4. Lo que tenés que construir

### Parte A — Ingesta y modelado batch

Un pipeline que lee los archivos crudos y los deja en Postgres en un modelo
analítico que vos diseñás (dimensional o el que justifiques en `DECISIONS.md`).

Los cuatro meses de consumo **no vienen en el mismo formato**. Esa es la
primera parte del trabajo: descubrí las diferencias leyendo los archivos, no
asumas nada. Hay cambios de encoding, de separador, de nombre de columna y de
unidad.

Requisitos duros:

- **Idempotencia.** `make run` dos veces seguidas debe dejar exactamente el
  mismo estado. Lo vamos a verificar corriéndolo dos veces y comparando.
- **Datos tardíos.** `consumos_2026-04_correccion.csv` trae relecturas que
  reemplazan registros ya cargados. Tu pipeline debe absorberlas sin recargar
  todo desde cero.
- **Cuarentena.** Lo que rechaces no se borra: va a una zona de cuarentena
  consultable, con el motivo del rechazo.
- **Descubrimiento de archivos.** Tu pipeline tiene que procesar **lo que
  encuentre** en `data/consumos/`, no una lista fija de meses.

  > Lo vamos a probar en serio: durante la evaluación dejamos caer **un mes
  > que no viene en este paquete** y volvemos a correr `make run`. Usará uno
  > de los formatos que ya viste. Después metemos **un archivo truncado y
  > corrupto** y volvemos a correr. Lo que medimos ahí no es que no falles —
  > es que no **corrompas**: podés rechazar el archivo entero o mandar las
  > filas malas a cuarentena, pero los meses ya cargados no pueden cambiar.

### Parte B — Calidad de datos

Reglas de normalización y validación. Estas son **exactas** — no son
interpretables, y de ellas dependen los números que vamos a comparar:

| # | Regla |
|---|-------|
| B1 | **Zona horaria.** El mes y la hora se definen en hora local de Colombia (`America/Bogota`, UTC−5 fijo, sin horario de verano). Algún archivo viene en UTC: normalizá. |
| B2 | **Unidades.** El resultado final está siempre en **kWh**. Si una fila declara otra unidad, convertila. |
| B3 | **Duplicados.** Para una misma `(frontera_id, timestamp)` gana la lectura con mayor `version_lectura`. Si empatan en versión, gana **la última en orden de archivo**, procesando los archivos en orden lexicográfico de nombre. |
| B4 | **Inválidos.** Se **descartan** (no se imputan) y se reportan: valor nulo/vacío, valor negativo, y valor `> 1.000.000 kWh` (outlier físicamente imposible para estas fronteras). |
| B5 | **Huecos.** Una **hora sin lectura válida** es un hueco — da igual si la fila no existía o si existía pero fue descartada por B4. Los huecos se **reportan, no se rellenan**. |
| B6 | **Facturación huérfana.** Una factura cuya `frontera_id` no existe en el maestro no se carga al modelo: va a cuarentena y se reporta. |
| B7 | **Entradas hostiles.** El maestro de clientes trae texto que rompe parsers ingenuos: comillas anidadas, un campo citado con un salto de línea adentro, cadenas larguísimas y algo que parece SQL. Son datos, no código. Ninguna fila puede perderse en silencio. |

### Parte C — Seguridad, privacidad y gobernanza

Esta es la parte que distingue este cargo de un Data Engineer normal. **Pesa
más que ninguna otra** (ver sección 9).

- **C1 · Clasificación.** Un `data_classification.yaml` que etiquete cada
  columna del modelo en: `Público` / `Interno` / `Confidencial` / `Restringido-PII`.
  Que sea un artefacto vivo que el pipeline consulta, no un documento muerto.

- **C2 · PII en texto libre.** El campo `observaciones` de `clientes.csv`
  tiene correos, celulares y cédulas incrustados por gente que escribió ahí lo
  que no debía. Detectalos y enmascaralos. Reportá cuántos clientes están
  afectados.

- **C3 · Cifrado en reposo.** Los campos `Restringido-PII` no pueden quedar en
  claro en la base. Elegí tu enfoque (cifrado a nivel de aplicación, envelope
  encryption, pgcrypto…) y justificalo. Si necesitás hacer *joins* por un campo
  sensible, resolvelo sin exponerlo.

  > Lo verificamos leyendo la tabla cruda **como superusuario** — que es
  > exactamente lo que hace quien se roba un dump o tiene acceso de DBA. Por eso
  > `entrega.yaml` te pide las credenciales del superusuario local: sin ellas
  > este control no se puede comprobar y el punto se pierde.

- **C4 · Menor privilegio.** Tres roles de Postgres, con permisos mínimos
  reales:
  - `ingesta` — escribe en las tablas del modelo. No lee PII en claro.
  - `analista` — consulta la **vista analítica**. No puede tocar las tablas
    base ni ver PII en claro. No puede escribir nada.
  - `auditor` — lee la bitácora de auditoría. No ve PII en claro.

  > Lo vamos a probar conectándonos con cada rol e intentando hacer lo que no
  > debería poder hacer. Si un rol puede más de lo que dice, se pierde el punto.

- **C5 · Trazabilidad.** Una bitácora **append-only** (`UPDATE` y `DELETE`
  revocados de verdad, no por convención) que registre cada corrida: quién,
  qué, cuándo, cuántas filas, con qué resultado.

- **C6 · Secretos.** Cero credenciales en el código. Revisá el scaffolding.

- **C7 · Dependencias.** Revisá `requirements.txt`.

- **C8 · Threat model.** `THREAT_MODEL.md` con STRIDE sobre **tu** arquitectura:
  mínimo 8 amenazas concretas, cada una con su control y su mapeo al Anexo A de
  ISO/IEC 27001:2022. Amenazas sobre el pipeline que construiste, no genéricas
  de libro.

### Parte D — Streaming

Un consumidor que lee el tópico `mediciones.raw` (usá `tools/producer.py` para
alimentarlo) y emite alertas. Las tres reglas, exactas:

| Tipo | Condición | `ts` de la alerta |
|------|-----------|-------------------|
| `FRONTERA_SIN_REPORTE` | Una frontera acumula **3 intervalos consecutivos de 15 min sin medición** (45 min). Una sola alerta por caída. | el del **tercer** intervalo ausente |
| `CONSUMO_FUERA_DE_BANDA` | `kwh` **estrictamente mayor** que el `max_kwh` de esa frontera en `limites_frontera.csv` | el de la medición |
| `LECTURA_RETROACTIVA` | Llega una medición con `ts_medicion` **anterior** a la última ya procesada para esa frontera | el de la medición que llegó tarde |

La rejilla del día son 96 intervalos de 15 minutos desde
`2026-06-01T00:00:00-05:00`.

### Parte E — Observabilidad y operación

- Logs **estructurados en JSON**, una línea por evento, en `logs/pipeline.log`.
  > **Tus logs no pueden contener PII.** Los vamos a revisar con expresiones
  > regulares buscando correos, celulares y cédulas de nuestros datos.
- `out/metrics.json` con, mínimo: filas leídas, filas cargadas, filas
  rechazadas por motivo, duración por etapa, timestamp de la corrida.
- Un `RUNBOOK.md` corto: qué hacer cuando esto falla a las 2 a.m.

### Parte F — Infraestructura como código (no se despliega)

`infra/` con Terraform que describa cómo correría esto en AWS: bucket S3 con
cifrado y versionado, llave KMS con su política, roles IAM de menor privilegio
equivalentes a C4, y lo que consideres necesario.

**No lo despleguemos.** No necesitás cuenta de AWS ni vas a gastar un peso. Se
evalúa leyendo el código: `terraform validate` debe pasar.

---

## 5. El contrato de ejecución

Tu entrega se califica **ejecutándola de forma automática**. El evaluador corre
exactamente esto:

```bash
make setup      # instalar dependencias
make up         # levantar infra y dejarla lista
make run        # pipeline batch completo      (timeout 900 s)
make run        # otra vez → prueba de idempotencia
make stream     # consumidor de streaming      (timeout 300 s)
make test       # tu suite de pruebas
make down
```

**Si un target no existe o falla, los puntos asociados se pierden sin revisión
manual.** Podés cambiar cómo implementás cada uno; no podés cambiar su nombre.

> ⚠️ **`out/` y `logs/` se borran antes de la primera corrida.** Lo que
> calificamos tiene que haberlo producido tu pipeline en nuestra máquina. Si
> tu `make run` no termina en 0, no sirve de nada que el zip traiga la salida
> de tu última corrida local.

### `make verificar` — corrélo antes de comprimir

Hay un séptimo target que **ya viene resuelto**:

```bash
make up
make run
make verificar
```

Se conecta con los tres roles que declaraste en `entrega.yaml` y busca los
objetos que nombraste. No califica nada ni te dice si tus controles están
bien: solo confirma que **el evaluador va a poder conectarse**.

Es la causa número uno de puntos perdidos por accidente. Toda la parte C
(31 puntos) se verifica conectándose a tu base con lo que diga ese archivo: un
nombre de objeto sin esquema o un puerto distinto deja esos checks en cero, y
no hay forma de que los adivinemos.

`make stream` **tiene que terminar solo**. Un consumidor que se queda escuchando
para siempre cuenta como fallo por timeout.

### Archivos de salida — formato exacto

**`out/consumo_mensual.csv`**

```csv
frontera_id,mes,consumo_kwh,horas_con_dato
CO1114174638,2026-01,183421.553,744
```

`mes` en formato `YYYY-MM` (hora local). `consumo_kwh` con 3 decimales. Una
fila por cada combinación frontera-mes con al menos una lectura válida.

**`out/dq_report.json`**

```json
{
  "filas_leidas": 0,
  "duplicados_exactos": 0,
  "duplicados_conflictivos": 0,
  "registros_negativos": 0,
  "registros_nulos": 0,
  "registros_outlier": 0,
  "registros_mwh_convertidos": 0,
  "correcciones_tardias_aplicadas": 0,
  "horas_sin_lectura_valida_total": 0,
  "horas_sin_lectura_valida_por_frontera": { "CO1114174638": 12 },
  "fronteras_huerfanas_facturacion": 0,
  "clientes_con_pii_en_texto_libre": 0
}
```

Todas las llaves son obligatorias. `duplicados_exactos` y
`duplicados_conflictivos` cuentan **filas sobrantes descartadas**, no grupos.

**`out/alertas.json`**

```json
[
  { "tipo": "FRONTERA_SIN_REPORTE", "frontera_id": "CO1114174638",
    "ts": "2026-05-01T06:30:00-05:00" }
]
```

Orden libre, duplicados no permitidos. `ts` en ISO 8601 con offset.

---

## 6. Qué entregás

Un único archivo:

```
apellido_nombre.zip
```

Todo en minúscula, sin tildes, sin espacios. Si te llamás Juan Pérez Gómez:
`perez_juan.zip`.

Dentro, en la raíz:

```
perez_juan/
├── entrega.yaml           ⚠️ obligatorio, completo
├── README.md              cómo correrlo + qué hiciste + qué no
├── Makefile
├── docker-compose.yml
├── src/ ...               tu código
├── sql/ ...               DDL, roles, grants
├── infra/ ...             terraform
├── tests/ ...
├── out/                   (vacío o con tu última corrida, da igual)
├── data_classification.yaml
├── THREAT_MODEL.md
├── SECURITY.md
├── DECISIONS.md
├── AI_USAGE.md
├── RUNBOOK.md
└── .git/                  ⚠️ incluí el historial (ver sección 8)
```

**Incluí `.git/`.** No es para vigilarte: nos sirve para entender cómo
trabajaste y para preparar mejores preguntas para la entrevista. Un repo sin
historial no descalifica, pero perdés la oportunidad de mostrar tu proceso.

**No incluyas** `node_modules/`, `.venv/`, volúmenes de Docker ni los datos de
`data/` (ya los tenemos). El zip no debería pasar de 10 MB.

---

## 7. Reglas del juego

- Lenguaje: **Python** para el pipeline, **SQL** para el modelo. El resto, a tu
  gusto (dbt, Spark, Dagster, Airflow… si justificás que cabe en 3 días).
- Todo corre **local**. No necesitás cuenta de ningún cloud.
- Podés usar cualquier librería open source.
- Podés preguntarnos lo que quieras por correo. Preguntar no resta; las
  preguntas buenas nos dicen cosas de vos. Respondemos en horario laboral.

---

## 8. Sobre el uso de IA

Usala. En serio. Lo que medimos no es si escribiste cada línea a mano, es si
sabés qué estás poniendo en producción.

`AI_USAGE.md` es obligatorio y tiene que responder cuatro cosas:

1. Qué herramientas usaste y para qué partes.
2. Dos o tres prompts que de verdad movieron la aguja (pegalos textuales).
3. Qué generó la IA que **cambiaste** y por qué.
4. **Un error que la IA cometió y que tuviste que corregir.** Concreto, con el
   archivo y la línea.

La 4 es la importante. Quien usó IA de verdad siempre tiene esa historia.

Un `AI_USAGE.md` vacío o un "no usé IA" no suma ni resta por sí mismo — pero la
entrevista técnica va igual, y ahí se nota.

---

## 9. Cómo se califica

100 puntos. **76 son automáticos**: ejecutamos tu código y comparamos tus
números contra un resultado esperado que ya conocemos. Los 24 restantes los
revisa una persona (modelo de datos, threat model, ADRs, legibilidad).

Que la mayor parte sea automática es deliberado: queremos que la nota dependa
de si tu solución **funciona**, no de a quién le cayó bien tu estilo.

| Bloque | Puntos |
|--------|-------:|
| C · Seguridad, privacidad y gobernanza | **31** |
| A · Ingesta, modelado, idempotencia y **robustez** | 24 |
| B · Calidad de datos y exactitud de resultados | 16 |
| D · Streaming | 9 |
| E · Observabilidad y operación | 8 |
| F · IaC y arquitectura cloud | 5 |
| G · Ingeniería (pruebas, estructura, legibilidad) | 4 |
| H · Comunicación y criterio (ADRs, AI_USAGE, honestidad) | 3 |

**Umbral para pasar a entrevista técnica: 65/100.**

Hay tres cosas que son **eliminatorias**, sin importar el puntaje:

1. PII en claro en logs, en `out/`, o accesible por el rol `analista`.
2. El secreto heredado del scaffolding sigue vivo en tu entrega.
3. La tabla de consumos desaparece o queda vacía al procesar el maestro de
   clientes: una entrada hostil se ejecutó como código.

Son eliminatorias porque en este cargo son exactamente el error que no podemos
permitirnos.

---

## 10. Sugerencia de ritmo

No es obligatorio, pero si no sabés por dónde arrancar:

- **Día 1** — Leer los datos de verdad (abrilos, contá filas, buscá las
  diferencias). Modelo + ingesta batch + idempotencia.
- **Día 2** — Calidad de datos y toda la Parte C. Es la que más pesa; no la
  dejes para el final.
- **Día 3** — Streaming, observabilidad, IaC, documentación. Reservá las
  últimas dos horas para el `README.md`, `DECISIONS.md` y declarar en
  `entrega.yaml` lo que no alcanzaste.

Antes de comprimir, corré el contrato completo de la sección 5 en limpio, desde
cero, y cerrá con `make verificar`. La causa número uno de puntos perdidos es
que "en mi máquina sí corría"; la segunda es un `entrega.yaml` que no describe
lo que el `make up` levanta.

---

## 11. Después de la prueba

Si pasás el umbral: **entrevista técnica de 75 minutos**, sobre tu propio
código. Abrimos tu repo, miramos decisiones concretas y te pedimos que
extiendas algo en vivo. No hay algoritmos de pizarrón ni preguntas de trivia.

Suerte. Y gracias por el tiempo — sabemos que tres días es mucho pedir.

*Equipo de Data · Bia Energy*
