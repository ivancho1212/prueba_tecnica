#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# SOLO DESARROLLO. Levanta un Postgres 16 local sin Docker ni root, usando los
# binarios embebidos de Zonky (Maven Central). Útil cuando la máquina de
# desarrollo no tiene Docker. El evaluador NO usa esto: usa docker-compose.
#
#   scripts/dev_pg_sin_docker.sh start   # descarga (1 vez), initdb, arranca
#   scripts/dev_pg_sin_docker.sh stop
#   scripts/dev_pg_sin_docker.sh reset   # borra el cluster y vuelve a crearlo
#
# Lee credenciales de .env y escucha en PGLOCAL_PORT (default 8432). Para que
# el pipeline apunte aquí: PG_PORT=8432 make run  (el entorno pisa a .env).
# ---------------------------------------------------------------------------
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="${PGLOCAL_DIR:-/tmp/bia-pg16}"   # fuera del repo; cambiar si /tmp es pequeño
PORT="${PGLOCAL_PORT:-8432}"
VER="${PGLOCAL_VER:-16.15.0}"
BIN="$DIR/dist/bin"; DATA="$DIR/data"; LOG="$DIR/postgres.log"
SOCK="${PGLOCAL_SOCK:-/tmp/bia-pg16-sock}"   # corto: el socket Unix admite máx. 107 bytes
BASE="https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-linux-amd64"

[ -f "$RAIZ/.env" ] || { echo "falta .env: corre 'make setup'"; exit 1; }
set -a; . "$RAIZ/.env"; set +a

descargar() {
  [ -x "$BIN/postgres" ] && return
  mkdir -p "$DIR"; echo ">> descargando postgres $VER (zonky)"
  curl -sSL -o "$DIR/pg.jar" "$BASE/$VER/embedded-postgres-binaries-linux-amd64-$VER.jar"
  python3 -c "import zipfile,sys;z=zipfile.ZipFile(sys.argv[1]);n=[x for x in z.namelist() if x.endswith('.txz')][0];z.extract(n,sys.argv[2])" "$DIR/pg.jar" "$DIR"
  mkdir -p "$DIR/dist"; tar -xJf "$DIR"/*.txz -C "$DIR/dist"
}
initdb_si_falta() {
  [ -f "$DATA/PG_VERSION" ] && return
  echo ">> initdb (superusuario $PG_SUPERUSER, scram-sha-256)"
  PWF="$(mktemp)"; printf '%s' "$PG_SUPERPASSWORD" > "$PWF"
  "$BIN/initdb" -D "$DATA" -U "$PG_SUPERUSER" --pwfile="$PWF" --auth=scram-sha-256 -E UTF8 --locale=C.UTF-8 >/dev/null
  rm -f "$PWF"
  { echo "port = $PORT"; echo "listen_addresses = '127.0.0.1'"; echo "unix_socket_directories = '$SOCK'"; } >> "$DATA/postgresql.conf"
}
start() {
  descargar; initdb_si_falta; mkdir -p "$SOCK"
  if "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; then echo ">> ya corre en 127.0.0.1:$PORT"; else
    "$BIN/pg_ctl" -D "$DATA" -l "$LOG" -w start >/dev/null; echo ">> postgres arriba en 127.0.0.1:$PORT"; fi
  # Zonky no trae psql: la base se crea con psycopg2 desde el venv del proyecto.
  "$RAIZ/.venv/bin/python" - "$PORT" <<'PY'
import os, sys, psycopg2
from psycopg2 import sql
conn = psycopg2.connect(host="127.0.0.1", port=int(sys.argv[1]), dbname="postgres",
                        user=os.environ["PG_SUPERUSER"], password=os.environ["PG_SUPERPASSWORD"])
conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (os.environ["PG_DATABASE"],))
    if not cur.fetchone():
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(os.environ["PG_DATABASE"])))
conn.close()
PY
  echo ">> base $PG_DATABASE lista. Usa: PG_PORT=$PORT .venv/bin/python scripts/bootstrap_db.py"
}
stop()  { "$BIN/pg_ctl" -D "$DATA" -m fast -w stop >/dev/null 2>&1 && echo ">> detenido" || echo ">> no estaba corriendo"; }
reset() { stop; rm -rf "$DATA"; start; }
case "${1:-}" in start) start;; stop) stop;; reset) reset;; *) echo "uso: $0 start|stop|reset"; exit 2;; esac
