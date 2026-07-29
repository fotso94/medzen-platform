# Containers only. Values are injected out-of-band so they never enter state.
resource "aws_secretsmanager_secret" "fish" {
  name       = "medzen/speech/fish-api-key"
  kms_key_id = aws_kms_key.data.arn
}

resource "aws_secretsmanager_secret" "client_keys" {
  name       = "medzen/speech/client-api-keys"
  kms_key_id = aws_kms_key.data.arn
}
