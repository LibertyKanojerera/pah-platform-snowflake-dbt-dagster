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
