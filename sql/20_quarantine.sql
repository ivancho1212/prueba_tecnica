-- ===========================================================================
-- Cuarentena: lo rechazado no se borra, se consulta. Idempotente.
-- Un registro por (archivo, fila, regla). El payload de fuentes con PII se
-- guarda cifrado; el de consumos en claro (no contiene PII).
-- ===========================================================================
CREATE TABLE IF NOT EXISTS quarantine.registros_rechazados (
  rechazo_id      bigserial PRIMARY KEY,
  corrida_id      uuid        NOT NULL,
  origen          text        NOT NULL,          -- consumos | clientes | fronteras | limites | facturacion | archivo
  archivo_nombre  text        NOT NULL,
  archivo_hash    text        NOT NULL,
  fila_origen     integer,                       -- NULL cuando se rechaza el archivo entero
  regla           text        NOT NULL,          -- B4_NULO | B4_NEGATIVO | B4_OUTLIER | B6_HUERFANA | ARCHIVO_CORRUPTO | ...
  motivo          text        NOT NULL,
  clave           text,                          -- identificador no sensible (frontera_id|ts, factura_id, cliente_id)
  payload         jsonb,                         -- fila original SIN PII (consumos, fronteras, límites)
  payload_cifrado bytea,                         -- fila original cifrada cuando la fuente tiene PII
  rechazado_en    timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (archivo_hash, fila_origen, regla)
);
CREATE INDEX IF NOT EXISTS ix_rechazos_regla  ON quarantine.registros_rechazados (regla);
CREATE INDEX IF NOT EXISTS ix_rechazos_origen ON quarantine.registros_rechazados (origen, archivo_nombre);
