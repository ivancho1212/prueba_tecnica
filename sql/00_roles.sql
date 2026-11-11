-- ===========================================================================
-- Roles de menor privilegio (C4). Idempotente.
-- Las contraseñas llegan por set_config('bia.pwd_*') desde scripts/bootstrap_db.py;
-- este archivo no contiene ningún secreto.
-- ===========================================================================
DO $$
DECLARE
  r record;
BEGIN
  FOR r IN SELECT * FROM (VALUES
      ('rol_ingesta',  current_setting('bia.pwd_ingesta',  true)),
      ('rol_analista', current_setting('bia.pwd_analista', true)),
      ('rol_auditor',  current_setting('bia.pwd_auditor',  true))
    ) AS t(nombre, pwd)
  LOOP
    IF r.pwd IS NULL OR r.pwd = '' THEN
      RAISE EXCEPTION 'falta la contraseña para el rol %', r.nombre;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.nombre) THEN
      EXECUTE format('CREATE ROLE %I LOGIN', r.nombre);
    END IF;
    -- Sin privilegios de sistema, sin herencia implícita, contraseña sincronizada
    -- con .env en cada `make up`, tiempo de conexión y de sentencia acotados.
    EXECUTE format(
      'ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD %L',
      r.nombre, r.pwd);
    EXECUTE format('ALTER ROLE %I SET statement_timeout = %L', r.nombre, '120s');
  END LOOP;
END $$;

-- El analista jamás escribe: cualquier sentencia de escritura falla aunque
-- alguien le conceda un GRANT por error.
ALTER ROLE rol_analista SET default_transaction_read_only = on;
ALTER ROLE rol_auditor  SET default_transaction_read_only = on;
