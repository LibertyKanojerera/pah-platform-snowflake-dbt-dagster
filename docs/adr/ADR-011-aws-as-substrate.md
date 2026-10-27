# ADR-011: AWS as substrate, not warehouse replacement

## Status

Accepted 2026-10-02. Amended 2026-10-25 (account shape, see Consequences).

## Context

The platform's differentiated asset is a migration narrative: the same PAH analytics platform
built twice, first on Azure Databricks with Delta Lake and Unity Catalog, then on Snowflake,
dbt and Dagster. The target roles name Snowflake, dbt, Airflow and Dagster explicitly.

Partway into the build I wanted practical AWS experience as well, since the Data Architect and
Data Engineer roles I am targeting ask for it, and nothing in the project exercised it.

The naive reading of "add AWS" is to swap the warehouse for Redshift or Athena. That reading is
wrong, and the reason it is wrong is the whole content of this decision.

## Decision

Add AWS as the **substrate beneath** the existing stack, not as a replacement for any part of it.

Concretely:

- **S3** as the landing zone, replacing a local `data/landing/` directory. Parquet objects under
  `landing/<source>/dt=YYYY-MM-DD/batch=<uuid>/`.
- **IAM** storage integration between S3 and Snowflake, including the circular trust-policy
  handshake that `DESC INTEGRATION` requires.
- **Secrets Manager** for the Snowflake RSA key pair, replacing a `.p8` file read from disk.
- **GitHub Actions OIDC** federation to an IAM role, so CI holds no long-lived AWS credentials.
- **Terraform** provisioning all of the above.

Snowflake, dbt Core, Dagster and the DuckDB parallel target are unchanged.

This is also how the stack is deployed in practice: Snowflake and dbt sitting on top of an S3
data lake with IAM doing the trust work between them. The Snowflake account was always going to
run on AWS us-east-1. This decision makes that fact load-bearing rather than incidental.

## Rejected alternatives

**Redshift or Athena as the warehouse.** Destroys the migration narrative, which is the project's
only genuinely scarce asset, and removes the tools named in the postings being targeted. Converts
a migration into a rebuild.

**MWAA (managed Airflow)** for the Airflow comparison in Phase 5. Starts around $350 per month,
which falsifies the cost bullet claiming total platform spend under $5 per month. The
Astronomer CLI running locally produces the same ADR for nothing.

**Glue or Athena as a third dbt target.** DuckDB already fills the free, portable, runs-anywhere
role. A third adapter triples dialect divergence work for no additional claim.

**Moving Streamlit off Community Cloud** to ECS or App Runner. Loses the permanent free public
URL that survives Snowflake trial expiry, which is the property that makes the dashboard
clickable in an interview months later.

**ECR and ECS Fargate for the extractors.** Deferred rather than rejected. It would convert
"understands AWS" into "deployed on AWS", but it partly duplicates what Dagster already does and
is the likeliest consumer of remaining buffer. Revisit only if the schedule allows.

## Consequences

**Schedule.** Roughly three additional sessions. Nearly all AWS work is independent of Snowflake,
so it runs before the trial clock starts and costs calendar time rather than trial days.

**Identity.** Sessions 5 and 6 exist to eliminate long-lived credentials (Secrets Manager, OIDC).
Starting from an IAM user with an access key on disk would contradict that three sessions later,
so human access uses IAM Identity Center with short-lived credentials from the outset.

**Account shape (amendment, 2026-10-25).** The first AWS account was created through AWS's newer
"Sign up for AWS (new)" flow, which issues a Builder ID instead of a root user and places the
account inside an AWS-managed Organization. That model has no root user, no root MFA, no
IAM-access-to-billing setting, and applies service control policies from a management account
with no visibility into them. IAM roles can be created for programmatic access only.

It was abandoned and the account recreated through "Sign up for AWS (advanced)" for three
reasons: invisible SCPs would make an OIDC provider or custom trust-policy failure undebuggable;
the new experience deliberately abstracts away the IAM work this decision exists to learn; and
the classic model is what every tutorial, certification and employer assumes. Cost of the switch
was the $120 in credits, against real spend of a few dollars per month.

**Migration write-up.** The comparison is now Azure to AWS as well as Databricks to Snowflake:
storage model (Delta on ADLS to Parquet on S3), governance (Unity Catalog to IAM plus Snowflake
roles), ingestion (Auto Loader to external stages). A stronger comparison than the original.

**Certification.** The resulting surface area maps closely onto the AWS Certified Data Engineer
Associate domains, if that credential is wanted later.
