-- ===========================================================================
-- Bitácora append-only (C5). Idempotente.
-- Dos capas: (1) privilegios: nadie salvo el dueño tiene UPDATE/DELETE/TRUNCATE
-- y el dueño los revoca sobre sí mismo en 90_grants.sql; (2) trigger que
-- aborta cualquier UPDATE/DELETE/TRUNCATE aunque llegue con privilegios.
-- Solo un superusuario podría desactivar el trigger: eso es exactamente lo que
-- la propia bitácora y el threat model registran como riesgo residual.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS audit.bitacora (
  evento_id     bigserial   PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT clock_timestamp(),
  corrida_id    uuid        NOT NULL,
  usuario_db    text        NOT NULL DEFAULT current_user,
  usuario_os    text,
  host_origen   text        DEFAULT inet_client_addr()::text,
  componente    text        NOT NULL,            -- bootstrap | batch | stream | test
  etapa         text        NOT NULL,            -- descubrimiento | parseo | carga | reporte | ...
  evento        text        NOT NULL,            -- inicio | fin | archivo_procesado | error | ...
  objeto        text,                            -- tabla/archivo/tópico afectado (nunca datos)
  filas         integer,
  resultado     text        NOT NULL CHECK (resultado IN ('OK','ERROR','PARCIAL','INFO')),
  duracion_ms   integer,
  detalle       jsonb       NOT NULL DEFAULT '{}'::jsonb   -- conteos y metadatos; prohibido PII
);
CREATE INDEX IF NOT EXISTS ix_bitacora_corrida ON audit.bitacora (corrida_id);
CREATE INDEX IF NOT EXISTS ix_bitacora_ts      ON audit.bitacora (ts);

CREATE OR REPLACE FUNCTION audit.fn_solo_append()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'audit.bitacora es append-only: % no permitido', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE OR REPLACE TRIGGER trg_bitacora_solo_append
  BEFORE UPDATE OR DELETE ON audit.bitacora
  FOR EACH ROW EXECUTE FUNCTION audit.fn_solo_append();

CREATE OR REPLACE TRIGGER trg_bitacora_sin_truncate
  BEFORE TRUNCATE ON audit.bitacora
  FOR EACH STATEMENT EXECUTE FUNCTION audit.fn_solo_append();
