// =============================================================================
// LinguaProctor — platform infrastructure (azd: `azd provision`)
//
// Provisions everything the application needs except the two container apps,
// which are deployed with their image by `azd deploy` (infra/app/*.bicep,
// revision-based strategy). See docs/infrastructure.md.
//
// Security posture: no keys or connection secrets anywhere. Every data-plane
// call uses Microsoft Entra ID via user-assigned managed identities.
// =============================================================================
targetScope = 'subscription'

metadata name = 'LinguaProctor platform'
metadata description = 'Azure OpenAI, AI Speech, Cosmos DB, Container Registry, Container Apps environment, monitoring and identities for LinguaProctor.'

import { modelDeploymentType, roleAssignmentType, roles } from 'modules/types.bicep'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@minLength(1)
@maxLength(64)
@description('azd environment name (e.g. "dev", "prod"); used in resource names and tags.')
param environmentName string

@minLength(1)
@description('Primary region for all resources except Azure OpenAI.')
param location string

@minLength(1)
@description('Region for Azure OpenAI. azd only offers regions with quota for the default deployments.')
@metadata({
  azd: {
    type: 'location'
    usageName: [
      'OpenAI.GlobalStandard.gpt-5.1,30'
      'OpenAI.GlobalStandard.text-embedding-3-small,30'
    ]
  }
})
param openAiLocation string

@description('Object ID of the developer or CI principal to grant data-plane access (for local development against Azure). Empty to skip.')
param principalId string = ''

@description('Type of `principalId`.')
@allowed(['User', 'ServicePrincipal', 'Group'])
param principalType string = 'User'

@description('Chat model used for scoring and content generation. gpt-5.1 is Microsoft\'s designated replacement for gpt-4o (retiring 2027-04-14).')
param chatModel modelDeploymentType = {
  name: 'chat'
  model: 'gpt-5.1'
  version: '2025-11-13'
  sku: 'GlobalStandard'
  capacity: 30
}

@description('True when the chat model is a reasoning model (GPT-5.x, o-series), which rejects temperature/max_tokens.')
param chatModelIsReasoning bool = startsWith(chatModel.model, 'gpt-5') || startsWith(chatModel.model, 'o')

@description('Embedding model used for transcript alignment and duplicate detection.')
param embeddingModel modelDeploymentType = {
  name: 'embedding'
  model: 'text-embedding-3-small'
  version: '1'
  sku: 'GlobalStandard'
  capacity: 30
}

@description('Azure OpenAI data-plane API version used by the application.')
param openAiApiVersion string = '2025-04-01-preview'

@description('Expose the API outside the Container Apps environment. Default false: only the dashboard (same environment) can reach it.')
param apiExternalIngress bool = false

// ---------------------------------------------------------------------------
// Naming
// ---------------------------------------------------------------------------

var abbrs = loadJsonContent('abbreviations.json')
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var tags = {
  'azd-env-name': environmentName
  application: 'linguaproctor'
}
var cosmosDatabaseName = 'linguaproctor'
var auditContainerName = 'audit'
var apiAppName = '${abbrs.appContainerApps}api-${resourceToken}'
var dashboardAppName = '${abbrs.appContainerApps}dash-${resourceToken}'

// Developer access is optional; build role lists conditionally.
var developerGrants = empty(principalId) ? [] : [principalId]

resource rg 'Microsoft.Resources/resourceGroups@2025-04-01' = {
  name: '${abbrs.resourcesResourceGroups}${environmentName}'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Observability
// ---------------------------------------------------------------------------

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    tags: tags
    logAnalyticsName: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
    applicationInsightsName: '${abbrs.insightsComponents}${resourceToken}'
  }
}

// ---------------------------------------------------------------------------
// Identities (one per workload: least privilege)
// ---------------------------------------------------------------------------

module apiIdentity 'modules/identity.bicep' = {
  name: 'api-identity'
  scope: rg
  params: {
    name: '${abbrs.managedIdentityUserAssignedIdentities}api-${resourceToken}'
    location: location
    tags: tags
  }
}

module dashboardIdentity 'modules/identity.bicep' = {
  name: 'dashboard-identity'
  scope: rg
  params: {
    name: '${abbrs.managedIdentityUserAssignedIdentities}dash-${resourceToken}'
    location: location
    tags: tags
  }
}

// ---------------------------------------------------------------------------
// AI services
// ---------------------------------------------------------------------------

var developerOpenAiGrants roleAssignmentType[] = [
  for id in developerGrants: {
    principalId: id
    principalType: principalType
    roleDefinitionId: roles.cognitiveServicesOpenAIUser
  }
]

module openAi 'modules/openai.bicep' = {
  name: 'openai'
  scope: rg
  params: {
    name: '${abbrs.cognitiveServicesOpenAI}${resourceToken}'
    location: openAiLocation
    tags: tags
    deployments: [chatModel, embeddingModel]
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    roleAssignments: concat(
      [
        {
          principalId: apiIdentity.outputs.principalId
          principalType: 'ServicePrincipal'
          roleDefinitionId: roles.cognitiveServicesOpenAIUser
        }
      ],
      developerOpenAiGrants
    )
  }
}

var developerSpeechGrants roleAssignmentType[] = [
  for id in developerGrants: {
    principalId: id
    principalType: principalType
    roleDefinitionId: roles.cognitiveServicesSpeechUser
  }
]

module speech 'modules/speech.bicep' = {
  name: 'speech'
  scope: rg
  params: {
    name: '${abbrs.cognitiveServicesSpeech}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    roleAssignments: concat(
      [
        {
          principalId: apiIdentity.outputs.principalId
          principalType: 'ServicePrincipal'
          roleDefinitionId: roles.cognitiveServicesSpeechUser
        }
      ],
      developerSpeechGrants
    )
  }
}

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  scope: rg
  params: {
    accountName: '${abbrs.documentDBDatabaseAccounts}${resourceToken}'
    location: location
    tags: tags
    databaseName: cosmosDatabaseName
    auditContainerName: auditContainerName
    auditAppenderPrincipalIds: [apiIdentity.outputs.principalId]
    dataContributorPrincipalIds: developerGrants
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
  }
}

// ---------------------------------------------------------------------------
// Container hosting
// ---------------------------------------------------------------------------

module registry 'modules/container-registry.bicep' = {
  name: 'container-registry'
  scope: rg
  params: {
    name: '${abbrs.containerRegistryRegistries}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    roleAssignments: [
      {
        principalId: apiIdentity.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionId: roles.acrPull
      }
      {
        principalId: dashboardIdentity.outputs.principalId
        principalType: 'ServicePrincipal'
        roleDefinitionId: roles.acrPull
      }
    ]
  }
}

module containerAppsEnvironment 'modules/container-apps-environment.bicep' = {
  name: 'container-apps-environment'
  scope: rg
  params: {
    name: '${abbrs.appManagedEnvironments}${resourceToken}'
    location: location
    tags: tags
    logAnalyticsWorkspaceName: monitoring.outputs.logAnalyticsWorkspaceName
  }
}

// ---------------------------------------------------------------------------
// Outputs: written to the azd environment. Consumed by infra/app/*.parameters.json
// at `azd deploy` and by infra/scripts/write-env.* for local development.
// None of these values is a secret.
// ---------------------------------------------------------------------------

output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output AZURE_RESOURCE_GROUP string = rg.name

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = registry.outputs.name
output AZURE_CONTAINER_ENVIRONMENT_NAME string = containerAppsEnvironment.outputs.name

output SERVICE_API_NAME string = apiAppName
output SERVICE_API_IDENTITY_NAME string = apiIdentity.outputs.name
output SERVICE_API_EXTERNAL_INGRESS bool = apiExternalIngress
output SERVICE_DASHBOARD_NAME string = dashboardAppName
output SERVICE_DASHBOARD_IDENTITY_NAME string = dashboardIdentity.outputs.name

output API_URI string = apiExternalIngress
  ? 'https://${apiAppName}.${containerAppsEnvironment.outputs.defaultDomain}'
  : 'https://${apiAppName}.internal.${containerAppsEnvironment.outputs.defaultDomain}'
output DASHBOARD_URI string = 'https://${dashboardAppName}.${containerAppsEnvironment.outputs.defaultDomain}'

// Application settings (names match src/config.py)
output AZURE_OPENAI_ENDPOINT string = openAi.outputs.endpoint
output AZURE_OPENAI_API_VERSION string = openAiApiVersion
output AZURE_OPENAI_CHAT_DEPLOYMENT string = chatModel.name
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = embeddingModel.name
output AZURE_OPENAI_CHAT_REASONING_MODEL bool = chatModelIsReasoning
output AZURE_SPEECH_ENDPOINT string = speech.outputs.endpoint
output AZURE_SPEECH_REGION string = speech.outputs.region
output COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output COSMOS_DATABASE_NAME string = cosmos.outputs.databaseName
output COSMOS_AUDIT_CONTAINER string = cosmos.outputs.auditContainerName
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.applicationInsightsConnectionString
