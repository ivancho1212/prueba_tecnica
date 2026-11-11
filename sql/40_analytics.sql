-- ===========================================================================
-- Vistas analíticas: lo único que consulta rol_analista. Idempotente.
-- Regla: ninguna columna Confidencial ni Restringido-PII. Cada columna de
-- estas vistas está declarada en data_classification.yaml como Público o
-- Interno y el pipeline lo verifica al arrancar.
-- Las vistas corren con los privilegios del dueño (security_invoker = false):
-- el analista no necesita, ni tiene, acceso a las tablas base.
-- ===========================================================================
CREATE OR REPLACE VIEW analytics.v_consumo_mensual AS
SELECT frontera_id,
       to_char(mes, 'YYYY-MM')          AS mes,
       round(sum(consumo_kwh), 3)       AS consumo_kwh,
       count(*)::integer                AS horas_con_dato
FROM core.fact_consumo_horario
GROUP BY frontera_id, mes;

CREATE OR REPLACE VIEW analytics.v_consumo_horario AS
SELECT frontera_id, ts_local, consumo_kwh, version_lectura, calidad_lectura, unidad_origen
FROM core.fact_consumo_horario;

CREATE OR REPLACE VIEW analytics.v_frontera AS
SELECT f.frontera_id, f.codigo_sic, f.cliente_id, f.mercado, f.operador_red,
       f.nivel_tension, f.fecha_registro, f.estado, l.min_kwh, l.max_kwh
FROM core.dim_frontera f
LEFT JOIN core.limite_frontera l USING (frontera_id);

-- Cliente SIN ningún atributo de persona ni identificador tributario.
CREATE OR REPLACE VIEW analytics.v_cliente AS
SELECT cliente_id, ciudad, departamento, pii_en_observaciones
FROM core.dim_cliente;

CREATE OR REPLACE VIEW analytics.v_facturacion_mensual AS
SELECT factura_id, to_char(periodo, 'YYYY-MM') AS periodo, frontera_id,
       consumo_facturado_kwh, tarifa_cop_kwh, valor_total_cop, metodo_pago_tipo,
       nit_coincide_maestro
FROM core.fact_facturacion;

-- Calidad: resumen de cuarentena sin payload (el payload puede contener PII).
CREATE OR REPLACE VIEW analytics.v_cuarentena_resumen AS
SELECT origen, archivo_nombre, regla, count(*)::integer AS registros,
       min(rechazado_en) AS primero, max(rechazado_en) AS ultimo
FROM quarantine.registros_rechazados
GROUP BY origen, archivo_nombre, regla;

CREATE OR REPLACE VIEW analytics.v_archivos_procesados AS
SELECT nombre, origen, estado, filas_leidas, filas_cargadas, filas_rechazadas,
       formato_detectado, primera_vez, ultima_vez
FROM core.control_archivos;
