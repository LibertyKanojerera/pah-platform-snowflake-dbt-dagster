# Containers only. Values are written out-of-band with the AWS CLI, because
# aws_secretsmanager_secret_version stores secret_string as plaintext in
# Terraform state. See docs/adr/ADR-011-aws-as-substrate.md.
#
# Populate:
#   snowflake/keypair  -> plan S55, after the RSA key pair is generated
#   postgres/app       -> now, from infra/postgres/docker-compose.yml

resource "aws_secretsmanager_secret" "snowflake_keypair" {
  name        = "${var.project}/snowflake/keypair"
  description = "RSA private key and passphrase for the SVC_DAGSTER service user."

  # Default is 30 days, during which the name cannot be reused. On a project
  # that will be torn down and rebuilt, that is a 30-day lockout on a name.
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "postgres_app" {
  name        = "${var.project}/postgres/app"
  description = "Credentials for the local Postgres application source."

  recovery_window_in_days = 7
}
