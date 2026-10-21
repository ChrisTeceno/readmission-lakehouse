import base64
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError
from databricks.sdk.errors import NotFound

from readmission.secrets import (
    CLIENT_ID,
    CLIENT_SECRET,
    KEY_VAULT_URL,
    TENANT_ID,
    get_secret,
    key_vault_client,
    vault_secret_name,
)

VAULT_URL = "https://kv-readm-test.vault.azure.net/"
SP = {TENANT_ID: "tenant", CLIENT_ID: "client", CLIENT_SECRET: "sp-secret"}


def workspace(values):
    """A fake WorkspaceClient whose `readmission` scope holds `values`."""

    class Secrets:
        def get_secret(self, scope, key):
            if scope != "readmission" or key not in values:
                raise NotFound("no such secret")
            return SimpleNamespace(value=base64.b64encode(values[key].encode()).decode())

    return SimpleNamespace(secrets=Secrets())


class FakeVault:
    """A fake azure.keyvault.secrets.SecretClient that records the names it was asked for."""

    def __init__(self, values, error=None):
        self.values = values
        self.error = error
        self.requested = []

    def get_secret(self, name):
        self.requested.append(name)
        if self.error is not None:
            raise self.error
        if name not in self.values:
            raise ResourceNotFoundError("SecretNotFound")
        return SimpleNamespace(value=self.values[name])


def test_vault_secret_name_swaps_underscores_for_dashes():
    assert vault_secret_name("census_api_key") == "census-api-key"
    assert vault_secret_name("databricks-token") == "databricks-token"


def test_scope_without_a_key_vault_url_reads_the_scope_and_never_the_vault():
    vault = FakeVault({"census-api-key": "from-vault"})
    ws = workspace({"census_api_key": "from-scope"})

    assert get_secret("readmission", "census_api_key", ws, vault) == "from-scope"
    assert get_secret("readmission", "other", ws, vault) is None
    assert vault.requested == []


def test_scope_with_a_key_vault_url_reads_the_vault_first(capsys):
    vault = FakeVault({"census-api-key": "from-vault"})
    ws = workspace({KEY_VAULT_URL: VAULT_URL, "census_api_key": "from-scope", **SP})

    assert get_secret("readmission", "census_api_key", ws, vault) == "from-vault"
    assert vault.requested == ["census-api-key"]
    out = capsys.readouterr().out
    assert VAULT_URL in out and "from-vault" not in out


def test_a_secret_missing_from_the_vault_falls_back_to_the_scope():
    vault = FakeVault({})
    ws = workspace({KEY_VAULT_URL: VAULT_URL, "census_api_key": "from-scope", **SP})

    assert get_secret("readmission", "census_api_key", ws, vault) == "from-scope"
    assert get_secret("readmission", "other", ws, vault) is None


def test_a_vault_that_refuses_the_service_principal_raises_instead_of_falling_back():
    # A wrong client secret must surface, not quietly turn into a scope or keyless read.
    vault = FakeVault({}, error=ClientAuthenticationError("invalid_client"))
    ws = workspace({KEY_VAULT_URL: VAULT_URL, "census_api_key": "from-scope", **SP})

    with pytest.raises(ClientAuthenticationError):
        get_secret("readmission", "census_api_key", ws, vault)


def test_key_vault_client_signs_in_with_the_scope_service_principal():
    vault = key_vault_client(workspace({KEY_VAULT_URL: VAULT_URL, **SP}), "readmission", VAULT_URL)

    # Building the client signs nothing in yet, so this makes no network call.
    assert vault.vault_url == VAULT_URL.rstrip("/")


def test_key_vault_client_names_missing_credentials_without_values():
    ws = workspace({KEY_VAULT_URL: VAULT_URL, TENANT_ID: "tenant", CLIENT_SECRET: "sp-secret"})

    with pytest.raises(RuntimeError) as error:
        key_vault_client(ws, "readmission", VAULT_URL)

    assert CLIENT_ID in str(error.value) and "sp-secret" not in str(error.value)
