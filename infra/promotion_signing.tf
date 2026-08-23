# B6v2 round 11 (Codex serving review): the promotion trust boundary.
#
# The grade authority and the admission receipt were bundle-authored JSON
# verified only against deployment-author-controlled hashes. This
# asymmetric KMS key signs both documents at ADMISSION time; the RUNTIME
# verifies the signatures OFFLINE against the public key committed in the
# repository and baked into the loader image — a boundary the bundle
# author cannot rewrite (the image is built from reviewed source by CI,
# not by whoever assembles a bundle).
#
# kms:Sign is deliberately NOT granted to any pod role. Signing happens
# in the admission pipeline under owner-reviewed credentials.
resource "aws_kms_key" "promotion_signing" {
  description              = "medzen promotion-evidence signing (B6v2 round 11)"
  key_usage                = "SIGN_VERIFY"
  customer_master_key_spec = "ECC_NIST_P256"
  deletion_window_in_days  = 30
}

resource "aws_kms_alias" "promotion_signing" {
  name          = "alias/medzen-promotion-signing"
  target_key_id = aws_kms_key.promotion_signing.key_id
}

output "promotion_signing_key_arn" {
  value = aws_kms_key.promotion_signing.arn
}
