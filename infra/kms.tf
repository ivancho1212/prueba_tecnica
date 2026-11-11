# Dos llaves con propósitos separados:
#  - datos: cifrado en reposo de los buckets (SSE-KMS).
#  - pii:   envelope encryption de las columnas Restringido-PII. Reemplaza a
#           BIA_ENC_KEY en .env (mitiga T3: la llave deja de vivir en el host).
#           La ingesta puede generar llaves de datos y cifrar, NO descifrar:
#           el mismo contrato que hoy impone el grant por columna a rol_ingesta.

data "aws_iam_policy_document" "kms_datos" {
  statement {
    sid       = "AdministracionPorLaCuenta"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [local.raiz]
    }
  }

  statement {
    sid       = "IngestaCifraYDescifraViaS3"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.ingesta.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "AnalistaDescifraSoloCurado"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.analista.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:s3:arn"
      values   = ["${aws_s3_bucket.this["curado"].arn}/analytics/*"]
    }
  }

  statement {
    sid       = "AuditorDescifraSoloAuditoria"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.auditor.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:s3:arn"
      values   = ["${aws_s3_bucket.this["auditoria"].arn}/*"]
    }
  }
}

resource "aws_kms_key" "datos" {
  description             = "${local.nombre}: SSE-KMS de los buckets"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms_datos.json
}

resource "aws_kms_alias" "datos" {
  name          = "alias/${local.nombre}-datos"
  target_key_id = aws_kms_key.datos.key_id
}

data "aws_iam_policy_document" "kms_pii" {
  statement {
    sid       = "AdministracionPorLaCuenta"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [local.raiz]
    }
  }

  statement {
    sid       = "IngestaSoloCifra"
    actions   = ["kms:GenerateDataKey", "kms:Encrypt", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.ingesta.arn]
    }
  }

  statement {
    sid       = "NadieDeLosRolesDeOperacionDescifra"
    effect    = "Deny"
    actions   = ["kms:Decrypt", "kms:ReEncrypt*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.ingesta.arn, aws_iam_role.analista.arn, aws_iam_role.auditor.arn]
    }
  }
}

resource "aws_kms_key" "pii" {
  description             = "${local.nombre}: envelope encryption de columnas Restringido-PII"
  enable_key_rotation     = true
  rotation_period_in_days = 365
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.kms_pii.json
}

resource "aws_kms_alias" "pii" {
  name          = "alias/${local.nombre}-pii"
  target_key_id = aws_kms_key.pii.key_id
}
