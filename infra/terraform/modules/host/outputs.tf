output "instance_id" {
  value = aws_instance.host.id
}

output "elastic_ip" {
  description = "The address DNS points at: an A record for the app's host name, DNS only."
  value       = aws_eip.host.public_ip
}

output "data_volume_id" {
  value = aws_ebs_volume.data.id
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}

output "role_name" {
  value = aws_iam_role.host.name
}
