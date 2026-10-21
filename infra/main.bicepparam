// Parameters for main.bicep. infra/deploy.sh looks each value up and exports it before the
// deployment, so nothing here is specific to one workspace or tenant, and nothing is secret.
using 'main.bicep'

param databricksHost = readEnvironmentVariable('DATABRICKS_HOST')
param databricksJobId = int(readEnvironmentVariable('DATABRICKS_JOB_ID'))
param databricksPrincipalId = readEnvironmentVariable('DATABRICKS_SP_OBJECT_ID')
param adminPrincipalId = readEnvironmentVariable('ADMIN_OBJECT_ID', '')
