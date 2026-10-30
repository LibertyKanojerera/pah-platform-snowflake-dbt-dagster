variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Project slug, used in resource names and tags."
  type        = string
  default     = "pah-platform"
}

variable "account_id" {
  description = "AWS account ID, used to make bucket names globally unique."
  type        = string
}

variable "snowflake_iam_user_arn" {
  description = "STORAGE_AWS_IAM_USER_ARN from DESC INTEGRATION. Null until plan S56."
  type        = string
  default     = null
}

variable "snowflake_external_id" {
  description = "STORAGE_AWS_EXTERNAL_ID from DESC INTEGRATION. Null until plan S56."
  type        = string
  default     = null
}
