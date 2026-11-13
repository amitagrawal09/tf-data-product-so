param location string
param stem string
param tags object

@description('Single user-assigned identity shared by all Tier-2 container apps and APIM')
resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${stem}'
  location: location
  tags: tags
}

output id string = uami.id
output name string = uami.name
output principalId string = uami.properties.principalId
output clientId string = uami.properties.clientId
