param location string
param name string
param tags object
param tenantId string = subscription().tenantId

@secure()
param postgresAdminPassword string
@secure()
param apiKey string
@secure()
param d365ClientSecret string
@secure()
param processDbUrl string
@secure()
param systemDbUrl string
@secure()
param appInsightsConnectionString string
param enablePurgeProtection bool = false

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: name
  location: location
  tags: tags
  properties: {
    tenantId: tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
  }
}

var secrets = [
  { name: 'postgres-admin-password', value: postgresAdminPassword }
  { name: 'tfso-api-key', value: apiKey }
  { name: 'd365-client-secret', value: empty(d365ClientSecret) ? 'not-configured' : d365ClientSecret }
  { name: 'tfso-process-db-url', value: processDbUrl }
  { name: 'tfso-system-db-url', value: systemDbUrl }
  { name: 'appinsights-connection-string', value: appInsightsConnectionString }
]

resource kvSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = [for s in secrets: {
  parent: kv
  name: s.name
  properties: {
    value: s.value
    contentType: 'text/plain'
  }
}]

output id string = kv.id
output name string = kv.name
output uri string = kv.properties.vaultUri
