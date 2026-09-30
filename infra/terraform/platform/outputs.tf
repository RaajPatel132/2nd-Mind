output "elastic_ip" {
  description = "The address DNS points at."
  value       = module.host.elastic_ip
}

output "app_hostname" {
  description = "The app's host name."
  value       = local.app_hostname
}

output "dns_record" {
  description = "The one record to add at Cloudflare, as DNS only (grey cloud, no proxy)."
  value       = "A  ${local.app_hostname}  ->  ${module.host.elastic_ip}  (DNS only)"
}

output "instance_id" {
  description = "The host, for `make host-stop`, `make host-start` and the INSTANCE_ID repository variable."
  value       = module.host.instance_id
}

output "data_volume_id" {
  value = module.host.data_volume_id
}

output "log_group_name" {
  value = module.host.log_group_name
}

output "backup_bucket_name" {
  description = "The bucket the app root makes and the host writes dumps to."
  value       = local.backup_bucket_name
}
