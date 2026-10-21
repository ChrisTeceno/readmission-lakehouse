"""The one place pipeline code reads a secret from.

Two backends sit behind `get_secret`, chosen by the Databricks secret scope itself:
- Azure Key Vault, when the scope holds `key_vault_url`. The vault is read with
  `azure-identity` as a service principal whose tenant id, client id and client secret are the
  scope's `azure_tenant_id`, `azure_client_id` and `azure_client_secret` (infra/deploy.sh puts
  them there, infra/teardown.sh removes them). A secret the vault does not hold falls back to
  the scope.
- The Databricks secret scope `readmission`, otherwise.

Callers never change between the two (SPEC.md, Open Questions 3). Never print or land a value.
"""

import base64
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from databricks.sdk.errors import NotFound

SCOPE = "readmission"

# Scope keys that switch get_secret to Key Vault and hold the service principal's credentials.
KEY_VAULT_URL = "key_vault_url"
TENANT_ID = "azure_tenant_id"
CLIENT_ID = "azure_client_id"
CLIENT_SECRET = "azure_client_secret"


def vault_secret_name(key: str) -> str:
    """Key Vault secret names allow only letters, digits and dashes, so `census_api_key` is
    stored as `census-api-key`."""
    return key.replace("_", "-")


def scope_secret(workspace: Any, scope: str, key: str) -> str | None:
    """The scope secret's value, or None when the scope or key does not exist."""
    try:
        response = workspace.secrets.get_secret(scope, key)
    except NotFound:
        return None
    return base64.b64decode(response.value).decode()


def key_vault_client(workspace: Any, scope: str, vault_url: str) -> Any:
    """An `azure.keyvault.secrets.SecretClient` signed in as the service principal held in
    the scope."""
    from azure.identity import ClientSecretCredential
    from azure.keyvault.secrets import SecretClient

    credentials = {
        k: scope_secret(workspace, scope, k) for k in (TENANT_ID, CLIENT_ID, CLIENT_SECRET)
    }
    missing = [k for k, v in credentials.items() if v is None]
    if missing:
        raise RuntimeError(
            f"secret scope '{scope}' names Key Vault {vault_url} but has no {', '.join(missing)}"
        )
    credential = ClientSecretCredential(
        credentials[TENANT_ID], credentials[CLIENT_ID], credentials[CLIENT_SECRET]
    )
    return SecretClient(vault_url=vault_url, credential=credential)


def get_secret(
    scope: str, key: str, workspace: Any | None = None, vault: Any | None = None
) -> str | None:
    """The secret's value, or None when neither Key Vault nor the scope holds it.

    `workspace` is a `databricks.sdk.WorkspaceClient`; by default one is built from the
    environment (the job's identity on Databricks, a CLI profile locally). `vault` is a
    `SecretClient` for the scope's Key Vault; by default one is built from the scope.
    """
    if workspace is None:
        from databricks.sdk import WorkspaceClient

        workspace = WorkspaceClient()
    vault_url = scope_secret(workspace, scope, KEY_VAULT_URL)
    if vault_url is not None:
        if vault is None:
            vault = key_vault_client(workspace, scope, vault_url)
        name = vault_secret_name(key)
        try:
            value = vault.get_secret(name).value
        except ResourceNotFoundError:
            print(f"secrets: {name} is not in Key Vault {vault_url}, reading scope '{scope}'")
        else:
            print(f"secrets: {key} read from Key Vault {vault_url}")
            return value
    return scope_secret(workspace, scope, key)
