# Uso de IA en esta prueba

## 1. Herramientas y dónde las usé

| Herramienta | Para qué | Aproximadamente cuánto del resultado |
|---|---|---|
| Claude Code (CLI) con Claude Fable 5.1 y, al final, Claude Opus 5.5 | Agente en la terminal: leyó el enunciado y los datos (con scripts que enmascaraban la PII), propuso el plan (`docs/PLAN.md`), escribió el código, el SQL, los tests, el Terraform y los borradores de los documentos, y ejecutó todo contra un Postgres y un Kafka locales | La mayor parte del texto del código y de la documentación lo escribió el agente. El alcance de cada paso, las interpretaciones de reglas ambiguas, qué se cambiaba y qué no, y la validación final en mi máquina las decidí yo |

Forma de trabajo: un hito a la vez, con paradas explícitas. Antes de
implementar pedía un análisis "sin cambiar nada"; después aprobaba un alcance
cerrado y pedía la verificación concreta (conteos, segunda corrida, tests).
Cada cambio quedó en un commit pequeño en `dev-kodama`, que es lo que se ve en
el historial.

## 2. Prompts que movieron la aguja

**Prompt 1** — revisión del Hito 1 antes de seguir construyendo:

```
Antes de seguir con el siguiente hito quiero que paremos un momento y revisemos lo que acabamos de hacer en el hito 1 porque no quiero seguir construyendo sin tener claro el por que de las decisiones que tomamos.

Quiero que revises el proyecto y me expliques principalmente estas cosas:

por que quitamos MinIO y que encontraste para decidir que realmente no lo necesitabamos

por que terminamos usando core quarantine audit y analytics y que problema resuelve cada uno

por que usamos bytea + HMAC para la PII, que campos usan cada cosa y por que no era suficiente solo cifrar

como funciona realmente la idempotencia, que identifica un archivo ya procesado, que pasa si corro otra vez el mismo archivo, si cambia el archivo y como se manejan las correcciones

por que decidimos hacer una transaccion por archivo y que pasa con un archivo que tenga errores, este truncado o corrupto

tambien quiero que me expliques B1 a B5 pero desde el problema de calidad de datos que resuelve cada regla y no desde el codigo

y quiero entender bien lo del audit append-only porque vi que mencionaste que los triggers incluso impiden cambios del superusuario. quiero que revises eso con cuidado y me digas exactamente como funciona y si realmente podemos afirmar eso o tiene alguna limitacion en PostgreSQL

En todos estos puntos quiero que te bases en lo que realmente encontraste en el README, los datos y el codigo actual. no quiero que inventes justificaciones despues de haber implementado las cosas. si alguna decision fue una inferencia tuya o una decision de implementacion y no algo que pidiera directamente el ejercicio, dimelo.

Tampoco cambies nada. Solo revisa lo que ya esta hecho y explicame el por que, que evidencia habia para tomar la decision y donde esta implementada. Tambien dime que prueba tenemos de que cada cosa funciona.

La idea es que antes de continuar yo tenga claro el razonamiento tecnico de lo que ya construimos y no simplemente seguir ejecutando los siguientes pasos.
```

**Qué obtuve y qué hice con eso:** la explicación separó lo que pedía el
enunciado de lo que había inferido el agente. Lo más importante: el agente
reconoció que su afirmación sobre el superusuario era falsa y lo comprobó en
la base (ver §4). De ahí salieron tres trabajos posteriores: corregir la
redacción del test y del plan, dejar el límite explícito en `SECURITY.md` §6
y llevarlo como riesgo aceptado (T7) al `THREAT_MODEL.md`.

**Prompt 2** — interpretación de B3:

```
bien, B6 ya queda cerrado con las pruebas que hiciste.

antes de seguir con el siguiente hito quiero cerrar solo una cosa que puede afectar los resultados del ejercicio: B3.

revisa nuevamente el README y el codigo de B3 y dime exactamente como deberiamos interpretar el conteo de duplicados conflictivos cuando existe una correccion de una version anterior.

tenemos 180 duplicados exactos, 120 conflictos dentro de los archivos originales y 300 registros que son reemplazados por las correcciones de abril, por eso actualmente reportamos 420.

quiero que determines si el enunciado espera que esos 300 se cuenten dentro de duplicados_conflictivos o si deberian reportarse solamente como correcciones_tardias_aplicadas y dejar duplicados_conflictivos en 120.

no cambies nada todavia. solo revisa el texto exacto del enunciado, como estan definidos los campos del dq_report y el comportamiento esperado, y dame la conclusion con la evidencia que encontraste.

si hay ambiguedad real dimelo y explica que opcion tiene menos riesgo frente al evaluador.
```

**Qué obtuve y qué hice con eso:** el agente confirmó que había ambigüedad
real y argumentó por 120: tres fenómenos en los datos para tres llaves del
reporte, y "conflictivo" como empate que necesita desempate. Decidí 120 y que
las 300 filas reemplazadas fueran a cuarentena con su propia regla
(`B3_SUPERADA_POR_VERSION`), en lugar de perderlas. Commit `0ab51f6`,
razonamiento en ADR-005.

**Prompt 3** — fallos al validar en Windows:

```
Acabo de validar todo en Windows.

El pipeline corre 2 veces y los resultados son idempotentes. `verificar_entrega.py` pasa completo, pero pytest queda 2 fallos:

1. `tests/test_bootstrap_env.py::test_plantilla_real_no_trae_llaves_ni_env_versionado`
   - `p.read_text()` usa el encoding por defecto de Windows y falla con `UnicodeDecodeError` leyendo `.env.example`.
   - El archivo está en UTF-8.
   - No quiero cambiar el archivo a otro encoding solo para Windows. Revisa si el test debe leer explícitamente `encoding="utf-8"` y si eso es coherente con el proyecto.

2. `tests/test_consumos_reglas.py::test_bytes_nulos_se_rechazan_sin_perder_lo_anterior`
   - El test espera 2 lecturas válidas y una fila corrupta:
     fila 2 válida
     fila 3 con NUL rechazada como `FILA_CORRUPTA`
     fila 4 válida
   - Actualmente `csv` lanza `line contains NUL`, el parser marca `truncado=True` y solo queda 1 lectura.
   - Necesito que revises el contrato del README, el test y el parser actual antes de proponer cambios.
   - Determina si el comportamiento esperado realmente es continuar después de una fila con NUL o si el test está equivocado.
   - No cambies código todavía. Solo dime para cada fallo: causa raíz, archivo/línea relevante, qué contrato lo justifica y cuál sería el cambio mínimo correcto.
   - Tampoco quiero tocar comportamiento que ya está validado por los datos reales ni hacer cambios cosméticos para que pytest pase.
```

**Qué obtuve y qué hice con eso:** el diagnóstico de §4. Aprobé el cambio
mínimo con una condición: buscar todos los usos de `truncado` antes de
borrar nada. Commit `4656c74`.

## 3. Qué generó la IA que cambié, y por qué

| Archivo / función | Qué propuso la IA | Qué dejé yo | Por qué |
|---|---|---|---|
| `src/bia/ingesta/consumos.py::deduplicar` | Contar como `duplicados_conflictivos` toda fila sobrante con versión o valor distinto: 420 | 120 conflictivos (empates de versión); las 300 reemplazadas por la corrección, en cuarentena como `B3_SUPERADA_POR_VERSION` y solo en `correcciones_tardias_aplicadas` | Con 420 la llave de correcciones era redundante y "conflictivo" dejaba de significar empate (ADR-005, `0ab51f6`) |
| `src/bia/ingesta/facturacion.py::procesar_facturacion` | Cifrar `cliente.nit` y `cliente.email_facturacion` por una lista fija en el código | Que consulte `data_classification.yaml` con el mismo mecanismo que `maestros.py` y falle si no coincide | C1 pide una clasificación viva; facturación era la única fuente donde no mandaba (`bdd211a`) |
| `tests/test_permisos_db.py`, `docs/PLAN.md` | "El trigger frena incluso al superusuario" | "Frena una sesión normal; un superusuario administrativo puede desactivarlo" | Era falso (§4). Preferí documentar el límite antes que sobrevender el control (`87267cf`) |
| `fact_facturacion.nit_coincide_maestro` | Implementar el cruce con el maestro por HMAC del NIT | Solo la columna; la lógica queda sin hacer y declarada | B6 se resuelve por `frontera_id`; el cruce por NIT no lo pide el enunciado y no quería sumar alcance |
| `SECURITY.md`, `THREAT_MODEL.md` | Borradores con controles descritos en abstracto | Cada afirmación con archivo, test o commit, cada comando ejecutado contra la base antes de documentarlo, y lo no construido marcado como No | No quería afirmaciones más fuertes que lo implementado. Al ejecutar los comandos aparecieron cuatro que no coincidían y se corrigieron |
| Identidad de los commits | La identidad del entorno de trabajo | Mi cuenta personal en todos los commits de la entrega | El historial `.git/` se entrega y debe reflejar mi autoría |

## 4. Un error que cometió la IA y que tuve que corregir

**El parser de consumos daba resultados distintos según la versión de
Python.** `src/bia/ingesta/consumos.py`, líneas 121–125 antes del commit
`4656c74`:

```python
except csv.Error as e:  # p. ej. bytes NUL: el resto del archivo no es confiable
    res.truncado = True
    res.rechazos.append(Rechazo(archivo.nombre, lector.line_num, "ARCHIVO_TRUNCADO", ...))
    break
```

En Python 3.11 o superior el módulo `csv` acepta un byte NUL dentro de un
campo: la fila llegaba entera, la rechazaba como `FILA_CORRUPTA` un chequeo
posterior y el parser seguía. En Python anterior, `csv` lanza `line contains
NUL`, se entraba en esta rama, el archivo se cortaba ahí y las filas válidas
siguientes se perdían. El agente sabía de la diferencia: había dejado el
comentario "Python ≥ 3.11 ya no lanza csv.Error por NUL" unas líneas más abajo.
Aun así, en la revisión de cierre del Hito 1 la describió como una "rama casi
muerta" y cosmética. Como el desarrollo corría en 3.13, sus tests pasaban.

**Cómo lo detecté:** corriendo la suite en mi máquina con Windows. Fallaron
`test_bytes_nulos_se_rechazan_sin_perder_lo_anterior` y, por otra causa,
`tests/test_bootstrap_env.py:22`, que leía `.env.example` con `read_text()`
sin encoding (cp1252 en Windows; la "Í" de "EFÍMERAS" rompe). El enunciado
admite rechazar el archivo o mandar filas a cuarentena, pero no que el
`dq_report` dependa del intérprete. Corrección: cualquier `csv.Error` de una
fila es `FILA_CORRUPTA` y el parser continúa; `truncado` y la mitad redundante
de la condición de umbral en `pipeline.py` quedaron sin uso y se eliminaron.
Los conteos con los datos reales no cambiaron.

Otros errores de la IA, más cortos:

- **Afirmación de seguridad falsa.** `tests/test_permisos_db.py:110` se
  llamaba `test_bitacora_append_only_incluso_para_superusuario` y
  `docs/PLAN.md:144` decía que los triggers frenaban "incluso al
  superusuario". Lo cuestioné en el prompt 1; el agente comprobó que
  `ALTER TABLE ... DISABLE TRIGGER` y `SET session_replication_role = replica`
  lo saltan. Corregido en `87267cf`.
- **Test probabilístico.** `tests/test_facturacion_b6.py:42` comprobaba que el
  byte `@` no apareciera en un cifrado aleatorio, lo que falla por azar en
  torno al 16 % de las veces. Apareció como fallo intermitente al correr la
  suite completa. Corregido en `19da3cc`.
- El propio agente detectó y corrigió dos más antes de commitear: offsets
  logueados con claves enteras que rompían el filtro de PII
  (`src/bia/logging_json.py:29`) y `datetime.fromisoformat` con sufijo `Z`,
  que no funciona antes de Python 3.11 (`src/bia/stream/alertas.py`, `4e0b2f6`).

## 5. Qué NO le delegué a la IA, a propósito

- **Las interpretaciones de reglas ambiguas.** El agente analizaba y
  proponía; la decisión de B3 (120 frente a 420) y el alcance de B6 los tomé
  yo, con el texto del enunciado delante.
- **El ritmo y el alcance.** Cada hito empezó con un análisis sin cambios y
  terminó con una verificación que pedí explícitamente. Varias veces limité
  lo que el agente podía tocar ("no agregues lógica que no sea necesaria",
  "no cambies el SQL por esto").
- **Lo que se afirma sobre seguridad.** Pedí que cada afirmación de
  `SECURITY.md` y `THREAT_MODEL.md` tuviera evidencia en el repo y que lo no
  construido se marcara como tal.
- **La validación final.** Correr el contrato en mi máquina y revisar los
  resultados fue mío; ahí aparecieron los fallos de §4.
- **El historial y la entrega.** Los commits van con mi cuenta; el merge a
  `main`, el push final y el zip los hago yo.
