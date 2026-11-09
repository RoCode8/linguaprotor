// Azure Container Registry. Admin user disabled: pulls use managed identity (AcrPull).

import { roleAssignmentType } from 'types.bicep'

@description('Registry name (alphanumeric, globally unique).')
param name string

@description('Region.')
param location string

@description('Resource tags.')
param tags object

@description('Log Analytics workspace for diagnostic logs.')
param logAnalyticsWorkspaceId string

@description('Role grants on the registry (e.g. AcrPull for workload identities).')
param roleAssignments roleAssignmentType[] = []

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: name
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

resource grants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for ra in roleAssignments: {
    scope: registry
    name: guid(registry.id, ra.principalId, ra.roleDefinitionId)
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
  scope: registry
  name: 'to-log-analytics'
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
