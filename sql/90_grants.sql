-- ===========================================================================
-- Menor privilegio real (C4). Idempotente: REVOKE/GRANT son declarativos.
--
--  rol_ingesta  escribe el modelo y la cuarentena, inserta en la bitácora.
--               NO puede leer las columnas PII (ni cifradas) de dim_cliente ni
--               de fact_facturacion: grant a nivel de columna.
--  rol_analista solo SELECT sobre analytics.*. Nada en core/quarantine/audit.
--  rol_auditor  solo SELECT sobre audit.bitacora.
-- ===========================================================================

-- Punto de partida limpio
REVOKE ALL ON ALL TABLES    IN SCHEMA core, quarantine, audit, analytics FROM rol_ingesta, rol_analista, rol_auditor;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA core, quarantine, audit            FROM rol_ingesta, rol_analista, rol_auditor;
REVOKE ALL ON SCHEMA core, quarantine, audit, analytics                  FROM rol_ingesta, rol_analista, rol_auditor;

DO $$ BEGIN
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO rol_ingesta, rol_analista, rol_auditor', current_database());
END $$;

-- ---------------------------------------------------------------- ingesta --
GRANT USAGE ON SCHEMA core, quarantine, audit TO rol_ingesta;

GRANT SELECT, INSERT, UPDATE ON core.control_archivos    TO rol_ingesta;
GRANT SELECT, INSERT, UPDATE ON core.dim_frontera        TO rol_ingesta;
GRANT SELECT, INSERT, UPDATE ON core.limite_frontera     TO rol_ingesta;
GRANT SELECT, INSERT, UPDATE ON core.fact_consumo_horario TO rol_ingesta;

-- dim_cliente: escribe todo, lee solo lo no sensible (lo justo para el upsert
-- idempotente por hash_fila y para resolver el join por HMAC).
GRANT INSERT, UPDATE ON core.dim_cliente TO rol_ingesta;
GRANT SELECT (cliente_id, razon_social, ciudad, departamento, pii_en_observaciones,
              tipos_pii_observaciones, hash_fila, archivo_id, actualizado_en,
              nit_hmac, email_hmac, observaciones_enmascaradas)
  ON core.dim_cliente TO rol_ingesta;

GRANT INSERT, UPDATE ON core.fact_facturacion TO rol_ingesta;
GRANT SELECT (factura_id, periodo, frontera_id, consumo_facturado_kwh, tarifa_cop_kwh,
              valor_total_cop, componentes, metodo_pago_tipo, cuenta_enmascarada,
              cliente_razon_social, cliente_nit_hmac, cliente_email_hmac,
              nit_coincide_maestro, hash_fila, archivo_id, actualizado_en)
  ON core.fact_facturacion TO rol_ingesta;

GRANT INSERT ON quarantine.registros_rechazados TO rol_ingesta;
GRANT SELECT (rechazo_id, corrida_id, origen, archivo_nombre, archivo_hash, fila_origen,
              regla, motivo, clave, rechazado_en)
  ON quarantine.registros_rechazados TO rol_ingesta;

GRANT INSERT ON audit.bitacora TO rol_ingesta;           -- solo INSERT: append-only
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, quarantine, audit TO rol_ingesta;

-- --------------------------------------------------------------- analista --
GRANT USAGE  ON SCHEMA analytics TO rol_analista;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO rol_analista;   -- solo vistas
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO rol_analista;

-- ---------------------------------------------------------------- auditor --
GRANT USAGE  ON SCHEMA audit TO rol_auditor;
GRANT SELECT ON audit.bitacora TO rol_auditor;

-- ------------------------------------------------------ append-only (C5) --
-- Ni siquiera el dueño conserva UPDATE/DELETE/TRUNCATE sobre la bitácora.
-- (Un superusuario ignora los privilegios: por eso además existe el trigger.)
DO $$
DECLARE duenio text;
BEGIN
  SELECT pg_get_userbyid(relowner) INTO duenio FROM pg_class
   WHERE oid = 'audit.bitacora'::regclass;
  EXECUTE format('REVOKE UPDATE, DELETE, TRUNCATE ON audit.bitacora FROM %I', duenio);
END $$;
REVOKE UPDATE, DELETE, TRUNCATE ON audit.bitacora FROM PUBLIC, rol_ingesta, rol_analista, rol_auditor;
