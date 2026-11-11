output "buckets" {
  description = "Nombre de cada bucket."
  value       = { for k, b in aws_s3_bucket.this : k => b.bucket }
}

output "llaves_kms" {
  description = "ARN de las llaves."
  value       = { datos = aws_kms_key.datos.arn, pii = aws_kms_key.pii.arn }
}

output "roles" {
  description = "ARN de los roles equivalentes a rol_ingesta, rol_analista y rol_auditor."
  value = {
    ingesta  = aws_iam_role.ingesta.arn
    analista = aws_iam_role.analista.arn
    auditor  = aws_iam_role.auditor.arn
  }
}
