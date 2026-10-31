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

output "secret_names" {
  description = "Secrets Manager containers. Values are populated out-of-band."
  value = {
    snowflake_keypair = aws_secretsmanager_secret.snowflake_keypair.name
    postgres_app      = aws_secretsmanager_secret.postgres_app.name
  }
}

output "github_actions_role_arn" {
  description = "Role GitHub Actions assumes via OIDC."
  value       = aws_iam_role.github_actions.arn
}
