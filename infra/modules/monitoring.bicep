// Log Analytics workspace (central log sink) and workspace-based Application Insights.

@description('Log Analytics workspace name.')
param logAnalyticsName string

@description('Application Insights component name.')
param applicationInsightsName string

@description('Region.')
param location string

@description('Resource tags.')
param tags object

@description('Log retention in days.')
@minValue(30)
@maxValue(730)
param retentionInDays int = 30

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    IngestionMode: 'LogAnalytics'
  }
}

output logAnalyticsWorkspaceId string = logAnalytics.id
output logAnalyticsWorkspaceName string = logAnalytics.name
output applicationInsightsName string = applicationInsights.name
@description('Ingestion connection string (an identifier, not a credential).')
output applicationInsightsConnectionString string = applicationInsights.properties.ConnectionString
