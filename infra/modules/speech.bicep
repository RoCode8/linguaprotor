// Azure AI Speech. Key auth disabled; the custom subdomain enables Entra ID auth
// (Speech SDK: SpeechConfig(endpoint=..., token_credential=...)).

import { roleAssignmentType } from 'types.bicep'

@description('Account name; also the custom subdomain.')
param name string

@description('Region.')
param location string

@description('Resource tags.')
param tags object

@description('Role grants on the account.')
param roleAssignments roleAssignmentType[] = []

@description('Log Analytics workspace for audit/request logs.')
param logAnalyticsWorkspaceId string

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'SpeechServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
  }
}

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
output region string = location
