# The Snowflake storage integration is a two-sided handshake and the two sides
# cannot be created in one pass:
#
#   1. This role exists first, with a placeholder trust policy.
#   2. Snowflake's CREATE STORAGE INTEGRATION names this role's ARN.
#   3. DESC INTEGRATION returns STORAGE_AWS_IAM_USER_ARN and
#      STORAGE_AWS_EXTERNAL_ID, which Snowflake generated in step 2.
#   4. Those two values go into terraform.tfvars and this is re-applied,
#      replacing the placeholder with the real trust relationship.
#
# Until step 4, snowflake_iam_user_arn and snowflake_external_id are null and
# the role trusts only this account. That is intentional, not unfinished.
# See docs/plan.md, plan S56.

locals {
  landing_prefix = "landing/"

  snowflake_integration_configured = (
    var.snowflake_iam_user_arn != null && var.snowflake_external_id != null
  )
}

data "aws_iam_policy_document" "snowflake_assume_role" {
  statement {
    sid     = "SnowflakeStorageIntegration"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type = "AWS"
      identifiers = [
        local.snowflake_integration_configured
        ? var.snowflake_iam_user_arn
        : "arn:aws:iam::${var.account_id}:root"
      ]
    }

    # Snowflake's external ID defeats the confused deputy problem: without it,
    # any other Snowflake account that learned this role ARN could ask
    # Snowflake's shared IAM user to assume it.
    dynamic "condition" {
      for_each = local.snowflake_integration_configured ? [1] : []

      content {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [var.snowflake_external_id]
      }
    }
  }
}

data "aws_iam_policy_document" "snowflake_landing_read" {
  statement {
    sid    = "ReadLandingObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
    ]

    resources = ["${aws_s3_bucket.landing.arn}/${local.landing_prefix}*"]
  }

  statement {
    sid    = "ListLandingPrefix"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]

    resources = [aws_s3_bucket.landing.arn]

    # ListBucket is a bucket-level action, so it cannot be scoped by resource
    # ARN. This condition is what confines the listing to landing/.
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.landing_prefix}*"]
    }
  }
}

resource "aws_iam_role" "snowflake_storage_integration" {
  name               = "${var.project}-snowflake-storage-integration"
  description        = "Assumed by Snowflake to read landed Parquet from S3."
  assume_role_policy = data.aws_iam_policy_document.snowflake_assume_role.json
}

resource "aws_iam_role_policy" "snowflake_landing_read" {
  name   = "landing-read"
  role   = aws_iam_role.snowflake_storage_integration.id
  policy = data.aws_iam_policy_document.snowflake_landing_read.json
}
