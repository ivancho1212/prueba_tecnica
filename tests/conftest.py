"""Fixtures compartidas. Las pruebas que necesitan Postgres se saltan si no hay
base alcanzable (p. ej. máquina sin Docker); con la infra arriba corren todas."""
from __future__ import annotations

import os

import pytest

from bia import config, db


@pytest.fixture(autouse=True)
def _entorno_aislado():
    """Cada test recibe el entorno tal como estaba: load_dotenv() de un test no
    debe contaminar a los demás."""
    antes = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(antes)


@pytest.fixture(scope="session")
def cfg():
    try:
        return config.cargar()
    except RuntimeError as e:  # sin .env
        pytest.skip(f"sin configuración: {e}")


@pytest.fixture(scope="session")
def db_disponible(cfg):
    try:
        conn = db.conectar(cfg.dsn_superusuario("bia_test_probe"))
    except RuntimeError as e:
        pytest.skip(f"Postgres no alcanzable: {e}")
    conn.close()
    return True


def _conn_factory(cfg, dsn: str):
    conn = db.conectar(dsn)
    yield conn
    conn.rollback()
    conn.close()


@pytest.fixture
def conn_su(cfg, db_disponible):
    yield from _conn_factory(cfg, cfg.dsn_superusuario("bia_test_su"))


@pytest.fixture
def conn_ingesta(cfg, db_disponible):
    yield from _conn_factory(cfg, cfg.dsn_rol("ingesta", "bia_test_ingesta"))


@pytest.fixture
def conn_analista(cfg, db_disponible):
    yield from _conn_factory(cfg, cfg.dsn_rol("analista", "bia_test_analista"))


@pytest.fixture
def conn_auditor(cfg, db_disponible):
    yield from _conn_factory(cfg, cfg.dsn_rol("auditor", "bia_test_auditor"))
