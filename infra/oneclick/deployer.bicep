// Resource-group part of the one-click deployment: deployer identity + deployment script.
targetScope = 'resourceGroup'

param prefix string
param environment string
param location string
param tags object
param apimPublisherEmail string
param apimSku string
param repositoryUrl string
param repositoryRef string
@secure()
param gitHubToken string
param loadDemoData bool
param runSmokeTest bool
param d365Url string
param d365TenantId string
param d365ClientId string
@secure()
param d365ClientSecret string
param d365Company string
param forceUpdateTag string

// Owner is scoped to this resource group only: the platform template assigns roles (AcrPull, Key Vault,
// Service Bus, Event Hubs, Storage) to the workload identity, which needs roleAssignments/write.
var ownerRoleId = '8e3af657-a8ff-443c-a75c-2fe8c4bcb635'

resource deployerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}-${environment}-deployer'
  location: location
  tags: tags
}

resource deployerOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployerIdentity.id, ownerRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', ownerRoleId)
    principalId: deployerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    description: 'One-click deployer for the Sales Order data product (resource-group scope)'
  }
}

// Optional values are only passed when set (deployment scripts reject empty environment variables).
var baseEnv = [
  { name: 'PREFIX', value: prefix }
  { name: 'ENVIRONMENT', value: environment }
  { name: 'LOCATION', value: location }
  { name: 'RG', value: resourceGroup().name }
  { name: 'APIM_PUBLISHER_EMAIL', value: apimPublisherEmail }
  { name: 'APIM_SKU', value: apimSku }
  { name: 'REPO_URL', value: repositoryUrl }
  { name: 'REPO_REF', value: repositoryRef }
  { name: 'LOAD_DEMO_DATA', value: loadDemoData ? 'true' : 'false' }
  { name: 'SKIP_SMOKE', value: runSmokeTest ? 'false' : 'true' }
  { name: 'D365_COMPANY', value: d365Company }
  { name: 'DEPLOYER_PRINCIPAL_ID', value: deployerIdentity.properties.principalId }
]
var tokenEnv = empty(gitHubToken) ? [] : [ { name: 'REPO_TOKEN', secureValue: gitHubToken } ]
var d365Env = empty(d365Url) ? [] : [
  { name: 'D365_URL', value: d365Url }
  { name: 'D365_TENANT_ID', value: d365TenantId }
  { name: 'D365_CLIENT_ID', value: d365ClientId }
  { name: 'D365_CLIENT_SECRET', secureValue: d365ClientSecret }
]

resource deploy 'Microsoft.Resources/deploymentScripts@2023-08-01' = {
  name: 'ds-${prefix}-${environment}-deploy'
  location: location
  tags: tags
  kind: 'AzureCLI'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${deployerIdentity.id}': {}
    }
  }
  dependsOn: [ deployerOwner ]
  properties: {
    azCliVersion: '2.63.0'
    timeout: 'PT2H'
    retentionInterval: 'P1D'
    cleanupPreference: 'OnSuccess'
    forceUpdateTag: forceUpdateTag
    environmentVariables: concat(baseEnv, tokenEnv, d365Env)
    scriptContent: loadTextContent('bootstrap.sh')
  }
}

output consoleUrl string = deploy.properties.outputs.consoleUrl
output apiGatewayUrl string = deploy.properties.outputs.apiGatewayUrl
