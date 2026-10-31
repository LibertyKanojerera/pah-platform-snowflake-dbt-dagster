# Session Log

One entry per session. The `Next:` line is written before closing the laptop,
never at the start of the following session.

## S1 - 2026-10-25
Did: repo root confirmed correct; .env untracked; empty .gitignore found and
     populated (4th instance of the silent-gate pattern, ADR-010); AWS account
     created via "Sign up for AWS (new)" Builder ID flow rather than a classic
     account. Builder ID MFA set, project spend limit $20, $120 credits.
Blocked: no root user, so no root MFA and no IAM-access-to-billing toggle.
     AWS-managed SCPs on the project account are invisible to me.
Decided: sign up again via "Sign up for AWS (advanced)" for a classic root plus
     IAM account. Accept losing the $120; real spend is a few dollars a month.
Next: create the classic AWS account (alternate email or +aws alias), root MFA,
      enable IAM access to billing, $5 budget with 80% actual and 100% forecast
      alerts, region us-east-1. Then install Terraform and the AWS CLI.


## S2 - 2026-10-26
Did: classic AWS account created (036999274035, libertkanojerera@gmail.com),
     root MFA, IAM access to billing activated, $5 budget with 80% actual and
     100% forecast alerts, region us-east-1. IAM Identity Center enabled, user
     liberty with AdministratorAccess permission set, 4h sessions. AWS CLI v2
     and Terraform installed. aws sts get-caller-identity returns an assumed
     SSO role, no long-lived keys on disk.
Deferred: aws configure agent-toolkit (MCP server and skills), revisit around
     S6/S7 once the IAM and Terraform work is understood first-hand.
     Old Builder ID project at settings.aws.com still open, close it.
Next: terraform skeleton in infra/terraform/ with S3 remote state using native
      S3 locking (use_lockfile = true). Bootstrap with local state, then migrate.
      Write ADR-011 (AWS as substrate) and ADR-012 (DuckDB-first build order).

## S4 - 2026-10-27
Did: ADR-009 through ADR-012 written and committed. Terraform landing bucket
     applied: versioning, SSE, public access block, 90-day expiry plus 30-day
     noncurrent expiry, multipart abort at 7 days. Round-trip write/list/delete
     verified from the pah-admin role. Key layout documented in architecture.md.
Decided: plain resources rather than a bucket module. Two buckets differ enough
     (prevent_destroy vs lifecycle) that a module needs toggles for every
     difference. Extract if a third appears.
Next: plan S4, the IAM role Snowflake will assume. Scope the policy to the
      landing/ prefix only, leave the trust policy principal as a placeholder
      until DESC INTEGRATION in plan S56, and comment the circular dependency.

## S5 - 2026-10-30
Did: IAM role pah-platform-snowflake-storage-integration applied. Inline policy
     read-scoped to landing/ with an s3:prefix condition on ListBucket. Trust
     policy currently account root; snowflake_iam_user_arn and
     snowflake_external_id are null variables, so plan S56 is a tfvars change
     and an apply rather than a code edit. Verified trust policy has no
     sts:ExternalId condition yet, which is the expected pre-S56 state.
     ADR register rows 009, 010, 011 updated to Written.
Next: plan S5, Secrets Manager. One secret for the Snowflake key pair
      (populated at plan S55), one for Postgres. Refactor config loading so
      secrets resolve from Secrets Manager when AWS_PROFILE is set and from
      .env otherwise.

## S6 - 2026-10-30
Did: Secrets Manager containers for snowflake/keypair and postgres/app,
     7-day recovery window. Values deliberately out of Terraform, since
     aws_secretsmanager_secret_version writes plaintext into state.
     ingest/core/config.py resolves from Secrets Manager when AWS_PROFILE is
     set, .env otherwise; both branches verified to differ. First Python in
     the repo, so ruff/black/mypy finally ran and Passed rather than Skipped.
     docs/plan.md committed (it had never been saved to the repo; the README
     link was broken).
Fixed: use_secrets_manager used `is not None`, so AWS_PROFILE="" took the AWS
     branch. Now bool(). Empty-but-set env vars are common in CI.
Next: plan S6, GitHub Actions OIDC. Identity provider plus a role trusting
      this repo only, scoped to main and pull requests. Smoke workflow that
      assumes the role and lists the landing bucket. Closes gate G1.
