from pathlib import Path

import pytest

from bia import config


def test_config_carga_desde_env(monkeypatch, tmp_path: Path):
    for k in list(__import__("os").environ):
        if k.startswith(("PG_", "BIA_", "KAFKA_")):
            monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("\n".join([
        "PG_SUPERUSER=su", "PG_SUPERPASSWORD=p", "PG_PORT=5999",
        "PG_ROL_INGESTA_USER=rol_ingesta", "PG_ROL_INGESTA_PASSWORD=a",
        "PG_ROL_ANALISTA_USER=rol_analista", "PG_ROL_ANALISTA_PASSWORD=b",
        "PG_ROL_AUDITOR_USER=rol_auditor", "PG_ROL_AUDITOR_PASSWORD=c",
        "BIA_ENC_KEY=k1", "BIA_HMAC_KEY=k2",
    ]))
    cfg = config.cargar(env)
    assert cfg.pg_port == 5999
    assert "password" not in repr(cfg) and "k1" not in repr(cfg), "las credenciales no se imprimen"
    assert "user=rol_analista" in cfg.dsn_rol("analista")


def test_nombre_de_rol_distinto_al_fijo_falla(monkeypatch, tmp_path: Path):
    for k in list(__import__("os").environ):
        if k.startswith(("PG_", "BIA_", "KAFKA_")):
            monkeypatch.delenv(k, raising=False)
    env = tmp_path / ".env"
    env.write_text("PG_SUPERUSER=su\nPG_SUPERPASSWORD=p\nPG_ROL_INGESTA_USER=otro\nPG_ROL_INGESTA_PASSWORD=a\n")
    with pytest.raises(RuntimeError, match="rol_ingesta"):
        config.cargar(env)
