# Seguridad de la solución

> Plantilla. Este documento le explica al evaluador qué controles pusiste
> y cómo verificarlos.

## 1. Clasificación de datos
> Resumen de `data_classification.yaml` y cómo el pipeline lo consume.

## 2. Cifrado
| Qué | Dónde | Mecanismo | Gestión de la llave |
|---|---|---|---|

> ¿Cómo hacés joins sobre campos sensibles sin exponerlos?

## 3. Control de accesos (menor privilegio)
| Rol | Puede | NO puede | Dónde está el GRANT |
|---|---|---|---|

## 4. Enmascaramiento de PII
> Qué se enmascara, con qué técnica, y qué pasa con la PII en texto libre.

## 5. Gestión de secretos
> Cómo se inyectan. Qué encontraste en el scaffolding heredado y qué hiciste.

## 6. Trazabilidad y auditoría
> Esquema de la bitácora. Cómo garantizás que es append-only de verdad.

## 7. Dependencias
> Qué auditaste, con qué herramienta, qué encontraste, qué actualizaste.

## 8. Cómo verificar todo lo anterior
> Comandos concretos que el evaluador puede correr. Sé específico.
