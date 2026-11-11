"""C4 menor privilegio y C5 bitácora append-only, probados como lo hará el
evaluador: conectándose con cada rol e intentando lo que NO debería poder.

Códigos esperados: 42501 insufficient_privilege, 25006 read_only_sql_transaction,
3F000 invalid_schema_name (sin USAGE el esquema "no existe" para el rol)."""
from __future__ import annotations

import uuid

import psycopg2
import pytest

PROHIBIDO = {"42501", "25006", "3F000", "42P01"}


def _falla(conn, sql, params=None):
    with conn.cursor() as cur:
        with pytest.raises(psycopg2.Error) as exc:
            cur.execute(sql, params)
    assert exc.value.pgcode in PROHIBIDO, f"código inesperado {exc.value.pgcode}: {exc.value}"
    conn.rollback()


def _ok(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        try:
            return cur.fetchall()
        except psycopg2.ProgrammingError:
            return None


# ------------------------------------------------------------- analista ----
def test_analista_lee_vistas(conn_analista):
    _ok(conn_analista, "SELECT * FROM analytics.v_consumo_mensual LIMIT 1")
    _ok(conn_analista, "SELECT * FROM analytics.v_cliente LIMIT 1")
    cols = [r[0] for r in _ok(conn_analista,
        "SELECT column_name FROM information_schema.columns WHERE table_schema='analytics'")]
    assert not any(c.endswith(("_cifrado", "_cifrada", "_hmac")) or c.startswith("payload") for c in cols)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM core.fact_consumo_horario LIMIT 1",
    "SELECT * FROM core.dim_cliente LIMIT 1",
    "SELECT nit_cifrado FROM core.dim_cliente LIMIT 1",
    "SELECT * FROM quarantine.registros_rechazados LIMIT 1",
    "SELECT * FROM audit.bitacora LIMIT 1",
    "INSERT INTO core.control_archivos (nombre, origen, hash_sha256, tamano_bytes, estado, primera_corrida, ultima_corrida) "
    "VALUES ('x','x','x',0,'OK',gen_random_uuid(),gen_random_uuid())",
    "DELETE FROM core.fact_consumo_horario",
    "CREATE TABLE analytics.t_fuga (x int)",
    "CREATE TABLE public.t_fuga (x int)",
    "CREATE TEMP TABLE t AS SELECT * FROM core.dim_cliente",
])
def test_analista_no_puede(conn_analista, sql):
    _falla(conn_analista, sql)


# -------------------------------------------------------------- auditor ----
def test_auditor_lee_bitacora_y_nada_mas(conn_auditor):
    filas = _ok(conn_auditor, "SELECT componente, evento, resultado FROM audit.bitacora LIMIT 5")
    assert filas is not None
    for sql in [
        "INSERT INTO audit.bitacora (corrida_id, componente, etapa, evento, resultado) VALUES (gen_random_uuid(),'t','t','t','OK')",
        "UPDATE audit.bitacora SET resultado='OK'",
        "DELETE FROM audit.bitacora",
        "SELECT * FROM core.dim_cliente LIMIT 1",
        "SELECT * FROM core.fact_consumo_horario LIMIT 1",
        "SELECT * FROM analytics.v_consumo_mensual LIMIT 1",
        "SELECT * FROM quarantine.registros_rechazados LIMIT 1",
    ]:
        _falla(conn_auditor, sql)


# -------------------------------------------------------------- ingesta ----
def test_ingesta_escribe_modelo_y_bitacora(conn_ingesta):
    corrida = uuid.uuid4()
    _ok(conn_ingesta,
        "INSERT INTO audit.bitacora (corrida_id, componente, etapa, evento, resultado) VALUES (%s,'test','t','t','INFO')",
        (corrida,))
    _ok(conn_ingesta,
        "INSERT INTO core.control_archivos (nombre, origen, hash_sha256, tamano_bytes, estado, primera_corrida, ultima_corrida) "
        "VALUES ('t.csv','test','h',1,'OK',%s,%s)", (corrida, corrida))
    _ok(conn_ingesta, "SELECT cliente_id, nit_hmac, hash_fila FROM core.dim_cliente LIMIT 1")
    conn_ingesta.rollback()


@pytest.mark.parametrize("sql", [
    "SELECT nit_cifrado FROM core.dim_cliente LIMIT 1",
    "SELECT email_cifrado, celular_cifrado FROM core.dim_cliente LIMIT 1",
    "SELECT representante_legal_cifrado FROM core.dim_cliente LIMIT 1",
    "SELECT observaciones_cifrado FROM core.dim_cliente LIMIT 1",
    "SELECT * FROM core.dim_cliente LIMIT 1",
    "SELECT cliente_nit_cifrado FROM core.fact_facturacion LIMIT 1",
    "SELECT * FROM core.fact_facturacion LIMIT 1",
    "SELECT payload, payload_cifrado FROM quarantine.registros_rechazados LIMIT 1",
    "UPDATE audit.bitacora SET resultado='OK' WHERE evento_id = 1",
    "DELETE FROM audit.bitacora WHERE evento_id = 1",
    "SELECT * FROM audit.bitacora LIMIT 1",
    "SELECT * FROM analytics.v_consumo_mensual LIMIT 1",
    "DELETE FROM core.dim_cliente",
    "DELETE FROM quarantine.registros_rechazados",
    "CREATE TABLE core.t_fuga (x int)",
    "CREATE TABLE public.t_fuga (x int)",
])
def test_ingesta_no_puede(conn_ingesta, sql):
    _falla(conn_ingesta, sql)


# ------------------------------------------------------ append-only (C5) ----
def test_bitacora_append_only_en_sesion_normal_de_superusuario(conn_su):
    """El superusuario ignora los privilegios (capa 1), así que esta prueba
    verifica la capa 2: en una sesión normal el trigger aborta UPDATE/DELETE/
    TRUNCATE. No es inmutabilidad absoluta: un superusuario con capacidad
    administrativa puede desactivar el trigger (ALTER TABLE ... DISABLE TRIGGER
    o session_replication_role = replica). Riesgo residual documentado en
    ADR-004 y en el threat model."""
    with conn_su.cursor() as cur:
        cur.execute("INSERT INTO audit.bitacora (corrida_id, componente, etapa, evento, resultado) "
                    "VALUES (gen_random_uuid(),'test','t','t','INFO') RETURNING evento_id")
        eid = cur.fetchone()[0]
    conn_su.commit()  # la fila debe existir: el trigger es FOR EACH ROW
    for sql in [f"UPDATE audit.bitacora SET resultado='ERROR' WHERE evento_id={eid}",
                f"DELETE FROM audit.bitacora WHERE evento_id={eid}",
                "TRUNCATE audit.bitacora"]:
        with conn_su.cursor() as cur, pytest.raises(psycopg2.Error) as exc:
            cur.execute(sql)
        assert "append-only" in str(exc.value)
        conn_su.rollback()


def test_privilegios_bitacora_revocados_hasta_para_el_duenio(conn_su):
    with conn_su.cursor() as cur:
        cur.execute("""
            SELECT grantee, privilege_type FROM information_schema.role_table_grants
             WHERE table_schema='audit' AND table_name='bitacora'
               AND privilege_type IN ('UPDATE','DELETE','TRUNCATE')""")
        assert cur.fetchall() == []


def test_roles_sin_privilegios_de_sistema(conn_su):
    with conn_su.cursor() as cur:
        cur.execute("""SELECT rolname FROM pg_roles
                        WHERE rolname LIKE 'rol_%' AND (rolsuper OR rolcreaterole OR rolcreatedb OR rolbypassrls OR rolreplication)""")
        assert cur.fetchall() == []
        cur.execute("SELECT has_schema_privilege('rol_analista','public','CREATE'), has_schema_privilege('rol_ingesta','public','CREATE')")
        assert cur.fetchone() == (False, False)


# ------------------------------------------------------ cifrado en reposo (C3) ----
def _pii_del_maestro():
    """Correos y NIT (solo dígitos) reales de data/maestros/clientes.csv. Si el
    archivo no está, la prueba verifica solo el formato del cifrado."""
    import csv, io, re
    from pathlib import Path
    ruta = Path(__file__).resolve().parent.parent / "data" / "maestros" / "clientes.csv"
    if not ruta.exists():
        return set(), set()
    filas = list(csv.DictReader(io.StringIO(ruta.read_bytes().decode("latin-1"))))
    correos = {f["email_contacto"].strip().lower().encode() for f in filas if f.get("email_contacto")}
    nits = {re.sub(r"\D", "", f["nit"]).encode() for f in filas if f.get("nit")}
    return correos, nits


def test_superusuario_no_ve_pii_en_claro(conn_su):
    """Lo que hace quien roba un dump: leer las tablas crudas como superusuario.
    Ninguna columna, cifrada o no, puede contener un correo o un NIT real del
    maestro; las columnas Restringido-PII deben ser bytes con versión 0x01."""
    correos, nits = _pii_del_maestro()
    with conn_su.cursor() as cur:
        cur.execute("""SELECT nit_cifrado, email_cifrado, celular_cifrado, direccion_cifrada,
                              representante_legal_cifrado, observaciones_cifrado,
                              razon_social, ciudad, departamento, observaciones_enmascaradas
                         FROM core.dim_cliente""")
        for fila in cur.fetchall():
            cifradas = [bytes(x) for x in fila[:6] if x is not None]
            claras = " ".join(str(x) for x in fila[6:] if x is not None).lower().encode()
            assert all(c[:1] == b"\x01" for c in cifradas)
            for c in cifradas + [claras]:
                assert not any(e in c for e in correos), "correo del maestro legible"
                assert not any(n in c for n in nits if len(n) >= 9), "NIT del maestro legible"
        cur.execute("""SELECT cliente_nit_cifrado, cliente_email_cifrado,
                              cliente_razon_social, metodo_pago_tipo, cuenta_enmascarada, componentes::text
                         FROM core.fact_facturacion""")
        for fila in cur.fetchall():
            cifradas = [bytes(x) for x in fila[:2] if x is not None]
            claras = " ".join(str(x) for x in fila[2:] if x is not None).lower().encode()
            assert all(c[:1] == b"\x01" for c in cifradas)
            for c in cifradas + [claras]:
                assert not any(e in c for e in correos)
                assert not any(n in c for n in nits if len(n) >= 9)
