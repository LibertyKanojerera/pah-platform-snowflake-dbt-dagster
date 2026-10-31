# GitHub Actions authenticates to AWS by exchanging a short-lived OIDC token
# for temporary credentials. No access key is ever stored in GitHub secrets.
#
# The trust policy is the whole security boundary here: without the `sub`
# condition, ANY GitHub repository in the world could assume this role.

variable "github_repo" {
  description = "owner/repo that may assume the CI role."
  type        = string
  default     = "LibertyKanojerera/pah-platform-snowflake-dbt-dagster"
}

resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = []
}

data "aws_iam_policy_document" "github_actions_assume_role" {
  statement {
    sid     = "GitHubActionsOIDC"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to two specific contexts rather than `repo:owner/repo:*`, which
    # would let any branch in the repo assume the role. A pushed feature
    # branch is untrusted until it is reviewed.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:${var.github_repo}:ref:refs/heads/main",
        "repo:${var.github_repo}:pull_request",
      ]
    }
  }
}

# Deliberately not sharing a policy document with the Snowflake role, even
# though the two are currently identical. They will diverge: CI may need to
# write fixtures for integration tests; Snowflake only ever reads.
data "aws_iam_policy_document" "github_actions_landing_read" {
  statement {
    sid       = "ReadLandingObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.landing.arn}/${local.landing_prefix}*"]
  }

  statement {
    sid       = "ListLandingPrefix"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.landing.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${local.landing_prefix}*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${var.project}-github-actions"
  description        = "Assumed by GitHub Actions via OIDC. No stored credentials."
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume_role.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "github_actions_landing_read" {
  name   = "landing-read"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions_landing_read.json
}
