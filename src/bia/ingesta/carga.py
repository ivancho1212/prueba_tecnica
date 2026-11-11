"""Carga a Postgres como rol_ingesta. Todo parametrizado, una transacción por
archivo, upserts idempotentes por clave natural. Nunca `SELECT *` (grants por
columna). La bitácora solo recibe INSERT.
"""
from __future__ import annotations

import getpass
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

import psycopg2.extras

from bia.crypto import Cifrador
from bia.ingesta.archivos import ArchivoCrudo, Formato
from bia.ingesta.consumos import Lectura, Rechazo
from bia.ingesta.facturacion import FacturaNormalizada
from bia.ingesta.maestros import ClienteNormalizado

ORIGENES_CON_PII = {"clientes", "facturacion"}


def _sin_nul(texto: str | None) -> str | None:
    """jsonb y text no admiten U+0000: un archivo truncado/binario lo trae."""
    return None if texto is None else texto.replace("\x00", "\ufffd")


@dataclass
class EstadoArchivo:
    archivo_id: int
    ya_cargado: bool      # mismo nombre+hash ya procesado con estado OK → no reescribir filas


class Cargador:
    def __init__(self, conn, corrida_id: uuid.UUID, cifrador: Cifrador):
        self.conn, self.corrida, self.cif = conn, corrida_id, cifrador

    # ------------------------------------------------------------ bitácora --
    def bitacora(self, etapa: str, evento: str, resultado: str = "INFO", objeto: str | None = None,
                 filas: int | None = None, duracion_ms: int | None = None, **detalle) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit.bitacora (corrida_id, usuario_os, componente, etapa, evento, objeto,
                                               filas, resultado, duracion_ms, detalle)
                   VALUES (%s, %s, 'batch', %s, %s, %s, %s, %s, %s, %s)""",
                (self.corrida, getpass.getuser(), etapa, evento, objeto, filas, resultado, duracion_ms,
                 json.dumps(detalle, default=str)),
            )
        self.conn.commit()

    # --------------------------------------------------- control archivos --
    def registrar_archivo(self, a: ArchivoCrudo, origen: str, fmt: Formato | None) -> EstadoArchivo:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT archivo_id, estado FROM core.control_archivos WHERE nombre = %s AND hash_sha256 = %s",
                (a.nombre, a.sha256),
            )
            fila = cur.fetchone()
            if fila:
                cur.execute(
                    "UPDATE core.control_archivos SET ultima_corrida = %s, ultima_vez = now() WHERE archivo_id = %s",
                    (self.corrida, fila[0]),
                )
                return EstadoArchivo(fila[0], fila[1] == "OK")
            cur.execute(
                """INSERT INTO core.control_archivos (nombre, origen, hash_sha256, tamano_bytes, formato_detectado,
                                                      estado, primera_corrida, ultima_corrida)
                   VALUES (%s, %s, %s, %s, %s::jsonb, 'PARCIAL', %s, %s) RETURNING archivo_id""",
                (a.nombre, origen, a.sha256, a.tamano, fmt.como_json() if fmt else "{}", self.corrida, self.corrida),
            )
            return EstadoArchivo(cur.fetchone()[0], False)

    def cerrar_archivo(self, archivo_id: int, leidas: int, cargadas: int, rechazadas: int, estado: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """UPDATE core.control_archivos
                      SET filas_leidas = %s, filas_cargadas = %s, filas_rechazadas = %s, estado = %s,
                          ultima_corrida = %s, ultima_vez = now()
                    WHERE archivo_id = %s""",
                (leidas, cargadas, rechazadas, estado, self.corrida, archivo_id),
            )

    # ----------------------------------------------------------- cuarentena --
    def cuarentena(self, origen: str, archivo_hash: str, rechazos: Iterable[Rechazo]) -> int:
        filas = []
        con_pii = origen in ORIGENES_CON_PII
        for r in rechazos:
            payload_json = _sin_nul(json.dumps(r.payload, ensure_ascii=False, default=str))
            filas.append((
                self.corrida, origen, r.archivo, archivo_hash, r.fila, r.regla, _sin_nul(r.motivo)[:500], _sin_nul(r.clave),
                None if con_pii else payload_json,
                self.cif.cifrar(payload_json) if con_pii else None,
            ))
        if not filas:
            return 0
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO quarantine.registros_rechazados
                       (corrida_id, origen, archivo_nombre, archivo_hash, fila_origen, regla, motivo, clave,
                        payload, payload_cifrado)
                   VALUES %s
                   ON CONFLICT (archivo_hash, fila_origen, regla) DO NOTHING""",
                filas, template="(%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)", page_size=2000,
            )
        return len(filas)

    # ------------------------------------------------------------- maestros --
    def upsert_clientes(self, clientes: list[ClienteNormalizado], archivo_id: int) -> int:
        """UPDATE-si-cambió y luego INSERT-si-no-existe, en vez de ON CONFLICT DO
        UPDATE: Postgres exige SELECT sobre toda columna referenciada vía
        EXCLUDED, y rol_ingesta NO puede leer las columnas cifradas (grant por
        columna). Así el rol escribe PII sin poder leerla. Un solo escritor
        (el batch), dentro de la transacción del archivo: sin carrera."""
        if not clientes:
            return 0
        sql_update = """
            UPDATE core.dim_cliente SET
                razon_social = %(razon_social)s, nit_cifrado = %(nit_cifrado)s, nit_hmac = %(nit_hmac)s,
                email_cifrado = %(email_cifrado)s, email_hmac = %(email_hmac)s,
                celular_cifrado = %(celular_cifrado)s, direccion_cifrada = %(direccion_cifrada)s,
                ciudad = %(ciudad)s, departamento = %(departamento)s,
                representante_legal_cifrado = %(representante_legal_cifrado)s,
                observaciones_cifrado = %(observaciones_cifrado)s,
                observaciones_enmascaradas = %(observaciones_enmascaradas)s,
                pii_en_observaciones = %(pii_en_observaciones)s, tipos_pii_observaciones = %(tipos_pii)s,
                hash_fila = %(hash_fila)s, archivo_id = %(archivo_id)s, actualizado_en = now()
            WHERE cliente_id = %(cliente_id)s AND hash_fila IS DISTINCT FROM %(hash_fila)s"""
        sql_insert = """
            INSERT INTO core.dim_cliente (cliente_id, razon_social, nit_cifrado, nit_hmac, email_cifrado,
                email_hmac, celular_cifrado, direccion_cifrada, ciudad, departamento,
                representante_legal_cifrado, observaciones_cifrado, observaciones_enmascaradas,
                pii_en_observaciones, tipos_pii_observaciones, hash_fila, archivo_id)
            SELECT %(cliente_id)s, %(razon_social)s, %(nit_cifrado)s, %(nit_hmac)s, %(email_cifrado)s,
                %(email_hmac)s, %(celular_cifrado)s, %(direccion_cifrada)s, %(ciudad)s, %(departamento)s,
                %(representante_legal_cifrado)s, %(observaciones_cifrado)s, %(observaciones_enmascaradas)s,
                %(pii_en_observaciones)s, %(tipos_pii)s, %(hash_fila)s, %(archivo_id)s
            WHERE NOT EXISTS (SELECT 1 FROM core.dim_cliente WHERE cliente_id = %(cliente_id)s)"""
        with self.conn.cursor() as cur:
            for c in clientes:
                params = {**c.__dict__, "archivo_id": archivo_id}
                cur.execute(sql_update, params)
                if cur.rowcount == 0:
                    cur.execute(sql_insert, params)
        return len(clientes)

    def upsert_fronteras(self, fronteras: list[dict], archivo_id: int) -> int:
        if not fronteras:
            return 0
        filas = [(f["frontera_id"], f["codigo_sic"], f["cliente_id"], f["mercado"], f["operador_red"],
                  f["nivel_tension"], f["fecha_registro"], f["estado"], f["hash_fila"], archivo_id) for f in fronteras]
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO core.dim_frontera (frontera_id, codigo_sic, cliente_id, mercado, operador_red,
                        nivel_tension, fecha_registro, estado, hash_fila, archivo_id)
                   VALUES %s
                   ON CONFLICT (frontera_id) DO UPDATE SET
                        codigo_sic = EXCLUDED.codigo_sic, cliente_id = EXCLUDED.cliente_id,
                        mercado = EXCLUDED.mercado, operador_red = EXCLUDED.operador_red,
                        nivel_tension = EXCLUDED.nivel_tension, fecha_registro = EXCLUDED.fecha_registro,
                        estado = EXCLUDED.estado, hash_fila = EXCLUDED.hash_fila,
                        archivo_id = EXCLUDED.archivo_id, actualizado_en = now()
                   WHERE core.dim_frontera.hash_fila IS DISTINCT FROM EXCLUDED.hash_fila""",
                filas, page_size=500,
            )
        return len(filas)

    def upsert_limites(self, limites: list[dict], archivo_id: int) -> int:
        if not limites:
            return 0
        filas = [(l["frontera_id"], l["min_kwh"], l["max_kwh"], l["hash_fila"], archivo_id) for l in limites]
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO core.limite_frontera (frontera_id, min_kwh, max_kwh, hash_fila, archivo_id)
                   VALUES %s
                   ON CONFLICT (frontera_id) DO UPDATE SET
                        min_kwh = EXCLUDED.min_kwh, max_kwh = EXCLUDED.max_kwh, hash_fila = EXCLUDED.hash_fila,
                        archivo_id = EXCLUDED.archivo_id, actualizado_en = now()
                   WHERE core.limite_frontera.hash_fila IS DISTINCT FROM EXCLUDED.hash_fila""",
                filas, page_size=500,
            )
        return len(filas)

    def upsert_facturacion(self, facturas: list[FacturaNormalizada], archivo_id: int) -> int:
        """Mismo patrón que upsert_clientes: UPDATE si cambió el hash, INSERT si no
        existe. Evita ON CONFLICT/EXCLUDED sobre columnas cifradas que
        rol_ingesta no puede leer, y no recifra lo que no cambió."""
        if not facturas:
            return 0
        sql_update = """
            UPDATE core.fact_facturacion SET
                periodo = %(periodo)s, frontera_id = %(frontera_id)s,
                consumo_facturado_kwh = %(consumo_facturado_kwh)s, tarifa_cop_kwh = %(tarifa_cop_kwh)s,
                valor_total_cop = %(valor_total_cop)s, componentes = %(componentes)s::jsonb,
                metodo_pago_tipo = %(metodo_pago_tipo)s, cuenta_enmascarada = %(cuenta_enmascarada)s,
                cliente_razon_social = %(cliente_razon_social)s,
                cliente_nit_cifrado = %(cliente_nit_cifrado)s, cliente_nit_hmac = %(cliente_nit_hmac)s,
                cliente_email_cifrado = %(cliente_email_cifrado)s, cliente_email_hmac = %(cliente_email_hmac)s,
                hash_fila = %(hash_fila)s, archivo_id = %(archivo_id)s, actualizado_en = now()
            WHERE factura_id = %(factura_id)s AND hash_fila IS DISTINCT FROM %(hash_fila)s"""
        sql_insert = """
            INSERT INTO core.fact_facturacion (factura_id, periodo, frontera_id, consumo_facturado_kwh,
                tarifa_cop_kwh, valor_total_cop, componentes, metodo_pago_tipo, cuenta_enmascarada,
                cliente_razon_social, cliente_nit_cifrado, cliente_nit_hmac, cliente_email_cifrado,
                cliente_email_hmac, hash_fila, archivo_id)
            SELECT %(factura_id)s, %(periodo)s, %(frontera_id)s, %(consumo_facturado_kwh)s,
                %(tarifa_cop_kwh)s, %(valor_total_cop)s, %(componentes)s::jsonb, %(metodo_pago_tipo)s,
                %(cuenta_enmascarada)s, %(cliente_razon_social)s, %(cliente_nit_cifrado)s, %(cliente_nit_hmac)s,
                %(cliente_email_cifrado)s, %(cliente_email_hmac)s, %(hash_fila)s, %(archivo_id)s
            WHERE NOT EXISTS (SELECT 1 FROM core.fact_facturacion WHERE factura_id = %(factura_id)s)"""
        with self.conn.cursor() as cur:
            for f in facturas:
                params = {**f.__dict__, "archivo_id": archivo_id}
                cur.execute(sql_update, params)
                if cur.rowcount == 0:
                    cur.execute(sql_insert, params)
        return len(facturas)

    def fronteras_en_maestro(self) -> set[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT frontera_id FROM core.dim_frontera")
            return {r[0] for r in cur.fetchall()}

    # ------------------------------------------------------------- consumos --
    def upsert_consumos(self, lecturas: list[Lectura], archivo_ids: dict[str, int]) -> int:
        """Upsert con la regla B3 también entre archivos: gana mayor versión; en
        empate, el archivo lexicográficamente posterior (COLLATE "C" = orden de
        bytes, igual que Python). Reinsertar lo mismo no toca la fila."""
        if not lecturas:
            return 0
        filas = [(l.frontera_id, l.ts_local, l.valor_kwh, l.version, l.calidad, l.unidad_origen,
                  l.archivo, archivo_ids[l.archivo], l.fila) for l in lecturas]
        with self.conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO core.fact_consumo_horario (frontera_id, ts_local, consumo_kwh, version_lectura,
                        calidad_lectura, unidad_origen, archivo_nombre, archivo_id, fila_origen)
                   VALUES %s
                   ON CONFLICT (frontera_id, ts_local) DO UPDATE SET
                        consumo_kwh = EXCLUDED.consumo_kwh, version_lectura = EXCLUDED.version_lectura,
                        calidad_lectura = EXCLUDED.calidad_lectura, unidad_origen = EXCLUDED.unidad_origen,
                        archivo_nombre = EXCLUDED.archivo_nombre, archivo_id = EXCLUDED.archivo_id,
                        fila_origen = EXCLUDED.fila_origen, actualizado_en = now()
                   WHERE EXCLUDED.version_lectura > core.fact_consumo_horario.version_lectura
                      OR (EXCLUDED.version_lectura = core.fact_consumo_horario.version_lectura
                          AND EXCLUDED.archivo_nombre COLLATE "C" > core.fact_consumo_horario.archivo_nombre COLLATE "C")
                      OR (EXCLUDED.version_lectura = core.fact_consumo_horario.version_lectura
                          AND EXCLUDED.archivo_nombre = core.fact_consumo_horario.archivo_nombre
                          AND EXCLUDED.consumo_kwh IS DISTINCT FROM core.fact_consumo_horario.consumo_kwh)""",
                filas, page_size=5000,
            )
        return len(filas)

    # ------------------------------------------------------------- lectura --
    def consumo_mensual(self) -> list[tuple[str, str, Decimal, int]]:
        with self.conn.cursor() as cur:
            cur.execute(
                """SELECT frontera_id, to_char(mes, 'YYYY-MM'), sum(consumo_kwh), count(*)
                     FROM core.fact_consumo_horario
                    GROUP BY frontera_id, mes ORDER BY frontera_id, mes"""
            )
            return cur.fetchall()
