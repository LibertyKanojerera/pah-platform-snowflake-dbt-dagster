terraform {
  backend "s3" {
    bucket       = "pah-platform-tfstate-036999274035"
    key          = "platform/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
