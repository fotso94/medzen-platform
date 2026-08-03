resource "aws_cloudwatch_log_group" "svc" {
  for_each          = local.pod_roles
  name              = "/medzen/speech/${each.key}"
  retention_in_days = 30
  kms_key_id        = aws_kms_key.audit.arn
}

resource "aws_cloudwatch_log_group" "audit" {
  name              = "/medzen/speech/audit"
  retention_in_days = 365
  kms_key_id        = aws_kms_key.audit.arn
}
