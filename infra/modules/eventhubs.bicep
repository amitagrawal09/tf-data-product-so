param location string
param name string
param tags object
param hubName string = 'order-status-stream'
param partitionCount int = 4

resource ehns 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 }
  properties: {
    minimumTlsVersion: '1.2'
    isAutoInflateEnabled: true
    maximumThroughputUnits: 4
    kafkaEnabled: true
  }
}

resource hub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehns
  name: hubName
  properties: {
    partitionCount: partitionCount
    messageRetentionInDays: 7
  }
}

resource cgDatabricks 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: hub
  name: 'databricks'
  properties: {
    userMetadata: 'Azure Databricks structured streaming -> bronze_order_events'
  }
}

output id string = ehns.id
output name string = ehns.name
output fqdn string = '${ehns.name}.servicebus.windows.net'
output hubName string = hub.name
