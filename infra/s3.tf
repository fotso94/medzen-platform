# A3 data zones. Prefix separation is the lineage boundary.

resource "aws_s3_bucket" "data" { bucket = var.data_bucket }

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "expire-old-curated-versions"
    status = "Enabled"
    filter { prefix = "curated/" }
    noncurrent_version_expiration { noncurrent_days = 90 }
  }
  rule {
    id     = "expire-ungated-candidates"
    status = "Enabled"
    filter { prefix = "candidates/" }
    expiration { days = 60 }
  }
}

# ---- audio cache -----------------------------------------------------------
resource "aws_s3_bucket" "audio" { bucket = var.audio_bucket }

resource "aws_s3_bucket_server_side_encryption_configuration" "audio" {
  bucket = aws_s3_bucket.audio.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.audio.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "audio" {
  bucket                  = aws_s3_bucket.audio.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "audio" {
  bucket = aws_s3_bucket.audio.id
  rule {
    id     = "tts-cache-90d"
    status = "Enabled"
    filter { prefix = "tts/" }
    expiration { days = 90 }
  }
  # A3: user audio has a HARD 30-day expiry. It leaves only by being copied
  # into raw/ under an explicit consent id; the original still expires.
  rule {
    id     = "user-audio-30d"
    status = "Enabled"
    filter { prefix = "user-audio/" }
    expiration { days = 30 }
  }
}
