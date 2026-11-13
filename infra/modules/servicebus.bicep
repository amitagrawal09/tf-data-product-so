param location string
param name string
param tags object
@allowed([ 'Standard', 'Premium' ])
param sku string = 'Standard'

resource sb 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: name
  location: location
  tags: tags
  sku: { name: sku, tier: sku }
  properties: {
    minimumTlsVersion: '1.2'
    disableLocalAuth: false // KEDA scaler uses the keda-monitor rule; apps use Entra ID (managed identity)
  }
}

// Topic -> subscriptions (blueprint section 4)
var topology = [
  { topic: 'sales-order-commands', subscriptions: [ 'system-api' ] }
  { topic: 'system-status-events', subscriptions: [ 'process-api' ] }
  { topic: 'sales-order-events', subscriptions: [ 'system-lakehouse', 'system-crm', 'process-webhooks' ] }
]

resource topics 'Microsoft.ServiceBus/namespaces/topics@2022-10-01-preview' = [for t in topology: {
  parent: sb
  name: t.topic
  properties: {
    defaultMessageTimeToLive: 'P14D'
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT10M'
    supportOrdering: true
    enableBatchedOperations: true
  }
}]

var flatSubs = flatten(map(range(0, length(topology)), i => map(topology[i].subscriptions, s => {
  topicIndex: i
  name: s
})))

resource subs 'Microsoft.ServiceBus/namespaces/topics/subscriptions@2022-10-01-preview' = [for s in flatSubs: {
  parent: topics[s.topicIndex]
  name: s.name
  properties: {
    lockDuration: 'PT1M'
    maxDeliveryCount: 10
    deadLetteringOnMessageExpiration: true
    deadLetteringOnFilterEvaluationExceptions: true
    defaultMessageTimeToLive: 'P14D'
    requiresSession: false
    enableBatchedOperations: true
  }
}]

@description('Used only by the KEDA azure-servicebus scaler (needs Manage to read message counts)')
resource kedaRule 'Microsoft.ServiceBus/namespaces/AuthorizationRules@2022-10-01-preview' = {
  parent: sb
  name: 'keda-monitor'
  properties: {
    rights: [ 'Listen', 'Send', 'Manage' ]
  }
}

output id string = sb.id
output name string = sb.name
output fqdn string = '${sb.name}.servicebus.windows.net'
output kedaRuleId string = kedaRule.id
