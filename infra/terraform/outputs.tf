output "tfstate_bucket" {
  description = "S3 bucket holding Terraform state."
  value       = aws_s3_bucket.tfstate.id
}

output "landing_bucket" {
  description = "S3 bucket holding raw landed objects."
  value       = aws_s3_bucket.landing.id
}
