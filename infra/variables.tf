variable "region" {
  description = "Región AWS."
  type        = string
  default     = "us-east-1"
}

variable "proyecto" {
  description = "Prefijo de nombres."
  type        = string
  default     = "bia-consumos"
}

variable "entorno" {
  description = "dev | prod."
  type        = string
  default     = "prod"
  validation {
    condition     = contains(["dev", "prod"], var.entorno)
    error_message = "entorno debe ser dev o prod."
  }
}

variable "retencion_auditoria_dias" {
  description = "Retención Object Lock (COMPLIANCE) de la bitácora exportada: nadie, ni la cuenta raíz, puede borrarla antes."
  type        = number
  default     = 365
}

variable "retencion_versiones_crudo_dias" {
  description = "Días que se conservan las versiones no actuales de los archivos crudos."
  type        = number
  default     = 90
}
