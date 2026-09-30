#!/bin/sh
# One-shot Vault bootstrap / unseal for ARGUS. Idempotent: safe to run on every `docker compose up`.
#
# First run   : initialise (1 key share), unseal, enable KV v2, create the read-only policy and the AppRole the agent
#               uses, seed secrets (from the legacy .env values if present, otherwise freshly generated), then revoke
#               the root token. The unseal key is printed ONCE - put it in .env as VAULT_UNSEAL_KEY.
# Later runs  : unseal with VAULT_UNSEAL_KEY (development may fall back to the key kept in the vault_init volume).
set -eu
export VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"

n=0
while :; do
  rc=0; vault status >/dev/null 2>&1 || rc=$?
  { [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; } && break
  n=$((n + 1)); [ "$n" -gt 90 ] && { echo "[vault-init] Vault did not come up" >&2; exit 1; }
  sleep 1
done

status() { vault status -format=json | tr -d '\n ' ; }
rand()   { head -c 32 /dev/urandom | base64 | tr -d '/+=\n'; }

if ! status | grep -q '"initialized":true'; then
  echo "[vault-init] initialising Vault"
  INIT=$(vault operator init -key-shares=1 -key-threshold=1 -format=json | tr -d '\n ')
  UNSEAL=$(echo "$INIT" | sed 's/.*"unseal_keys_b64":\["\([^"]*\)".*/\1/')
  ROOT=$(echo "$INIT" | sed 's/.*"root_token":"\([^"]*\)".*/\1/')
  case "${ARGUS_ENV:-development}" in
    production) ;;
    *) mkdir -p /vault/init && echo "$UNSEAL" > /vault/init/unseal_key && chmod 600 /vault/init/unseal_key ;;
  esac
  echo "=============================================================================="
  echo " VAULT INITIALISED. Store this unseal key in .env now - it is shown only once:"
  echo "   VAULT_UNSEAL_KEY=$UNSEAL"
  echo "=============================================================================="
  vault operator unseal "$UNSEAL" >/dev/null
  export VAULT_TOKEN="$ROOT"

  vault secrets enable -path=secret kv-v2 >/dev/null
  printf 'path "secret/data/argus/*" {\n  capabilities = ["read"]\n}\n' | vault policy write argus-app - >/dev/null
  vault auth enable approle >/dev/null
  vault write auth/approle/role/argus-agent token_policies=argus-app token_ttl=1h token_max_ttl=24h secret_id_ttl=0 >/dev/null

  # Legacy values from .env are migrated as they are (existing volumes keep working); anything unset is generated.
  vault kv put secret/argus/app \
    jwt_secret="${JWT_SECRET:-$(rand)$(rand)}" \
    mfa_encryption_key="${MFA_ENCRYPTION_KEY:-$(head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '\n')}" \
    victim_hash_pepper="${VICTIM_HASH_PEPPER:-$(rand)}" \
    groq_api_key="${GROQ_API_KEY:-}" \
    gemini_api_key="${GEMINI_API_KEY:-}" >/dev/null
  vault kv put secret/argus/db \
    postgres_password="${POSTGRES_PASSWORD:-$(rand)}" \
    neo4j_password="${NEO4J_PASSWORD:-$(rand)}" \
    redis_password="${REDIS_PASSWORD:-$(rand)}" \
    elasticsearch_password="${ELASTIC_PASSWORD:-$(rand)}" \
    minio_access_key="${MINIO_ACCESS_KEY:-argus$(rand | head -c 8)}" \
    minio_secret_key="${MINIO_SECRET_KEY:-$(rand)}" >/dev/null

  mkdir -p /vault/auth
  vault read -field=role_id auth/approle/role/argus-agent/role-id > /vault/auth/role_id
  vault write -f -field=secret_id auth/approle/role/argus-agent/secret-id > /vault/auth/secret_id
  chmod 644 /vault/auth/role_id /vault/auth/secret_id
  vault token revoke -self >/dev/null
  echo "[vault-init] secrets seeded, AppRole issued, root token revoked"
  exit 0
fi

if status | grep -q '"sealed":true'; then
  KEY="${VAULT_UNSEAL_KEY:-}"
  if [ -z "$KEY" ] && [ "${ARGUS_ENV:-development}" != "production" ] && [ -s /vault/init/unseal_key ]; then
    KEY=$(cat /vault/init/unseal_key)   # development convenience only
  fi
  if [ -z "$KEY" ]; then
    echo "[vault-init] Vault is sealed and VAULT_UNSEAL_KEY is not set in .env" >&2
    exit 1
  fi
  vault operator unseal "$KEY" >/dev/null
  echo "[vault-init] unsealed"
else
  echo "[vault-init] already unsealed"
fi
