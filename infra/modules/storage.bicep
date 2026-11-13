param location string
@description('ADLS Gen2 lakehouse account name (HNS)')
param lakeName string
@description('Static website account name for the Order Operations Console')
param webName string
param tags object
@allowed([ 'Standard_LRS', 'Standard_ZRS', 'Standard_GRS', 'Standard_RAGRS' ])
param skuName string = 'Standard_LRS'

resource lake 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: lakeName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: skuName }
  properties: {
    isHnsEnabled: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true // Databricks Unity Catalog uses an access connector (MI); apps use MI
    supportsHttpsTrafficOnly: true
    accessTier: 'Hot'
  }
}

resource lakeBlob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: lake
  name: 'default'
}

resource medallion 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [for c in [ 'bronze', 'silver', 'gold', 'checkpoints' ]: {
  parent: lakeBlob
  name: c
  properties: { publicAccess: 'None' }
}]

resource web 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: webName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: true
    supportsHttpsTrafficOnly: true
    accessTier: 'Hot'
  }
}

output lakeId string = lake.id
output lakeName string = lake.name
output lakeDfsUrl string = lake.properties.primaryEndpoints.dfs
output lakeBlobUrl string = lake.properties.primaryEndpoints.blob
output webId string = web.id
output webName string = web.name
output webEndpoint string = web.properties.primaryEndpoints.web
