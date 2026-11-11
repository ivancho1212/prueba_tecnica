"""Configuración por variables de entorno (C6: cero credenciales en código).

Carga `.env` de la raíz del repo si existe (sin pisar variables ya definidas)
y expone un objeto `Settings` inmutable. Las credenciales nunca se imprimen:
`repr` las omite.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
ROLES_FIJOS = {"ingesta": "rol_ingesta", "analista": "rol_analista", "auditor": "rol_auditor"}


def _req(nombre: str) -> str:
    v = os.environ.get(nombre, "")
    if not v:
        raise RuntimeError(f"variable de entorno obligatoria ausente: {nombre} (¿corriste `make setup`?)")
    return v


@dataclass(frozen=True)
class Settings:
    pg_host: str
    pg_port: int
    pg_database: str
    superuser: str
    superuser_password: str = field(repr=False)
    roles: dict[str, tuple[str, str]] = field(repr=False)  # nombre_logico -> (usuario, password)
    enc_key: str = field(repr=False)
    hmac_key: str = field(repr=False)
    kafka_bootstrap: str
    kafka_topic: str
    data_dir: Path
    out_dir: Path
    log_dir: Path
    log_level: str

    def dsn(self, usuario: str, password: str, application_name: str) -> str:
        return (f"host={self.pg_host} port={self.pg_port} dbname={self.pg_database} "
                f"user={usuario} password={password} application_name={application_name} "
                f"connect_timeout=10")

    def dsn_superusuario(self, app: str = "bia_bootstrap") -> str:
        return self.dsn(self.superuser, self.superuser_password, app)

    def dsn_rol(self, rol: str, app: str | None = None) -> str:
        usuario, pwd = self.roles[rol]
        return self.dsn(usuario, pwd, app or f"bia_{rol}")


def cargar(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or RAIZ / ".env", override=False)
    roles: dict[str, tuple[str, str]] = {}
    for logico, fijo in ROLES_FIJOS.items():
        usuario = _req(f"PG_ROL_{logico.upper()}_USER")
        if usuario != fijo:
            raise RuntimeError(f"PG_ROL_{logico.upper()}_USER debe ser '{fijo}' (lo exige sql/00_roles.sql)")
        roles[logico] = (usuario, _req(f"PG_ROL_{logico.upper()}_PASSWORD"))
    return Settings(
        pg_host=os.environ.get("PG_HOST", "localhost"),
        pg_port=int(os.environ.get("PG_PORT", "5432")),
        pg_database=os.environ.get("PG_DATABASE", "bia_datos"),
        superuser=_req("PG_SUPERUSER"),
        superuser_password=_req("PG_SUPERPASSWORD"),
        roles=roles,
        enc_key=_req("BIA_ENC_KEY"),
        hmac_key=_req("BIA_HMAC_KEY"),
        kafka_bootstrap=os.environ.get("KAFKA_BOOTSTRAP", "localhost:9092"),
        kafka_topic=os.environ.get("KAFKA_TOPIC", "mediciones.raw"),
        data_dir=RAIZ / os.environ.get("BIA_DATA_DIR", "data"),
        out_dir=RAIZ / os.environ.get("BIA_OUT_DIR", "out"),
        log_dir=RAIZ / os.environ.get("BIA_LOG_DIR", "logs"),
        log_level=os.environ.get("BIA_LOG_LEVEL", "INFO"),
    )
