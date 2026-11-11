-- ===========================================================================
-- Esquemas. Idempotente.
--   core        modelo analítico (hechos, dimensiones, control de archivos)
--   quarantine  registros rechazados con su motivo (nunca se borran)
--   audit       bitácora append-only de corridas
--   analytics   vistas sin PII: lo único que ve rol_analista
-- ===========================================================================
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS analytics;

-- Nadie crea objetos en public ni lo usa como escondite.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
-- Conectarse a la base requiere grant explícito.
DO $$ BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
END $$;
