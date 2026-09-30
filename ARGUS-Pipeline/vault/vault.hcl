# HashiCorp Vault server for ARGUS. File storage, internal network only (no host port).
# The listener is plain HTTP because only containers on the compose network can reach it; if Vault is ever exposed
# beyond that network, enable tls_cert_file / tls_key_file here.
storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr      = "http://vault:8200"
ui            = false
disable_mlock = true
