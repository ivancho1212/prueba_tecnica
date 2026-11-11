# infra/ — cómo correría esto en AWS (no se despliega)

Terraform de referencia para la Parte F. No se aplica: no hay cuenta ni
estado remoto. Se evalúa leyendo el código y con:

```bash
cd infra
terraform init -backend=false
terraform validate        # Success! The configuration is valid.
```

Validado con Terraform 1.17.2 y el proveedor `hashicorp/aws` 6.x.

| Archivo | Qué describe | Equivale en local a |
|---|---|---|
| `s3.tf` | Buckets `crudo`, `curado` y `auditoria`: versionado, SSE-KMS con la llave del proyecto, sin acceso público ni ACLs, solo TLS, rechazo de objetos cifrados con otra llave. `auditoria` con Object Lock en modo COMPLIANCE | `data/`, `out/` y `audit.bitacora` |
| `kms.tf` | Llave `datos` (SSE de los buckets) y llave `pii` (envelope encryption de las columnas Restringido-PII), ambas con rotación anual y política explícita | `BIA_ENC_KEY` en `.env` |
| `iam.tf` | Roles `ingesta`, `analista` y `auditor` con mínimo privilegio y denegaciones explícitas | `rol_ingesta`, `rol_analista`, `rol_auditor` en `sql/90_grants.sql` |
| `outputs.tf` | Nombres de buckets y ARN de llaves y roles | — |

Decisiones que cierran riesgos del `THREAT_MODEL.md`:

- **T3, llave junto a la base.** La llave `pii` vive en KMS. El rol de
  ingesta solo puede `GenerateDataKey`/`Encrypt`; la política de la llave
  **niega** `Decrypt` a los tres roles de operación. Es el mismo contrato que
  hoy impone el grant por columna: la ingesta escribe PII que no puede leer.
- **T7, bitácora alterable por el superusuario.** La bitácora se exporta al
  bucket `auditoria` con Object Lock COMPLIANCE: durante la retención nadie,
  ni la cuenta raíz, puede borrar ni sobrescribir versiones. Ningún rol tiene
  `DeleteObject` ni `BypassGovernanceRetention` ahí.
- **C4 en la nube.** `analista` solo lee `curado/analytics/*` y solo puede
  descifrar con la llave `datos` a través de S3 sobre ese prefijo; `auditor`
  solo lee `auditoria`. Ambos se asumen con MFA y tienen escritura denegada.

Fuera de alcance, declarado: red (VPC, endpoints privados), el cómputo que
ejecuta el pipeline (se asume una tarea ECS que asume `ingesta`), RDS para
PostgreSQL, MSK/Redpanda Cloud para el streaming, CloudTrail y el mecanismo
que exporta la bitácora a S3.
