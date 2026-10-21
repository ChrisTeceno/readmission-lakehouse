Bicep for the Azure Key Vault and Data Factory pieces. Deployed for evidence capture, then torn down.

- `main.bicep`, `main.bicepparam`: Key Vault, Data Factory and its `run_readmission_pipeline` pipeline, role assignments.
- `deploy.sh`: service principal, deployment, secrets into Key Vault. `deploy.sh --what-if` previews.
- `teardown.sh`: removes all of it.

What, why and how: `docs/azure.md`.
