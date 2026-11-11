# ===========================================================================
# CONTRATO DE EJECUCION  --  NO RENOMBRES NI ELIMINES ESTOS TARGETS
#
# El evaluador corre: setup → up → run → run → stream → test → down.
# Si un target no existe o falla, se pierden los puntos asociados.
# `make verificar` ya viene resuelto y no se toca.
# ===========================================================================

SHELL        := /bin/bash
.SHELLFLAGS  := -eu -o pipefail -c
.DEFAULT_GOAL := help

VENV    := .venv
PY      := $(VENV)/bin/python
PYTHON3 ?= python3
COMPOSE := docker compose
export PYTHONPATH := src

.PHONY: setup up run stream test down clean verificar audit help

## Instala dependencias. Idempotente y sin interacción.
## Crea el venv, instala requirements.txt y genera .env (con llaves aleatorias)
## si no existe. Si el python del sistema no trae ensurepip, arranca pip con
## get-pip.py.
setup:
	@if [ ! -x "$(PY)" ]; then \
	  $(PYTHON3) -m venv $(VENV) 2>/dev/null || $(PYTHON3) -m venv --without-pip $(VENV); \
	fi
	@if ! $(PY) -m pip --version >/dev/null 2>&1; then \
	  echo ">> pip no disponible en el venv: arrancando con get-pip.py"; \
	  curl -sSL https://bootstrap.pypa.io/get-pip.py | $(PY) - -q; \
	fi
	@$(PY) -m pip install -q --upgrade pip
	@$(PY) -m pip install -q -r requirements.txt
	@$(PYTHON3) scripts/bootstrap_env.py
	@mkdir -p out logs
	@echo ">> setup OK"

## Levanta Postgres y Redpanda, espera a que estén sanos (healthchecks) y aplica
## sql/*.sql de forma idempotente (esquemas, tablas, roles, grants, vistas).
## Al terminar, 'make run' puede correr sin pasos manuales.
up:
	@test -f .env || $(PYTHON3) scripts/bootstrap_env.py
	@$(COMPOSE) up -d --wait --wait-timeout 180
	@$(PY) scripts/bootstrap_db.py
	@echo ">> infraestructura arriba y sana"

## Pipeline batch completo. Idempotente. Produce:
##   out/consumo_mensual.csv  out/dq_report.json  out/metrics.json  logs/pipeline.log
run:
	@mkdir -p out logs
	@$(PY) -m bia.pipeline

## Consumidor de streaming. Termina solo (< 300 s). Produce out/alertas.json
stream:
	@echo "TODO: hito 3.1 (streaming) pendiente"; exit 1

## Suite de pruebas. Exit 0 = todo pasó.
test:
	@$(PY) -m pytest -q tests

## Apaga la infraestructura (conserva volúmenes).
down:
	@$(COMPOSE) down --remove-orphans

## Borra artefactos generados: out/, logs/, volúmenes. Conserva .env (llaves).
clean:
	@$(COMPOSE) down -v --remove-orphans 2>/dev/null || true
	@rm -rf out logs .pytest_cache
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo ">> limpio"

## YA ESTA RESUELTO. Comprueba que entrega.yaml describe lo que levantaste.
verificar:
	@$(PY) tools/verificar_entrega.py

## Audita dependencias instaladas (C7) y guarda la evidencia.
audit:
	@mkdir -p docs/evidencias
	@$(PY) -m pip_audit --desc off 2>&1 | tee docs/evidencias/pip_audit_actual.txt

help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## //' | head -30
	@echo; echo "targets: setup up run stream test down clean verificar audit"
