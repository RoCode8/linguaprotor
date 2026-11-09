// Azure OpenAI account with model deployments. Key auth is disabled:
// callers authenticate with Microsoft Entra ID (Cognitive Services OpenAI User).

import { modelDeploymentType, roleAssignmentType } from 'types.bicep'

@description('Account name; also the custom subdomain required for Entra ID auth.')
param name string

@description('Region (must have quota for every deployment).')
param location string

@description('Resource tags.')
param tags object

@description('Model deployments, created sequentially (the service rejects concurrent deployment writes).')
param deployments modelDeploymentType[]

@description('Role grants on the account.')
param roleAssignments roleAssignmentType[] = []

@description('Log Analytics workspace for audit/request logs.')
param logAnalyticsWorkspaceId string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
}

@batchSize(1)
resource modelDeployments 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [
  for d in deployments: {
    parent: account
    name: d.name
    sku: {
      name: d.sku
      capacity: d.capacity
    }
    properties: {
      model: {
        format: 'OpenAI'
        name: d.model
        version: d.version
      }
      // Never silently swap the grading model (that would invalidate calibration);
      // upgrade only when the pinned version is retired.
      versionUpgradeOption: 'OnceCurrentVersionExpired'
      raiPolicyName: 'Microsoft.DefaultV2'
    }
  }
]

resource grants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for ra in roleAssignments: {
    scope: account
    name: guid(account.id, ra.principalId, ra.roleDefinitionId)
    properties: {
      principalId: ra.principalId
      principalType: ra.principalType
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', ra.roleDefinitionId)
    }
  }
]

// categoryGroup ('allLogs') requires this API; it is the latest available (as used by Azure Verified Modules).
#disable-next-line use-recent-api-versions
resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: account
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output name string = account.name
output endpoint string = account.properties.endpoint
