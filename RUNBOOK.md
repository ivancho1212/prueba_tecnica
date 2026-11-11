# Runbook operativo

Para quien lo abre a las 2 a.m. Comandos y consultas reales de este repo.
Credenciales: `.env` (nunca pegarlas en un chat ni en un ticket).

```bash
set -a; source .env; set +a
SU="postgresql://$PG_SUPERUSER:$PG_SUPERPASSWORD@localhost:$PG_PORT/$PG_DATABASE"
AU="postgresql://rol_auditor:$PG_ROL_AUDITOR_PASSWORD@localhost:$PG_PORT/$PG_DATABASE"
```

## Cómo corre normalmente

| Paso | Qué hace | Duración esperada | Qué deja |
|---|---|---|---|
| `make up` | Levanta Postgres y Redpanda, espera healthchecks, aplica `sql/*.sql` y verifica que `data_classification.yaml` cubre el modelo | < 1 min | Esquemas, roles, grants, vistas |
| `make run` | Batch completo: maestros → consumos → facturación → reportes. Re-parsea todo, carga en Postgres solo archivos nuevos o cambiados | ~1 s con el paquete actual | `out/consumo_mensual.csv`, `out/dq_report.json`, `out/metrics.json`, `logs/pipeline.log`, una corrida en `audit.bitacora` |
| `make stream` | Crea el tópico si falta, publica con `tools/producer.py`, consume hasta el offset final | ~2–5 s | `out/alertas.json`, sección `stream` en `out/metrics.json` |

Una segunda corrida de `make run` sin archivos nuevos no escribe nada en la
base y deja `out/` byte a byte igual. Conteos de referencia con los datos
entregados: 115.340 filas leídas, 114.545 cargadas, 160 filas en
`consumo_mensual.csv`, 33 alertas. Si cambian sin que hayan cambiado los
archivos, algo se rompió.

## Señales de que algo anda mal

| Síntoma | Dónde se ve | Causa probable |
|---|---|---|
| `make up` falla con `ErrorClasificacion` | salida de `scripts/bootstrap_db.py` | Alguien añadió una columna al DDL sin clasificarla en `data_classification.yaml`. Es a propósito: clasificar y repetir |
| `make run` sale ≠ 0 al arrancar con `analytics expone niveles prohibidos` | stderr, `logs/pipeline.log` | Una vista de `analytics` incluye una columna Confidencial o Restringido-PII |
| `make run` sale ≠ 0 con `InsufficientPrivilege` | `logs/pipeline.log` (`evento: error`) | Código nuevo hace `SELECT *` o lee una columna cifrada como `rol_ingesta`; listar columnas explícitamente |
| Un archivo con `estado: RECHAZADO` y regla `ARCHIVO_CORRUPTO` | `out/metrics.json → archivos`, `analytics.v_archivos_procesados` | Cabecera ilegible o más del 50 % de filas malformadas. Los meses previos no cambian |
| `rechazos_por_motivo` con `FILA_CORRUPTA`, `FILA_MALFORMADA`, `TIMESTAMP_INVALIDO` o `UNIDAD_DESCONOCIDA` nuevos | `out/metrics.json` | Archivo truncado, binario o con un formato que el detector no conoce |
| Conteos del `dq_report.json` distintos sin archivos nuevos | `out/dq_report.json` vs. corrida anterior | Regresión en una regla B1–B5. Correr `make test` |
| `consumo_mensual.csv` sin un mes esperado | `out/metrics.json → meses_detectados` | El archivo no está en `data/consumos/` o fue rechazado entero |
| `make stream` sale ≠ 0 o `motivo_fin` distinto de `offset_final` | `out/metrics.json → stream`, `logs/pipeline.log` (`componente: stream`) | Broker caído (`error`), productor falló, o nada nuevo llegó en 20 s (`inactividad`) / plazo de 240 s (`plazo_total`) |
| `eventos_rechazados_por_motivo` no vacío | `out/metrics.json → stream` | Eventos malformados o de fronteras sin límites en el tópico |
| `make verificar` falla | su salida | `entrega.yaml` no coincide con lo levantado: puerto, rol u objeto |

## Procedimientos

### El pipeline falló a mitad de camino

**Es seguro reintentar `make run` sin limpiar nada.** Cada archivo se carga
en su propia transacción: lo que falló se deshizo entero y lo que terminó
quedó registrado con su hash en `core.control_archivos`. El reintento salta
los archivos ya cargados (`ya_cargado: true` en `metrics.json`) y los upserts
por clave natural no duplican filas.

1. Mirar el último error:
   `grep '"nivel": "ERROR"' logs/pipeline.log | tail -5`
2. Ver qué quedó a medias:
   ```sql
   -- psql "$SU"
   SELECT nombre, estado, filas_leidas, filas_cargadas, filas_rechazadas, ultima_vez
     FROM core.control_archivos ORDER BY ultima_vez DESC LIMIT 10;
   ```
   `PARCIAL` con filas rechazadas es normal (cuarentena). `RECHAZADO` con
   `ARCHIVO_ERROR_CARGA` es un fallo de carga: el motivo está en
   `quarantine.registros_rechazados.motivo`.
3. Corregir la causa (base caída, permisos, disco) y `make run`.
4. Confirmar: dos `make run` seguidos dejan `out/dq_report.json` idéntico.

**No** hacer `TRUNCATE` de tablas ni borrar `control_archivos` "para empezar
limpio": la bitácora no lo permite y se pierde el linaje.

### Llegaron datos corregidos de un mes ya cerrado

1. Dejar el archivo en `data/consumos/` con un nombre que ordene **después**
   del original (p. ej. `consumos_2026-04_correccion2.csv`) y
   `version_lectura` mayor que la cargada. No editar el archivo original: los
   datos de entrada son inmutables.
2. `make run`. La regla B3 hace el resto: mayor versión gana; a igual versión
   gana el archivo posterior en orden lexicográfico.
3. Verificar en `out/dq_report.json` que `correcciones_tardias_aplicadas`
   subió en lo esperado, y en cuarentena las filas reemplazadas:
   ```sql
   SELECT regla, count(*) FROM quarantine.registros_rechazados
    WHERE archivo_nombre LIKE 'consumos_2026-04%' GROUP BY regla;
   -- B3_SUPERADA_POR_VERSION = filas viejas reemplazadas por la corrección
   ```
4. Si la corrección trae la **misma** versión con otro valor, cuenta como
   `DUPLICADO_CONFLICTIVO` y gana por orden de archivo: confirmar con quien la
   envió que eso es lo que quieren.

### La tasa de rechazo se disparó

1. Por regla y archivo, sin tocar payloads:
   ```sql
   SELECT archivo_nombre, regla, registros FROM analytics.v_cuarentena_resumen
    ORDER BY registros DESC LIMIT 20;
   ```
2. Ver qué formato se detectó para ese archivo:
   `SELECT nombre, formato_detectado FROM analytics.v_archivos_procesados ORDER BY ultima_vez DESC;`
   Separador, `ts_utc`, `decimal_coma` y `unidad_defecto` deben coincidir con
   lo que el proveedor dice haber enviado. Un cambio de formato no anunciado
   es la causa más común.
3. B4 (`B4_NULO`, `B4_NEGATIVO`, `B4_OUTLIER`) alto es un problema de los
   medidores, no del pipeline: reportarlo, **no** imputar valores. B5 (huecos)
   sube en consecuencia y también se reporta tal cual.
4. Para ver filas concretas de **consumos**, `payload` está en claro. Para
   clientes y facturación el payload está cifrado a propósito: no descifrarlo
   en una sesión de soporte.

### Sospecha de acceso indebido a PII

**Primeros 15 minutos.**

1. **Mirar sin cambiar nada.** Bitácora como auditor (no como superusuario):
   ```sql
   -- psql "$AU"
   SELECT ts, usuario_db, usuario_os, host_origen, componente, etapa, evento, objeto, resultado
     FROM audit.bitacora ORDER BY evento_id DESC LIMIT 50;
   ```
   Buscar corridas fuera de horario, `usuario_os` desconocidos o hosts
   distintos de `127.0.0.1`.
2. Sesiones abiertas ahora, como superusuario:
   ```sql
   SELECT usename, application_name, client_addr, backend_start, state, left(query, 80)
     FROM pg_stat_activity WHERE datname = current_database();
   ```
   El pipeline se identifica como `bia_batch` y `bia_stream`.
3. ¿Se alteró la bitácora? Si los triggers no están activos, alguien con
   superusuario los desactivó (límite conocido, `SECURITY.md` §6):
   ```sql
   SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid = 'audit.bitacora'::regclass;
   -- esperado: tgenabled = 'O' en las dos filas
   ```
4. Revisar que no haya PII en lo que sale del sistema:
   `grep -cE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}' logs/pipeline.log out/*`
5. **Contener**: cambiar las contraseñas de los roles afectados en `.env` y
   correr `make up` (vuelve a aplicar `sql/00_roles.sql` con las nuevas);
   cortar las sesiones sospechosas con `pg_terminate_backend(pid)`.
6. **Avisar** al responsable de protección de datos (abajo) con hora, rol,
   host y qué tablas. Sin pegar datos personales en el aviso.

**Qué NO hacer.**
- No regenerar ni borrar `BIA_ENC_KEY`/`BIA_HMAC_KEY` "por si acaso": la PII
  ya cifrada quedaría ilegible para siempre. Si la llave pudo filtrarse, se
  rota con un plan de recifrado (el byte de versión de `crypto.py` lo
  permite), no en caliente.
- No borrar `logs/`, `out/` ni filas de la bitácora: son la evidencia.
- No desactivar triggers ni cambiar grants para "investigar más rápido".
- No descifrar PII para "ver qué se llevaron" fuera de un procedimiento
  autorizado.

## Contactos y escalamiento

| Situación | A quién | Cuándo |
|---|---|---|
| Fallo del pipeline o de `make stream` | Dueño del pipeline (equipo de Data) | En horario; fuera de horario si bloquea facturación |
| Proveedor de datos cambió el formato o envió datos corruptos | Dueño del pipeline → contacto del proveedor | Al confirmar en cuarentena |
| Sospecha o confirmación de acceso indebido a PII | Oficial de protección de datos y Seguridad de la información | Inmediato. Si se confirma un incidente sobre datos personales, el oficial gestiona el reporte a la SIC según la Ley 1581 |
| Credenciales o llaves posiblemente expuestas | Seguridad de la información | Inmediato |

Los nombres y canales concretos de cada rol los define el equipo al poner
esto en producción; en la evaluación local no aplican.
