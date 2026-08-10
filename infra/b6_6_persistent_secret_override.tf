# Packet 2026-019 changes only lifecycle: the synthetic secret and both reader
# boundaries persist between windows. Historical b6_client_secret.tf remains
# byte-identical to its prior packet bindings. No window passes
# enable_b6_client_keys=false; prevent_destroy adds a second fail-closed guard.

resource "aws_secretsmanager_secret" "b6_client_keys" {
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_secretsmanager_secret_policy" "b6_client_keys" {
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role_policy" "b6_client_keys_kms" {
  lifecycle {
    prevent_destroy = true
  }
}
