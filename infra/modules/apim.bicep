// API Management: the Tier-2 gateway in front of the Experience, Process and System APIs.
param location string
param name string
param tags object
@allowed([ 'Consumption', 'Developer', 'Basic', 'Standard', 'Premium', 'BasicV2', 'StandardV2' ])
param sku string = 'Developer'
param publisherEmail string
param publisherName string = 'Sales Order Data Product'
param uamiId string
param uamiClientId string
param keyVaultUri string
param experienceFqdn string
param processFqdn string
param systemFqdn string
@description('Origin of the console static website, e.g. https://stweb.z13.web.core.windows.net')
param consoleOrigin string
param appInsightsId string = ''
param appInsightsInstrumentationKey string = ''
@description('Expose the System API (simulator + master data) through APIM. Disable for prod.')
param exposeSystemApi bool = true

var isConsumption = sku == 'Consumption'
var rateLimit = isConsumption ? '' : loadTextContent('../apim/policies/rate-limit.xml')
var globalPolicy = replace(replace(loadTextContent('../apim/policies/global.xml'), '__CORS_ORIGIN__', consoleOrigin), '__RATE_LIMIT__', rateLimit)

resource apim 'Microsoft.ApiManagement/service@2022-08-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: sku, capacity: isConsumption ? 0 : 1 }
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uamiId}': {} }
  }
  properties: {
    publisherEmail: publisherEmail
    publisherName: publisherName
  }
}

resource nvApiKey 'Microsoft.ApiManagement/service/namedValues@2022-08-01' = {
  parent: apim
  name: 'tfso-api-key'
  properties: {
    displayName: 'tfso-api-key'
    secret: true
    keyVault: {
      secretIdentifier: '${keyVaultUri}secrets/tfso-api-key'
      identityClientId: uamiClientId
    }
  }
}

resource globalPol 'Microsoft.ApiManagement/service/policies@2022-08-01' = {
  parent: apim
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: globalPolicy
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2022-08-01' = if (!empty(appInsightsId)) {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsightsId
    credentials: { instrumentationKey: appInsightsInstrumentationKey }
  }
}

resource diag 'Microsoft.ApiManagement/service/diagnostics@2022-08-01' = if (!empty(appInsightsId)) {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    httpCorrelationProtocol: 'W3C'
    sampling: { samplingType: 'fixed', percentage: 100 }
  }
}

var allApis = [
  { name: 'tfso-experience', displayName: 'Sales Order - Experience APIs', path: 'exp', fqdn: experienceFqdn, description: 'Tier-2 channel APIs: portal, partner, EDI X12 850, email.' }
  { name: 'tfso-process', displayName: 'Sales Order - Process APIs (Order Status)', path: 'process', fqdn: processFqdn, description: 'Order intake, fulfillment orchestration and the Order Status API.' }
  { name: 'tfso-system', displayName: 'Sales Order - System APIs', path: 'system', fqdn: systemFqdn, description: 'D365 / WMS / TMS / CRM / Lakehouse adapters, master data and fulfillment simulator (non-prod).' }
]
var apis = exposeSystemApi ? allApis : take(allApis, 2)

resource apiRes 'Microsoft.ApiManagement/service/apis@2022-08-01' = [for a in apis: {
  parent: apim
  name: a.name
  properties: {
    displayName: a.displayName
    description: a.description
    path: a.path
    protocols: [ 'https' ]
    serviceUrl: 'https://${a.fqdn}'
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: 'Ocp-Apim-Subscription-Key', query: 'subscription-key' }
    apiType: 'http'
    type: 'http'
  }
}]

resource apiPolicies 'Microsoft.ApiManagement/service/apis/policies@2022-08-01' = [for (a, i) in apis: {
  parent: apiRes[i]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../apim/policies/api-backend.xml')
  }
  dependsOn: [ nvApiKey ]
}]

// Pass-through operations (the services publish their own OpenAPI at /openapi.json)
var methods = [ 'GET', 'POST', 'PUT', 'PATCH', 'DELETE' ]
var operations = flatten(map(range(0, length(apis)), i => map(methods, m => {
  apiIndex: i
  method: m
})))

resource ops 'Microsoft.ApiManagement/service/apis/operations@2022-08-01' = [for o in operations: {
  parent: apiRes[o.apiIndex]
  name: toLower('${o.method}-all')
  properties: {
    displayName: '${o.method} /*'
    method: o.method
    urlTemplate: '/*'
    templateParameters: []
    responses: []
  }
}]

resource prodSalesOrder 'Microsoft.ApiManagement/service/products@2022-08-01' = {
  parent: apim
  name: 'sales-order-data-product'
  properties: {
    displayName: 'Sales Order Data Product'
    description: 'Channel capture, order processing and status for trusted applications (console, partners).'
    subscriptionRequired: true
    approvalRequired: true
    subscriptionsLimit: 50
    state: 'published'
  }
}

resource prodSalesOrderPolicy 'Microsoft.ApiManagement/service/products/policies@2022-08-01' = {
  parent: prodSalesOrder
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../apim/policies/product-sales-order.xml')
  }
}

resource prodSalesOrderApis 'Microsoft.ApiManagement/service/products/apis@2022-08-01' = [for (a, i) in apis: {
  parent: prodSalesOrder
  name: a.name
  dependsOn: [ apiRes[i] ]
}]

resource prodStatus 'Microsoft.ApiManagement/service/products@2022-08-01' = {
  parent: apim
  name: 'order-status-api'
  properties: {
    displayName: 'Order Status API'
    description: 'Read-only order status, history and documents (GET on /process).'
    subscriptionRequired: true
    approvalRequired: true
    state: 'published'
  }
}

resource prodStatusPolicy 'Microsoft.ApiManagement/service/products/policies@2022-08-01' = {
  parent: prodStatus
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../apim/policies/product-order-status.xml')
  }
}

resource prodStatusApi 'Microsoft.ApiManagement/service/products/apis@2022-08-01' = {
  parent: prodStatus
  name: 'tfso-process'
  dependsOn: [ apiRes[1] ]
}

@description('Subscription used by the Order Operations Console (key fetched by deploy.sh via listSecrets)')
resource consoleSub 'Microsoft.ApiManagement/service/subscriptions@2022-08-01' = {
  parent: apim
  name: 'order-console'
  properties: {
    displayName: 'Order Operations Console'
    scope: prodSalesOrder.id
    state: 'active'
    allowTracing: false
  }
  dependsOn: [ prodSalesOrderApis ]
}

output id string = apim.id
output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output consoleSubscriptionName string = consoleSub.name
