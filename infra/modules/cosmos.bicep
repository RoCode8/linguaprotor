// Azure Cosmos DB (NoSQL, serverless) holding the append-only audit trail.
//
// Key auth is disabled. The application identity gets a *custom* data-plane
// role scoped to the audit container that can create and read items but cannot
// replace, upsert or delete them, so the audit trail is append-only by policy,
// not just by convention.

@description('Account name (globally unique).')
param accountName string

@description('Region.')
param location string

@description('Resource tags.')
param tags object

@description('Database name.')
param databaseName string

@description('Audit container name.')
param auditContainerName string

@description('Principal IDs granted the append-only audit role (application identities).')
param auditAppenderPrincipalIds string[] = []

@description('Principal IDs granted full data access (developers / operators). Leave empty in production.')
param dataContributorPrincipalIds string[] = []

@description('Log Analytics workspace for data-plane logs.')
param logAnalyticsWorkspaceId string

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: accountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    capabilities: [
      { name: 'EnableServerless' }
    ]
    disableLocalAuth: true
    disableKeyBasedMetadataWriteAccess: true
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Enabled'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// AuditLogger writes { ..., "partitionKey": <assessment id> } and reads a
// resource's trail with a single-partition query.
resource auditContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: auditContainerName
  properties: {
    resource: {
      id: auditContainerName
      partitionKey: {
        paths: ['/partitionKey']
        kind: 'Hash'
        version: 2
      }
    }
  }
}

var auditScope = '${account.id}/dbs/${databaseName}/colls/${auditContainerName}'

resource auditAppenderRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' = {
  parent: account
  name: guid(account.id, 'audit-appender')
  properties: {
    roleName: 'LinguaProctor Audit Appender'
    type: 'CustomRole'
    assignableScopes: [auditScope]
    permissions: [
      {
        dataActions: [
          'Microsoft.DocumentDB/databaseAccounts/readMetadata'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/create'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/items/read'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/executeQuery'
          'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers/readChangeFeed'
        ]
      }
    ]
  }
  dependsOn: [auditContainer]
}

resource appenderGrants 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [
  for principalId in auditAppenderPrincipalIds: {
    parent: account
    name: guid(account.id, principalId, auditAppenderRole.id)
    properties: {
      principalId: principalId
      roleDefinitionId: auditAppenderRole.id
      scope: auditScope
    }
  }
]

// Built-in "Cosmos DB Built-in Data Contributor".
resource contributorGrants 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [
  for principalId in dataContributorPrincipalIds: {
    parent: account
    name: guid(account.id, principalId, 'data-contributor')
    properties: {
      principalId: principalId
      roleDefinitionId: '${account.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
      scope: account.id
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
    logAnalyticsDestinationType: 'Dedicated'
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'Requests', enabled: true }]
  }
}

output name string = account.name
output endpoint string = account.properties.documentEndpoint
output databaseName string = database.name
output auditContainerName string = auditContainer.name
