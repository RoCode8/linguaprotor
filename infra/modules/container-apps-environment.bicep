// Container Apps managed environment (workload-profiles, Consumption) with
// console/system logs streamed to Log Analytics.

@description('Environment name.')
param name string

@description('Region.')
param location string

@description('Resource tags.')
param tags object

@description('Name of the Log Analytics workspace in this resource group.')
param logAnalyticsWorkspaceName string

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2025-02-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        // Read inside the deployment; never surfaced as an output.
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    zoneRedundant: false
  }
}

output name string = environment.name
output defaultDomain string = environment.properties.defaultDomain
