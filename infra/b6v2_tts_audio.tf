# B6v2 TTS audio delivery storage (dark until var.b6v2_enabled).
variable "b6v2_enabled" {
  description = "owner switch for the B6v2 real-provider audio storage"
  type        = bool
  default     = false
}

resource "aws_s3_bucket" "tts_audio" {
  count  = var.b6v2_enabled ? 1 : 0
  bucket = "medzen-tts-audio-nonprod"
}

resource "aws_s3_bucket_lifecycle_configuration" "tts_audio" {
  count  = var.b6v2_enabled ? 1 : 0
  bucket = aws_s3_bucket.tts_audio[0].id
  rule {
    id     = "expire-delivery-cache"
    status = "Enabled"
    filter {
      prefix = "tts-audio/"
    }
    expiration {
      days = 7
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tts_audio" {
  count                   = var.b6v2_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.tts_audio[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
