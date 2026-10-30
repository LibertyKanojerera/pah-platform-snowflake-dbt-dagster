output "tfstate_bucket" {
  description = "S3 bucket holding Terraform state."
  value       = aws_s3_bucket.tfstate.id
}

output "landing_bucket" {
  description = "S3 bucket holding raw landed objects."
  value       = aws_s3_bucket.landing.id
}

output "snowflake_storage_integration_role_arn" {
  description = "Pass this to CREATE STORAGE INTEGRATION in Snowflake."
  value       = aws_iam_role.snowflake_storage_integration.arn
}
