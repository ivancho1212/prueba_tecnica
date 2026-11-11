"""scripts/bootstrap_env.py: crea .env desde la plantilla y genera llaves sin pisar valores."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _cargar(tmp: Path):
    spec = importlib.util.spec_from_file_location("bootstrap_env", RAIZ / "scripts" / "bootstrap_env.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod.EJEMPLO = tmp / ".env.example"
    mod.DESTINO = tmp / ".env"
    return mod


def _parse(p: Path) -> dict[str, str]:
    kv = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip() and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            kv[k] = v
    return kv


def test_crea_env_y_genera_llaves(tmp_path: Path):
    (tmp_path / ".env.example").write_text("PG_HOST=localhost\nBIA_ENC_KEY=\nBIA_HMAC_KEY=\n")
    mod = _cargar(tmp_path)
    assert mod.main() == 0
    env = _parse(tmp_path / ".env")
    assert env["PG_HOST"] == "localhost"
    assert len(env["BIA_ENC_KEY"]) >= 32 and len(env["BIA_HMAC_KEY"]) >= 32
    assert env["BIA_ENC_KEY"] != env["BIA_HMAC_KEY"]


def test_idempotente_no_pisa_llaves_existentes(tmp_path: Path):
    (tmp_path / ".env.example").write_text("PG_HOST=localhost\nBIA_ENC_KEY=\nNUEVA_VAR=x\n")
    (tmp_path / ".env").write_text("PG_HOST=otro\nBIA_ENC_KEY=llave-existente\n")
    mod = _cargar(tmp_path)
    assert mod.main() == 0
    env = _parse(tmp_path / ".env")
    assert env["BIA_ENC_KEY"] == "llave-existente", "una llave existente jamás se regenera"
    assert env["PG_HOST"] == "otro", "valores existentes se respetan"
    assert env["NUEVA_VAR"] == "x", "variables nuevas de la plantilla se agregan"


def test_plantilla_real_no_trae_llaves_ni_env_versionado():
    ejemplo = _parse(RAIZ / ".env.example")
    assert ejemplo["BIA_ENC_KEY"] == "" and ejemplo["BIA_HMAC_KEY"] == ""
    gitignore = (RAIZ / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
