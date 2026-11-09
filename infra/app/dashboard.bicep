// LinguaProctor reviewer dashboard (Streamlit) — deployed by `azd deploy`.
targetScope = 'resourceGroup'

@description('azd environment name.')
param environmentName string

@description('Region.')
param location string = resourceGroup().location

@description('Container app name (from provision output SERVICE_DASHBOARD_NAME).')
param name string

@description('Image built and pushed by azd (SERVICE_DASHBOARD_IMAGE_NAME).')
param imageName string

@description('User-assigned identity used for registry pulls.')
param identityName string

param containerRegistryName string
param containerAppsEnvironmentName string

@description('Name of the API container app (same environment).')
param apiName string

@description('Whether the API uses external ingress ("true"/"false"; determines its FQDN).')
param apiExternalIngress string

@description('Comma-separated CIDR ranges allowed to reach the dashboard (e.g. "203.0.113.0/24,198.51.100.7/32"). Empty = no IP restriction.')
param allowedIpRanges string = ''

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' existing = {
  name: identityName
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' existing = {
  name: containerRegistryName
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: containerAppsEnvironmentName
}

var port = 8501
var apiUrl = toLower(trim(apiExternalIngress)) == 'true'
  ? 'https://${apiName}.${environment.properties.defaultDomain}'
  : 'https://${apiName}.internal.${environment.properties.defaultDomain}'
var ipRanges = filter(map(split(allowedIpRanges, ','), r => trim(r)), r => !empty(r))

resource dashboard 'Microsoft.App/containerApps@2025-01-01' = {
  name: name
  location: location
  tags: {
    'azd-env-name': environmentName
    'azd-service-name': 'dashboard'
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
        external: true
        targetPort: port
        transport: 'auto'
        allowInsecure: false
        // Streamlit keeps session state per websocket: pin users to a replica.
        stickySessions: {
          affinity: 'sticky'
        }
        ipSecurityRestrictions: [
          for (range, i) in ipRanges: {
            name: 'allow-${i}'
            action: 'Allow'
            ipAddressRange: range
          }
        ]
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
          name: 'dashboard'
          image: imageName
          // Explicit entrypoint: azd's remote ACR build ignores `docker.target`,
          // so don't rely on the image's stage CMD. Mirrors the `dashboard` stage.
          command: [
            'streamlit'
            'run'
            'dashboard/app.py'
            '--server.port=${port}'
            '--server.address=0.0.0.0'
            '--server.headless=true'
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            { name: 'LINGUAPROCTOR_API_URL', value: apiUrl }
          ]
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/_stcore/health', port: port }
              periodSeconds: 5
              failureThreshold: 24
            }
            {
              type: 'Liveness'
              httpGet: { path: '/_stcore/health', port: port }
              periodSeconds: 30
              failureThreshold: 3
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 2
      }
    }
  }
}

output name string = dashboard.name
output uri string = 'https://${dashboard.properties.configuration.ingress.fqdn}'
