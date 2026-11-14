// =====================================================================================
// ONE-CLICK DEPLOYMENT - Sales Order data product on Azure
//
// Deploy from the portal ("Deploy to Azure" button or a Template Spec), the CLI, or Cloud Shell:
//   az deployment sub create -l eastus2 -f infra/oneclick.bicep \
//      -p apimPublisherEmail=owner@contoso.com
//
// What happens:
//   1. Creates the resource group rg-<prefix>-<environment>.
//   2. Creates a deployer managed identity with Owner on that resource group only
//      (the platform template creates role assignments for the workload identity).
//   3. Runs an Azure deployment script (Azure CLI container) that downloads this repository and
//      runs ./deploy.sh azure: platform Bicep → image build in ACR → Container Apps rollout →
//      APIM keys → console published to the static website → optional smoke test / demo data.
//   4. Returns the console URL and API gateway URL as deployment outputs.
//
// Requirements: the person deploying needs Owner (or Contributor + User Access Administrator) on the
// subscription. A private repository needs a GitHub token with read access to its contents.
// =====================================================================================
targetScope = 'subscription'

@description('Short name prefix (2-8 lowercase letters/digits, starting with a letter)')
@minLength(2)
@maxLength(8)
param prefix string = 'tfso'

@description('Environment to create')
@allowed([ 'dev', 'test', 'prod' ])
param environment string = 'dev'

@description('Azure region for every resource')
param location string = deployment().location

@description('API Management publisher e-mail (required by APIM)')
param apimPublisherEmail string

@description('API Management SKU. Developer ≈ 30-45 min to provision; Consumption/BasicV2 are faster')
@allowed([ 'Developer', 'Consumption', 'BasicV2', 'StandardV2', 'Basic', 'Standard', 'Premium' ])
param apimSku string = 'Developer'

@description('GitHub repository that holds this solution')
param repositoryUrl string = 'https://github.com/amitagrawal09/tf-data-product-so'

@description('Branch, tag or commit to deploy')
param repositoryRef string = 'claude/dreamy-shannon-1j1v3d'

@secure()
@description('GitHub token with read access to the repository (only needed for private repositories)')
param gitHubToken string = ''

@description('Load demo orders across all regions after deployment (ignored for prod)')
param loadDemoData bool = true

@description('Run the end-to-end smoke test through API Management after deployment')
param runSmokeTest bool = true

@description('Dynamics 365 F&O URL. Leave empty to run the D365 simulator.')
param d365Url string = ''
param d365TenantId string = ''
param d365ClientId string = ''
@secure()
param d365ClientSecret string = ''
param d365Company string = 'usmf'

@description('Changes on every deployment so the deployment script re-runs (leave the default)')
param forceUpdateTag string = utcNow()

var rgName = 'rg-${prefix}-${environment}'
var tags = {
  'data-product': 'sales-order'
  environment: environment
  'managed-by': 'oneclick'
}

resource rg 'Microsoft.Resources/resourceGroups@2022-09-01' = {
  name: rgName
  location: location
  tags: tags
}

module deployer 'oneclick/deployer.bicep' = {
  name: 'tfso-oneclick-${environment}'
  scope: rg
  params: {
    prefix: prefix
    environment: environment
    location: location
    tags: tags
    apimPublisherEmail: apimPublisherEmail
    apimSku: apimSku
    repositoryUrl: repositoryUrl
    repositoryRef: repositoryRef
    gitHubToken: gitHubToken
    loadDemoData: loadDemoData && environment != 'prod'
    runSmokeTest: runSmokeTest
    d365Url: d365Url
    d365TenantId: d365TenantId
    d365ClientId: d365ClientId
    d365ClientSecret: d365ClientSecret
    d365Company: d365Company
    forceUpdateTag: forceUpdateTag
  }
}

output resourceGroup string = rg.name
output consoleUrl string = deployer.outputs.consoleUrl
output apiGatewayUrl string = deployer.outputs.apiGatewayUrl
output orderStatusApi string = '${deployer.outputs.apiGatewayUrl}/process/v1/orders/{orderId}/status'
