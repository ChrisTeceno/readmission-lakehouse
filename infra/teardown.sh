#!/usr/bin/env bash
# Remove everything infra/deploy.sh created: the resource group (Key Vault, Data Factory, role
# assignments), the soft-deleted vault, the service principal, and the scope keys that point
# readmission.secrets at Key Vault. The Census key stays in the secret scope, where the
# pipeline reads it again once key_vault_url is gone. Safe to rerun. See docs/azure.md.
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-readmission}"
DATABRICKS_PROFILE="${DATABRICKS_PROFILE:-readmission}"
SCOPE="readmission"
SP_NAME="${SP_NAME:-sp-readmission-databricks}"

dbx() { databricks --profile "$DATABRICKS_PROFILE" "$@"; }

az account show -o none 2>/dev/null || { echo "Not logged in to Azure: run 'az login' first." >&2; exit 1; }

# First, so no pipeline run tries a vault that is about to disappear.
for key in key_vault_url azure_tenant_id azure_client_id azure_client_secret; do
  if dbx secrets list-secrets "$SCOPE" -o json | jq -e --arg k "$key" 'any(.[]; .key == $k)' >/dev/null; then
    dbx secrets delete-secret "$SCOPE" "$key"
    echo "Secret scope $SCOPE: deleted $key"
  fi
done

vaults=""
if az group exists -n "$RESOURCE_GROUP" | grep -q true; then
  vaults="$(az keyvault list -g "$RESOURCE_GROUP" --query '[].name' -o tsv)"
  echo "Deleting resource group $RESOURCE_GROUP"
  az group delete -n "$RESOURCE_GROUP" --yes
fi

# Soft delete keeps a deleted vault for 7 days; purge protection is off, so purge it now.
for vault in $vaults $(az keyvault list-deleted --query "[?properties.tags.project=='readmission-lakehouse'].name" -o tsv); do
  if az keyvault show-deleted --name "$vault" -o none 2>/dev/null; then
    echo "Purging soft-deleted Key Vault $vault"
    az keyvault purge --name "$vault"
  fi
done

app_id="$(az ad sp list --display-name "$SP_NAME" --query '[0].appId' -o tsv)"
if [[ -n "$app_id" ]]; then
  echo "Deleting service principal $SP_NAME and its app registration"
  az ad app delete --id "$app_id"
fi

echo "Teardown done."
