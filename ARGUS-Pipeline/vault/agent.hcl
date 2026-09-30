# Vault agent sidecar: authenticates with AppRole and renders every secret to its own file under /vault/secrets
# (a tmpfs volume, so secrets never touch disk). ARGUS containers read those files (config.py -> _secret()).
pid_file = "/tmp/vault-agent.pid"

vault {
  address = "http://vault:8200"
}

auto_auth {
  method "approle" {
    config = {
      role_id_file_path                   = "/vault/auth/role_id"
      secret_id_file_path                 = "/vault/auth/secret_id"
      remove_secret_id_file_after_reading = false
    }
  }
}

template_config {
  static_secret_render_interval = "1m"
}

template {
  destination = "/vault/secrets/jwt_secret"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/app\" }}{{ .Data.data.jwt_secret }}{{ end }}"
}
template {
  destination = "/vault/secrets/mfa_encryption_key"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/app\" }}{{ .Data.data.mfa_encryption_key }}{{ end }}"
}
template {
  destination = "/vault/secrets/victim_hash_pepper"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/app\" }}{{ .Data.data.victim_hash_pepper }}{{ end }}"
}
template {
  destination = "/vault/secrets/groq_api_key"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/app\" }}{{ .Data.data.groq_api_key }}{{ end }}"
}
template {
  destination = "/vault/secrets/gemini_api_key"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/app\" }}{{ .Data.data.gemini_api_key }}{{ end }}"
}
template {
  destination = "/vault/secrets/postgres_password"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/db\" }}{{ .Data.data.postgres_password }}{{ end }}"
}
template {
  destination = "/vault/secrets/neo4j_password"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/db\" }}{{ .Data.data.neo4j_password }}{{ end }}"
}
template {
  destination = "/vault/secrets/redis_password"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/db\" }}{{ .Data.data.redis_password }}{{ end }}"
}
template {
  destination = "/vault/secrets/elasticsearch_password"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/db\" }}{{ .Data.data.elasticsearch_password }}{{ end }}"
}
template {
  destination = "/vault/secrets/minio_access_key"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/db\" }}{{ .Data.data.minio_access_key }}{{ end }}"
}
template {
  destination = "/vault/secrets/minio_secret_key"
  perms       = "0644"
  contents    = "{{ with secret \"secret/data/argus/db\" }}{{ .Data.data.minio_secret_key }}{{ end }}"
}
