output "cluster_name" { value = aws_eks_cluster.this.name }
output "cluster_endpoint" { value = aws_eks_cluster.this.endpoint }
output "ecr_registry" { value = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com" }
output "data_bucket" { value = aws_s3_bucket.data.id }
output "audio_bucket" { value = aws_s3_bucket.audio.id }
output "kubeconfig_cmd" {
  value = "aws eks update-kubeconfig --name ${aws_eks_cluster.this.name} --region ${var.region} --profile ${var.profile}"
}
output "gpu_note" {
  value = "GPU node group created at desired_size=${var.gpu_desired_size}. Raise after quota L-DB2E81BA is granted."
}
