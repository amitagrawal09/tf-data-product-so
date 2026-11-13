@description('Azure region')
param location string
@description('Resource name stem, e.g. tfso-dev')
param stem string
param tags object
@description('Log retention in days')
param retentionInDays int = 30

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${stem}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: retentionInDays
    features: { enableLogAccessUsingOnlyResourcePermissions: true }
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${stem}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    IngestionMode: 'LogAnalytics'
  }
}

output logAnalyticsId string = law.id
output logAnalyticsName string = law.name
output appInsightsName string = appi.name
output appInsightsConnectionString string = appi.properties.ConnectionString
output appInsightsId string = appi.id
output appInsightsInstrumentationKey string = appi.properties.InstrumentationKey
