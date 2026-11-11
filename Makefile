# ===========================================================================
# CONTRATO DE EJECUCION  --  NO RENOMBRES NI ELIMINES ESTOS TARGETS
#
# El evaluador corre tu entrega de forma automatica usando exactamente estos
# comandos, en este orden. Si un target no existe o falla, se pierden los
# puntos asociados sin revision manual.
#
# Podes agregar los targets que quieras. Podes cambiar COMO se implementa
# cada uno. No podes cambiar su NOMBRE ni su CONTRATO de salida.
# ===========================================================================

.PHONY: setup up run stream test down clean verificar

## Instala dependencias. Debe ser idempotente y no requerir interaccion.
setup:
	@echo "TODO: implementar"; exit 1

## Levanta la infraestructura local y espera a que este lista para recibir
## conexiones. Al terminar, 'make run' debe poder correr sin pasos manuales.
up:
	@echo "TODO: implementar"; exit 1

## Ejecuta el pipeline batch completo de punta a punta.
## DEBE ser idempotente: correrlo dos veces seguidas no puede duplicar datos
## ni cambiar los resultados.
## Al terminar deben existir:
##   out/consumo_mensual.csv
##   out/dq_report.json
##   out/metrics.json
##   logs/pipeline.log
run:
	@echo "TODO: implementar"; exit 1

## Procesa el stream de mediciones y termina solo (no puede quedarse colgado).
## Timeout del evaluador: 300 s.
## Al terminar debe existir: out/alertas.json
stream:
	@echo "TODO: implementar"; exit 1

## Corre tu suite de pruebas. Exit code 0 = todo paso.
test:
	@echo "TODO: implementar"; exit 1

## Apaga la infraestructura.
down:
	@echo "TODO: implementar"; exit 1

## Borra artefactos generados (out/, logs/, volumenes).
clean:
	@echo "TODO: implementar"; exit 1

## YA ESTA RESUELTO. Comprueba que tu entrega.yaml describe lo que levantaste:
## que los roles conectan y que los objetos que nombraste existen. No califica
## nada. Correlo con la infra arriba, antes de comprimir.
verificar:
	@python3 tools/verificar_entrega.py
