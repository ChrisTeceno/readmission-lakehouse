// Key Vault and Data Factory for readmission-lakehouse (SPEC.md task 3.1), deployed into one
// resource group so `az group delete` removes everything. See docs/azure.md.
//
// Key Vault holds the Census API key (`census-api-key`, read by the pipeline through
// readmission.secrets) and a Databricks token (`databricks-token`, read by the ADF pipeline).
// Neither value is in this file: infra/deploy.sh puts them in after the deployment.
//
// API versions checked against the Microsoft template reference on 2026-10-21: Key Vault
// 2026-05-15 is the newest non-preview version, Data Factory has only 2018-06-01, role
// assignments 2022-04-01.

@description('Region for every resource. Defaults to the resource group\'s region.')
param location string = resourceGroup().location

@description('Databricks workspace URL the pipeline calls, e.g. https://dbc-1234abcd-5678.cloud.databricks.com')
param databricksHost string

@description('Job id of readmission_pipeline in that workspace (databricks jobs list --name readmission_pipeline).')
param databricksJobId int

@description('Object id of the service principal the Databricks job signs in to Key Vault as.')
param databricksPrincipalId string

@description('Object id of the person deploying, who needs to write the two secrets. Empty skips the assignment.')
param adminPrincipalId string = ''

param tags object = {
  project: 'readmission-lakehouse'
  client: 'Contoso Health (fictional)'
}

var suffix = uniqueString(resourceGroup().id)

// Built-in role ids, from the Azure built-in roles reference.
var keyVaultSecretsUser = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4633458b-17de-408a-b874-0445c86b69e6'
)
var keyVaultSecretsOfficer = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
)

resource vault 'Microsoft.KeyVault/vaults@2026-05-15' = {
  name: 'kv-readm-${take(suffix, 10)}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    // Access is by Azure RBAC role assignments below, not access policies.
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    // Purge protection stays off (it cannot be turned off once on), so teardown can purge the
    // soft-deleted vault and its name is free again at once.
    publicNetworkAccess: 'Enabled'
  }
}

resource factory 'Microsoft.DataFactory/factories@2018-06-01' = {
  name: 'adf-readmission-${suffix}'
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publicNetworkAccess: 'Enabled'
  }
}

resource factorySecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, factory.id, keyVaultSecretsUser)
  scope: vault
  properties: {
    roleDefinitionId: keyVaultSecretsUser
    principalId: factory.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource databricksSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, databricksPrincipalId, keyVaultSecretsUser)
  scope: vault
  properties: {
    roleDefinitionId: keyVaultSecretsUser
    principalId: databricksPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource adminSecretsOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(adminPrincipalId)) {
  name: guid(vault.id, adminPrincipalId, keyVaultSecretsOfficer)
  scope: vault
  properties: {
    roleDefinitionId: keyVaultSecretsOfficer
    principalId: adminPrincipalId
    principalType: 'User'
  }
}

// The Databricks workspace is Free Edition, not an Azure Databricks workspace, so ADF's
// Databricks linked service and activities do not apply. The pipeline calls the Jobs API with
// Web activities instead: read the token from Key Vault as the factory's managed identity,
// start readmission_pipeline, then poll the run until it ends and fail if it did not succeed.
// secureOutput and secureInput keep the token out of the ADF run history.
var tokenUrl = '${vault.properties.vaultUri}secrets/databricks-token?api-version=2025-07-01'
var bearer = '@{concat(\'Bearer \', activity(\'GetDatabricksToken\').output.value)}'
var runId = '@{activity(\'RunReadmissionPipeline\').output.run_id}'

resource pipeline 'Microsoft.DataFactory/factories/pipelines@2018-06-01' = {
  parent: factory
  name: 'run_readmission_pipeline'
  properties: {
    description: 'Starts the Databricks job readmission_pipeline and waits for it to finish.'
    variables: {
      resultState: {
        type: 'String'
        defaultValue: ''
      }
    }
    activities: [
      {
        name: 'GetDatabricksToken'
        type: 'WebActivity'
        policy: {
          timeout: '0.00:05:00'
          retry: 2
          retryIntervalInSeconds: 30
          secureOutput: true
        }
        typeProperties: {
          method: 'GET'
          url: tokenUrl
          authentication: {
            type: 'MSI'
            // https://vault.azure.net in the public cloud (keyvaultDns is '.vault.azure.net').
            resource: 'https://${skip(environment().suffixes.keyvaultDns, 1)}'
          }
        }
      }
      {
        name: 'RunReadmissionPipeline'
        type: 'WebActivity'
        dependsOn: [
          {
            activity: 'GetDatabricksToken'
            dependencyConditions: ['Succeeded']
          }
        ]
        policy: {
          timeout: '0.00:05:00'
          // Not retried: a retry after a lost response would start a second job run.
          retry: 0
          secureInput: true
        }
        typeProperties: {
          method: 'POST'
          url: '${databricksHost}/api/2.2/jobs/run-now'
          headers: {
            Authorization: bearer
          }
          body: {
            job_id: databricksJobId
          }
        }
      }
      {
        name: 'WaitForRun'
        type: 'Until'
        dependsOn: [
          {
            activity: 'RunReadmissionPipeline'
            dependencyConditions: ['Succeeded']
          }
        ]
        typeProperties: {
          expression: {
            type: 'Expression'
            value: '@not(empty(variables(\'resultState\')))'
          }
          // A clean run takes about 13 minutes (docs/data-sources.md).
          timeout: '0.01:00:00'
          activities: [
            {
              name: 'Wait60s'
              type: 'Wait'
              typeProperties: {
                waitTimeInSeconds: 60
              }
            }
            {
              name: 'GetRun'
              type: 'WebActivity'
              dependsOn: [
                {
                  activity: 'Wait60s'
                  dependencyConditions: ['Succeeded']
                }
              ]
              policy: {
                timeout: '0.00:05:00'
                retry: 2
                retryIntervalInSeconds: 30
                secureInput: true
              }
              typeProperties: {
                method: 'GET'
                url: '${databricksHost}/api/2.2/jobs/runs/get?run_id=${runId}'
                headers: {
                  Authorization: bearer
                }
              }
            }
            {
              // result_state appears only once the run has terminated.
              name: 'SetResultState'
              type: 'SetVariable'
              dependsOn: [
                {
                  activity: 'GetRun'
                  dependencyConditions: ['Succeeded']
                }
              ]
              typeProperties: {
                variableName: 'resultState'
                value: '@coalesce(activity(\'GetRun\').output.state?.result_state, \'\')'
              }
            }
          ]
        }
      }
      {
        name: 'CheckResult'
        type: 'IfCondition'
        dependsOn: [
          {
            activity: 'WaitForRun'
            dependencyConditions: ['Succeeded']
          }
        ]
        typeProperties: {
          expression: {
            type: 'Expression'
            value: '@equals(variables(\'resultState\'), \'SUCCESS\')'
          }
          ifFalseActivities: [
            {
              name: 'RunDidNotSucceed'
              type: 'Fail'
              typeProperties: {
                message: '@concat(\'readmission_pipeline run \', string(activity(\'RunReadmissionPipeline\').output.run_id), \' ended \', variables(\'resultState\'))'
                errorCode: 'DatabricksRunFailed'
              }
            }
          ]
        }
      }
    ]
  }
}

output keyVaultName string = vault.name
output keyVaultUri string = vault.properties.vaultUri
output dataFactoryName string = factory.name
output pipelineName string = pipeline.name
