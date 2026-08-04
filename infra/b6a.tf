# B6A is a temporary, non-serving platform proof. These resources do not
# publish a production registry value or grant access to approved ASR models.

resource "aws_ecr_repository" "b6a_nvidia_dra" {
  name                 = "medzen-nvidia-dra"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
}

# Deliberately no lifecycle policy: packet 2026-003A requires the
# evidence-bound NVIDIA DRA digest to remain retained throughout B6A.

resource "aws_iam_role" "b6a_asr" {
  name                 = "medzen-b6a-asr-role"
  max_session_duration = 3600
  assume_role_policy = file(
    "${path.module}/../platform/iam/b6a/medzen-b6a-asr-role.trust.template.json"
  )
}

resource "aws_iam_role_policy" "b6a_asr" {
  name = "medzen-b6a-asr-access"
  role = aws_iam_role.b6a_asr.id
  policy = file(
    "${path.module}/../platform/iam/b6a/medzen-b6a-asr-role.policy.template.json"
  )
}

resource "aws_eks_pod_identity_association" "b6a_asr" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "medzen"
  service_account = "asr-runtime-b6a"
  role_arn        = aws_iam_role.b6a_asr.arn
  depends_on      = [aws_eks_addon.core]
}
