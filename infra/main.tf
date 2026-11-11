data "aws_caller_identity" "actual" {}
data "aws_partition" "actual" {}

locals {
  cuenta = data.aws_caller_identity.actual.account_id
  raiz   = "arn:${data.aws_partition.actual.partition}:iam::${local.cuenta}:root"
  nombre = "${var.proyecto}-${var.entorno}"

  # Equivalencias con el entorno local (sql/90_grants.sql, docker-compose.yml):
  #   crudo     ← data/ (archivos de terceros, Frontera 1 del THREAT_MODEL)
  #   curado    ← out/ y exportes de analytics.* (lo único que ve el analista)
  #   auditoria ← exporte de audit.bitacora con Object Lock (mitiga T7)
  buckets = {
    crudo     = { object_lock = false }
    curado    = { object_lock = false }
    auditoria = { object_lock = true }
  }
}
