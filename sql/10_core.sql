-- ===========================================================================
-- Esquema core: modelo analítico. Idempotente.
--
-- Convenciones (ver data_classification.yaml y SECURITY.md):
--   *_cifrado  bytea  → AES-256-GCM a nivel de aplicación (Restringido-PII).
--                        El superusuario ve bytes, no datos.
--   *_hmac     bytea  → HMAC-SHA256 del valor normalizado: permite joins e
--                        igualdad sin exponer el valor.
--   Las horas se guardan en hora local Colombia (America/Bogota, UTC-5 fijo),
--   sin zona, porque así define el enunciado "mes" y "hora" (B1).
-- ===========================================================================

-- Control de archivos: zona raw lógica. Un registro por (archivo, hash).
-- Es la base de la idempotencia y de la absorción de datos tardíos: un archivo
-- ya cargado con el mismo hash no se vuelve a escribir.
CREATE TABLE IF NOT EXISTS core.control_archivos (
  archivo_id        bigserial PRIMARY KEY,
  nombre            text        NOT NULL,
  origen            text        NOT NULL,            -- consumos | maestros | facturacion
  hash_sha256       text        NOT NULL,
  tamano_bytes      bigint      NOT NULL,
  formato_detectado jsonb       NOT NULL DEFAULT '{}'::jsonb,
  filas_leidas      integer     NOT NULL DEFAULT 0,
  filas_cargadas    integer     NOT NULL DEFAULT 0,
  filas_rechazadas  integer     NOT NULL DEFAULT 0,
  estado            text        NOT NULL CHECK (estado IN ('OK','PARCIAL','RECHAZADO')),
  primera_corrida   uuid        NOT NULL,
  ultima_corrida    uuid        NOT NULL,
  primera_vez       timestamptz NOT NULL DEFAULT now(),
  ultima_vez        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (nombre, hash_sha256)
);

CREATE TABLE IF NOT EXISTS core.dim_cliente (
  cliente_id                  text  PRIMARY KEY,
  razon_social                text  NOT NULL,          -- Confidencial (empresa, no persona)
  nit_cifrado                 bytea NOT NULL,          -- Restringido-PII
  nit_hmac                    bytea NOT NULL,          -- join/igualdad sin exponer
  email_cifrado               bytea,                   -- Restringido-PII
  email_hmac                  bytea,
  celular_cifrado             bytea,                   -- Restringido-PII
  direccion_cifrada           bytea,                   -- Restringido-PII
  ciudad                      text,                    -- Interno
  departamento                text,                    -- Interno
  representante_legal_cifrado bytea,                   -- Restringido-PII (persona natural)
  observaciones_cifrado       bytea,                   -- Restringido-PII (texto libre original)
  observaciones_enmascaradas  text,                    -- Confidencial (PII sustituida por tokens)
  pii_en_observaciones        boolean NOT NULL DEFAULT false,
  tipos_pii_observaciones     text[]  NOT NULL DEFAULT '{}',
  hash_fila                   text  NOT NULL,          -- idempotencia: no reescribir si no cambió
  archivo_id                  bigint REFERENCES core.control_archivos(archivo_id),
  actualizado_en              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_dim_cliente_nit_hmac   ON core.dim_cliente (nit_hmac);
CREATE INDEX IF NOT EXISTS ix_dim_cliente_email_hmac ON core.dim_cliente (email_hmac);

CREATE TABLE IF NOT EXISTS core.dim_frontera (
  frontera_id    text PRIMARY KEY CHECK (frontera_id ~ '^CO[0-9]{10}$'),
  codigo_sic     text NOT NULL,
  cliente_id     text NOT NULL REFERENCES core.dim_cliente(cliente_id),
  mercado        text,
  operador_red   text,
  nivel_tension  smallint,
  fecha_registro date,
  estado         text,
  hash_fila      text NOT NULL,
  archivo_id     bigint REFERENCES core.control_archivos(archivo_id),
  actualizado_en timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS core.limite_frontera (
  frontera_id    text PRIMARY KEY REFERENCES core.dim_frontera(frontera_id),
  min_kwh        numeric NOT NULL CHECK (min_kwh >= 0),
  max_kwh        numeric NOT NULL CHECK (max_kwh >= min_kwh),
  hash_fila      text NOT NULL,
  archivo_id     bigint REFERENCES core.control_archivos(archivo_id),
  actualizado_en timestamptz NOT NULL DEFAULT now()
);

-- Hechos: solo lecturas VÁLIDAS (B4). Lo inválido vive en quarantine.
CREATE TABLE IF NOT EXISTS core.fact_consumo_horario (
  frontera_id     text      NOT NULL REFERENCES core.dim_frontera(frontera_id),
  ts_local        timestamp NOT NULL,                 -- hora Colombia, sin zona (B1)
  mes             date      NOT NULL GENERATED ALWAYS AS (date_trunc('month', ts_local)::date) STORED,
  consumo_kwh     numeric   NOT NULL CHECK (consumo_kwh >= 0 AND consumo_kwh <= 1000000),
  version_lectura integer   NOT NULL,
  calidad_lectura text,
  unidad_origen   text      NOT NULL DEFAULT 'kWh',   -- 'MWh' si fue convertida (B2)
  archivo_nombre  text      NOT NULL,                 -- desempate B3 entre archivos
  archivo_id      bigint    REFERENCES core.control_archivos(archivo_id),
  fila_origen     integer,
  actualizado_en  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (frontera_id, ts_local)
);
CREATE INDEX IF NOT EXISTS ix_fact_consumo_mes ON core.fact_consumo_horario (mes, frontera_id);

CREATE TABLE IF NOT EXISTS core.fact_facturacion (
  factura_id             text    PRIMARY KEY,
  periodo                date    NOT NULL,
  frontera_id            text    NOT NULL REFERENCES core.dim_frontera(frontera_id),
  consumo_facturado_kwh  numeric NOT NULL,
  tarifa_cop_kwh         numeric,
  valor_total_cop        numeric,
  componentes            jsonb,                       -- [{concepto, valor_cop_kwh}]
  metodo_pago_tipo       text,
  cuenta_enmascarada     text,                        -- ya viene enmascarada en origen
  cliente_razon_social   text,                        -- Confidencial
  cliente_nit_cifrado    bytea,                       -- Restringido-PII (PII duplicada en origen)
  cliente_nit_hmac       bytea,
  cliente_email_cifrado  bytea,                       -- Restringido-PII
  cliente_email_hmac     bytea,
  nit_coincide_maestro   boolean,                     -- join por HMAC, sin exponer el NIT
  archivo_id             bigint REFERENCES core.control_archivos(archivo_id),
  actualizado_en         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_fact_facturacion_periodo ON core.fact_facturacion (periodo, frontera_id);
