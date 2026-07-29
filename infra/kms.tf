# Three keys so blast radius is separated: corpus, generated audio, audit logs.
resource "aws_kms_key" "data" {
  description             = "medzen-speech: corpus, manifests, model artifacts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "data" {
  name          = "alias/medzen-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_kms_key" "audio" {
  description             = "medzen-speech: generated + user audio (biometric)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "audio" {
  name          = "alias/medzen-audio"
  target_key_id = aws_kms_key.audio.key_id
}

resource "aws_kms_key" "audit" {
  description             = "medzen-speech: full-turn audit stream (PHI)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "audit" {
  name          = "alias/medzen-audit"
  target_key_id = aws_kms_key.audit.key_id
}
