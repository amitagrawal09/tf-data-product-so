// Resource-group part of the one-click deployment: the deployment script that runs deploy.sh azure.
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

@description('Resource id of the deployer managed identity (oneclick/identity.bicep)')
param deployerIdentityId string
param deployerPrincipalId string
@description('Role assignments that must exist before the script runs (implicit dependency)')
param prerequisiteAssignmentIds array

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
  { name: 'DEPLOYER_PRINCIPAL_ID', value: deployerPrincipalId }
  { name: 'PREREQUISITES', value: join(prerequisiteAssignmentIds, ',') }
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
      '${deployerIdentityId}': {}
    }
  }
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
