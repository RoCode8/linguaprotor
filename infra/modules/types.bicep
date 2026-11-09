// Shared user-defined types (imported with `import { ... } from 'types.bicep'`).

@export()
@description('A data-plane role grant for a Microsoft Entra principal.')
type roleAssignmentType = {
  @description('Object ID of the principal (managed identity, user or group).')
  principalId: string

  @description('Kind of principal; required so ARM does not wait on directory replication.')
  principalType: 'ServicePrincipal' | 'User' | 'Group'

  @description('GUID of the built-in role definition.')
  roleDefinitionId: string
}

@export()
@description('Azure OpenAI model deployment.')
type modelDeploymentType = {
  @description('Deployment name the application calls (model-agnostic, e.g. "chat").')
  name: string

  @description('Model name in the Azure OpenAI catalog.')
  model: string

  @description('Model version. Must be GA or Legacy for new subscriptions (Deprecated versions are blocked).')
  version: string

  @description('Deployment SKU. GlobalStandard has the widest availability; DataZoneStandard keeps processing in the data zone.')
  sku: 'GlobalStandard' | 'DataZoneStandard' | 'Standard'

  @description('Throughput in units of 1,000 tokens per minute.')
  capacity: int
}

@export()
@description('Built-in Azure role definition GUIDs used by this solution.')
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  cognitiveServicesOpenAIUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  cognitiveServicesSpeechUser: 'f2dc8367-1007-4938-bd23-fe263f013447'
}
