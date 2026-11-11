# Roles equivalentes a C4 (sql/90_grants.sql):
#   ingesta  ← rol_ingesta : lee crudo, escribe curado y auditoría; cifra PII sin poder descifrarla.
#   analista ← rol_analista: solo lee curado/analytics/; nada de crudo, auditoría ni llave PII.
#   auditor  ← rol_auditor : solo lee la bitácora exportada a auditoría.
# Nadie de los tres puede borrar en auditoría (además, Object Lock lo impide).

data "aws_iam_policy_document" "confianza_servicio" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"] # el pipeline corre como tarea, sin credenciales en .env
    }
  }
}

data "aws_iam_policy_document" "confianza_personas" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [local.raiz]
    }
    condition {
      test     = "Bool"
      variable = "aws:MultiFactorAuthPresent"
      values   = ["true"]
    }
  }
}

resource "aws_iam_role" "ingesta" {
  name               = "${local.nombre}-ingesta"
  assume_role_policy = data.aws_iam_policy_document.confianza_servicio.json
}

resource "aws_iam_role" "analista" {
  name                 = "${local.nombre}-analista"
  assume_role_policy   = data.aws_iam_policy_document.confianza_personas.json
  max_session_duration = 3600
}

resource "aws_iam_role" "auditor" {
  name                 = "${local.nombre}-auditor"
  assume_role_policy   = data.aws_iam_policy_document.confianza_personas.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "ingesta" {
  statement {
    sid       = "LeerCrudo"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket"]
    resources = [aws_s3_bucket.this["crudo"].arn, "${aws_s3_bucket.this["crudo"].arn}/*"]
  }
  statement {
    sid       = "EscribirCurado"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:ListBucket"]
    resources = [aws_s3_bucket.this["curado"].arn, "${aws_s3_bucket.this["curado"].arn}/*"]
  }
  statement {
    sid       = "AnexarAuditoria"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.this["auditoria"].arn}/*"]
  }
  statement {
    sid       = "NuncaBorrarAuditoria"
    effect    = "Deny"
    actions   = ["s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectRetention", "s3:BypassGovernanceRetention", "s3:PutBucketObjectLockConfiguration"]
    resources = [aws_s3_bucket.this["auditoria"].arn, "${aws_s3_bucket.this["auditoria"].arn}/*"]
  }
  statement {
    sid       = "LlaveDatos"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.datos.arn]
  }
  statement {
    sid       = "LlavePiiSoloCifrar"
    actions   = ["kms:GenerateDataKey", "kms:Encrypt"]
    resources = [aws_kms_key.pii.arn]
  }
}

data "aws_iam_policy_document" "analista" {
  statement {
    sid       = "LeerSoloAnalytics"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this["curado"].arn}/analytics/*"]
  }
  statement {
    sid       = "ListarSoloAnalytics"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.this["curado"].arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["analytics/*"]
    }
  }
  statement {
    sid       = "LlaveDatosSoloDescifrar"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.datos.arn]
  }
  statement {
    sid     = "NadaFueraDeAnalytics"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.this["crudo"].arn, "${aws_s3_bucket.this["crudo"].arn}/*",
      aws_s3_bucket.this["auditoria"].arn, "${aws_s3_bucket.this["auditoria"].arn}/*",
    ]
  }
  statement {
    sid       = "SinEscritura"
    effect    = "Deny"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
    resources = ["*"]
  }
  statement {
    sid       = "SinLlavePii"
    effect    = "Deny"
    actions   = ["kms:*"]
    resources = [aws_kms_key.pii.arn]
  }
}

data "aws_iam_policy_document" "auditor" {
  statement {
    sid       = "LeerBitacora"
    actions   = ["s3:GetObject", "s3:GetObjectVersion", "s3:ListBucket", "s3:GetObjectRetention"]
    resources = [aws_s3_bucket.this["auditoria"].arn, "${aws_s3_bucket.this["auditoria"].arn}/*"]
  }
  statement {
    sid       = "LlaveDatosSoloDescifrar"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.datos.arn]
  }
  statement {
    sid     = "NadaFueraDeAuditoria"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.this["crudo"].arn, "${aws_s3_bucket.this["crudo"].arn}/*",
      aws_s3_bucket.this["curado"].arn, "${aws_s3_bucket.this["curado"].arn}/*",
    ]
  }
  statement {
    sid       = "SinEscritura"
    effect    = "Deny"
    actions   = ["s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectRetention"]
    resources = ["*"]
  }
  statement {
    sid       = "SinLlavePii"
    effect    = "Deny"
    actions   = ["kms:*"]
    resources = [aws_kms_key.pii.arn]
  }
}

resource "aws_iam_role_policy" "ingesta" {
  name   = "minimo-privilegio"
  role   = aws_iam_role.ingesta.id
  policy = data.aws_iam_policy_document.ingesta.json
}

resource "aws_iam_role_policy" "analista" {
  name   = "minimo-privilegio"
  role   = aws_iam_role.analista.id
  policy = data.aws_iam_policy_document.analista.json
}

resource "aws_iam_role_policy" "auditor" {
  name   = "minimo-privilegio"
  role   = aws_iam_role.auditor.id
  policy = data.aws_iam_policy_document.auditor.json
}
