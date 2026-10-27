resource "aws_s3_bucket" "landing" {
  bucket = "${var.project}-landing-${var.account_id}"
}

resource "aws_s3_bucket_versioning" "landing" {
  bucket = aws_s3_bucket.landing.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "landing" {
  bucket                  = aws_s3_bucket.landing.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "landing" {
  bucket = aws_s3_bucket.landing.id

  # Orphaned multipart parts are billed but invisible in the console.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-landing-objects"
    status = "Enabled"

    filter {
      prefix = "landing/"
    }

    expiration {
      days = 90
    }

    # Without this, versioning retains every overwritten object forever.
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  depends_on = [aws_s3_bucket_versioning.landing]
}
