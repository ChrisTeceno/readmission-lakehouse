#!/usr/bin/env bash
# Deploy the Key Vault and Data Factory (infra/main.bicep) and wire them to Databricks.
# Safe to rerun: every step checks before it creates. See docs/azure.md.
#
#   infra/deploy.sh             create or update everything
#   infra/deploy.sh --what-if   show what the deployment would change, create nothing
#
# Needs `az login` (a user who can create role assignments in the subscription) and the
# Databricks CLI profile in DATABRICKS_PROFILE. No secret value is printed or written to disk:
# each one goes from one command to the next through a pipe.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-readmission}"
LOCATION="${LOCATION:-eastus}"
DATABRICKS_PROFILE="${DATABRICKS_PROFILE:-readmission}"
SCOPE="readmission"
SP_NAME="${SP_NAME:-sp-readmission-databricks}"
JOB_NAME="readmission_pipeline"
INFRA_DIR="$(cd "$(dirname "$0")" && pwd)"

what_if=false
if [[ "${1:-}" == "--what-if" ]]; then
  what_if=true
fi

dbx() { databricks --profile "$DATABRICKS_PROFILE" "$@"; }
scope_has() { dbx secrets list-secrets "$SCOPE" -o json | jq -e --arg k "$1" 'any(.[]; .key == $k)' >/dev/null; }
# The value comes on stdin and is stored as sent, so values are piped with `jq -j` or printf,
# never with a trailing newline.
scope_put() { dbx secrets put-secret "$SCOPE" "$1"; }

az account show -o none 2>/dev/null || { echo "Not logged in to Azure: run 'az login' first." >&2; exit 1; }
echo "Subscription: $(az account show --query '[name, id]' -o tsv | paste -sd ' ' -)"

export DATABRICKS_HOST
DATABRICKS_HOST="$(databricks auth profiles -o json \
  | jq -r --arg p "$DATABRICKS_PROFILE" '.profiles[] | select(.name == $p) | .host')"
export DATABRICKS_JOB_ID
DATABRICKS_JOB_ID="$(dbx jobs list --name "$JOB_NAME" -o json | jq -r '.[0].job_id // empty')"
if [[ -z "$DATABRICKS_HOST" || -z "$DATABRICKS_JOB_ID" ]]; then
  echo "No $JOB_NAME job found through profile $DATABRICKS_PROFILE; run 'databricks bundle deploy -t dev' first." >&2
  exit 1
fi
echo "Databricks: $DATABRICKS_HOST, job $JOB_NAME = $DATABRICKS_JOB_ID"

export ADMIN_OBJECT_ID
ADMIN_OBJECT_ID="$(az ad signed-in-user show --query id -o tsv)"

# The service principal the Databricks job reads Key Vault as. Its tenant id, client id and
# client secret go straight into the secret scope, where readmission.secrets reads them.
app_id="$(az ad sp list --display-name "$SP_NAME" --query '[0].appId' -o tsv)"
if $what_if; then
  if ! az group exists -n "$RESOURCE_GROUP" | grep -q true; then
    echo "Resource group $RESOURCE_GROUP does not exist yet; what-if needs it. Nothing created." >&2
    exit 1
  fi
  export DATABRICKS_SP_OBJECT_ID
  DATABRICKS_SP_OBJECT_ID="$(az ad sp show --id "${app_id:-none}" --query id -o tsv 2>/dev/null \
    || echo 00000000-0000-0000-0000-000000000000)"
  az deployment group what-if -g "$RESOURCE_GROUP" -n readmission \
    --parameters "$INFRA_DIR/main.bicepparam"
  exit 0
fi

if [[ -z "$app_id" ]]; then
  echo "Creating service principal $SP_NAME (no role assignment; main.bicep grants Key Vault only)"
  sp_json="$(az ad sp create-for-rbac --name "$SP_NAME" -o json --only-show-errors)"
  app_id="$(jq -r .appId <<<"$sp_json")"
  jq -j .tenant <<<"$sp_json" | scope_put azure_tenant_id
  jq -j .appId <<<"$sp_json" | scope_put azure_client_id
  jq -j .password <<<"$sp_json" | scope_put azure_client_secret
  unset sp_json
elif ! scope_has azure_client_secret; then
  echo "Service principal $SP_NAME exists but scope $SCOPE has no secret for it; resetting it"
  sp_json="$(az ad sp credential reset --id "$app_id" -o json --only-show-errors)"
  jq -j .tenant <<<"$sp_json" | scope_put azure_tenant_id
  jq -j .appId <<<"$sp_json" | scope_put azure_client_id
  jq -j .password <<<"$sp_json" | scope_put azure_client_secret
  unset sp_json
fi
export DATABRICKS_SP_OBJECT_ID
DATABRICKS_SP_OBJECT_ID="$(az ad sp show --id "$app_id" --query id -o tsv)"

az group create -n "$RESOURCE_GROUP" -l "$LOCATION" \
  --tags project=readmission-lakehouse "client=Contoso Health (fictional)" -o none
az deployment group create -g "$RESOURCE_GROUP" -n readmission \
  --parameters "$INFRA_DIR/main.bicepparam" -o none

output() {
  az deployment group show -g "$RESOURCE_GROUP" -n readmission --query "properties.outputs.$1.value" -o tsv
}
vault="$(output keyVaultName)"
vault_url="$(output keyVaultUri)"
factory="$(output dataFactoryName)"
echo "Deployed Key Vault $vault and Data Factory $factory"

vault_has() { az keyvault secret show --vault-name "$vault" --name "$1" -o none 2>/dev/null; }
# A new role assignment can take a few minutes to reach Key Vault, so writes are retried.
vault_put() {
  local name="$1" value
  value="$(cat)"
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if printf '%s' "$value" | az keyvault secret set --vault-name "$vault" --name "$name" \
      --file /dev/stdin -o none 2>/dev/null; then
      echo "Key Vault: set $name"
      return 0
    fi
    echo "Key Vault: $name not writable yet (attempt $attempt), waiting for the role assignment"
    sleep 30
  done
  echo "Key Vault: could not set $name" >&2
  return 1
}

# The Census key moves from the secret scope, where the ACS ingest has read it so far.
if ! vault_has census-api-key; then
  if scope_has census_api_key; then
    dbx secrets get-secret "$SCOPE" census_api_key -o json | jq -j .value | base64 --decode \
      | vault_put census-api-key
  elif [[ -t 0 ]]; then
    read -rsp "Census API key: " value && echo
    printf '%s' "$value" | vault_put census-api-key
    unset value
  else
    echo "Key Vault: census-api-key not set; rerun interactively to enter it" >&2
  fi
fi

# A Databricks token the ADF pipeline calls the Jobs API with. It is not in the scope.
if ! vault_has databricks-token; then
  if [[ -t 0 ]]; then
    read -rsp "Databricks token for ADF (Enter to skip): " value && echo
    if [[ -n "$value" ]]; then
      printf '%s' "$value" | vault_put databricks-token
    fi
    unset value
  else
    echo "Key Vault: databricks-token not set; rerun interactively to enter it" >&2
  fi
fi

# Last, because this is what switches readmission.secrets from the scope to Key Vault.
printf '%s' "$vault_url" | scope_put key_vault_url
echo "Secret scope $SCOPE: key_vault_url = $vault_url"

cat <<EOF

Run the ADF pipeline once:
  az datafactory pipeline create-run -g $RESOURCE_GROUP --factory-name $factory --name run_readmission_pipeline
Tear everything down:
  infra/teardown.sh
EOF
