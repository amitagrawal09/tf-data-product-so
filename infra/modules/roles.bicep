// RBAC for the shared user-assigned identity (least privilege per resource).
param principalId string
param acrName string
param keyVaultName string
param serviceBusName string
param eventHubsName string
param lakeStorageName string
param docIntelName string

var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  serviceBusDataOwner: '090c5cfd-751d-490a-894a-3ce6f1109419'
  eventHubsDataSender: '2b629674-e913-4c01-ae53-ef4638d8f975'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: acrName }
resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: keyVaultName }
resource sb 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = { name: serviceBusName }
resource eh 'Microsoft.EventHub/namespaces@2024-01-01' existing = { name: eventHubsName }
resource lake 'Microsoft.Storage/storageAccounts@2023-05-01' existing = { name: lakeStorageName }
resource di 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = { name: docIntelName }

resource raAcr 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, principalId, roles.acrPull)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

resource raKv 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: kv
  name: guid(kv.id, principalId, roles.keyVaultSecretsUser)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

// Data Owner = send + receive + manage subscriptions/rules (consumers create receivers on subscriptions)
resource raSb 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: sb
  name: guid(sb.id, principalId, roles.serviceBusDataOwner)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.serviceBusDataOwner)
  }
}

resource raEh 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: eh
  name: guid(eh.id, principalId, roles.eventHubsDataSender)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.eventHubsDataSender)
  }
}

resource raLake 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: lake
  name: guid(lake.id, principalId, roles.storageBlobDataContributor)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
  }
}

resource raDi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: di
  name: guid(di.id, principalId, roles.cognitiveServicesUser)
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.cognitiveServicesUser)
  }
}

output keyVaultRoleAssignmentId string = raKv.id
output acrRoleAssignmentId string = raAcr.id
