"""The one place pipeline code reads a secret from.

Secrets live in the Databricks secret scope `readmission` today. The Azure card points this at
Key Vault (SPEC.md, Open Questions 3) without changing any caller. Never print or land a value.
"""

import base64
from typing import Any

from databricks.sdk.errors import NotFound

SCOPE = "readmission"


def get_secret(scope: str, key: str, workspace: Any | None = None) -> str | None:
    """The secret's value, or None when the scope or key does not exist.

    `workspace` is a `databricks.sdk.WorkspaceClient`; by default one is built from the
    environment (the job's identity on Databricks, a CLI profile locally).
    """
    if workspace is None:
        from databricks.sdk import WorkspaceClient

        workspace = WorkspaceClient()
    try:
        response = workspace.secrets.get_secret(scope, key)
    except NotFound:
        return None
    return base64.b64decode(response.value).decode()
