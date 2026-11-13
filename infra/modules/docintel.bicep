param location string
param name string
param tags object

resource di 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'FormRecognizer'
  sku: { name: 'S0' }
  properties: {
    customSubDomainName: name // required for Entra ID (managed identity) auth
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

output id string = di.id
output name string = di.name
output endpoint string = di.properties.endpoint
