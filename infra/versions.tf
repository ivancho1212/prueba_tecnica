# Infraestructura de referencia en AWS. NO se despliega (enunciado, Parte F):
# se evalúa leyendo el código y con `terraform validate`.
terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      proyecto      = var.proyecto
      entorno       = var.entorno
      clasificacion = "ver data_classification.yaml"
      gestionado    = "terraform"
    }
  }
}
