// LinguaProctor API container app — deployed by `azd deploy` (revision-based):
// the image and this configuration ship together as a single revision.
targetScope = 'resourceGroup'

@description('azd environment name.')
param environmentName string

@description('Region.')
param location string = resourceGroup().location

@description('Container app name (from provision output SERVICE_API_NAME).')
param name string

@description('Image built and pushed by azd (SERVICE_API_IMAGE_NAME).')
param imageName string

@description('User-assigned identity used for registry pulls and all Azure data-plane calls.')
param identityName string

param containerRegistryName string
param containerAppsEnvironmentName string

// azd substitutes env vars into the parameters file as JSON strings, so
// boolean flags arrive as 'true'/'false' and are parsed here.
@description('Expose the API outside the environment ("true"/"false").')
param externalIngress string = 'false'

param openAiEndpoint string
param openAiApiVersion string
param openAiChatDeployment string
param openAiEmbeddingDeployment string
@description('Whether the chat deployment is a reasoning model ("true"/"false").')
param openAiChatReasoningModel string
param speechEndpoint string
param speechRegion string
param cosmosEndpoint string
param cosmosDatabaseName string
param cosmosAuditContainer string
param applicationInsightsConnectionString string

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: identityName
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: containerRegistryName
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: containerAppsEnvironmentName
}

var port = 8000
var isExternalIngress = toLower(trim(externalIngress)) == 'true'
var isReasoningModel = toLower(trim(openAiChatReasoningModel)) == 'true'

resource api 'Microsoft.App/containerApps@2025-01-01' = {
  name: name
  location: location
  tags: {
    'azd-env-name': environmentName
    'azd-service-name': 'api'
    application: 'linguaproctor'
  }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    environmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: isExternalIngress
        targetPort: port
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registry.properties.loginServer
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: imageName
          // Explicit entrypoint: azd's remote ACR build ignores `docker.target`
          // and builds the Dockerfile's last stage (the dashboard), so the
          // stage's CMD can't be relied on. Mirrors the `api` stage CMD.
          command: [
            'uvicorn'
            'src.main:app'
            '--host'
            '0.0.0.0'
            '--port'
            string(port)
            '--proxy-headers'
            '--forwarded-allow-ips=*'
          ]
          resources: {
            cpu: json('1.0')
            memory: '2Gi'
          }
          env: [
            // Selects the user-assigned identity for DefaultAzureCredential.
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'AZURE_OPENAI_ENDPOINT', value: openAiEndpoint }
            { name: 'AZURE_OPENAI_API_VERSION', value: openAiApiVersion }
            { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: openAiChatDeployment }
            { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: openAiEmbeddingDeployment }
            { name: 'AZURE_OPENAI_CHAT_REASONING_MODEL', value: string(isReasoningModel) }
            { name: 'AZURE_SPEECH_ENDPOINT', value: speechEndpoint }
            { name: 'AZURE_SPEECH_REGION', value: speechRegion }
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'COSMOS_DATABASE_NAME', value: cosmosDatabaseName }
            { name: 'COSMOS_AUDIT_CONTAINER', value: cosmosAuditContainer }
            { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: applicationInsightsConnectionString }
            { name: 'APP_ENVIRONMENT', value: environmentName }
            { name: 'APP_DEBUG', value: 'false' }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health', port: port }
              periodSeconds: 5
              failureThreshold: 24
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: port }
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: port }
              periodSeconds: 30
              failureThreshold: 3
            }
          ]
        }
      ]
      // Exactly one replica: the review queue, results and calibration data are
      // held in process. Scaling out (or to zero) would split or lose that state.
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
}

output name string = api.name
output uri string = 'https://${api.properties.configuration.ingress.fqdn}'
