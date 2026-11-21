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
@description('Authentication mode (docs/07 §1, docs/08): entra | dev | apikey')
@allowed([ 'apikey', 'dev', 'entra' ])
param authMode string = 'dev'
@description('Entra ID workforce tenant id (entra mode)')
param entraTenantId string = ''
@description('Accepted audiences, comma-separated (API client id and/or api://<client-id>)')
param entraAudience string = ''
@description('Optional Entra External ID issuer (customers and vendors)')
param entraExternalIssuer string = ''

var isConsumption = sku == 'Consumption'
var rateLimit = isConsumption ? '' : loadTextContent('../apim/policies/rate-limit.xml')

// ---------------------------------------------------------------- authentication policy fragments
// Callers never supply the backend key; in entra mode dev persona headers are stripped as well.
var authStrip = authMode == 'entra' ? '<set-header name="x-api-key" exists-action="delete" /><set-header name="x-dev-persona" exists-action="delete" />' : '<set-header name="x-api-key" exists-action="delete" />'
var setBackendKey = loadTextContent('../apim/policies/auth/set-backend-key.xml')
var externalIssuer = endsWith(entraExternalIssuer, '/') ? take(entraExternalIssuer, max(length(entraExternalIssuer) - 1, 0)) : entraExternalIssuer
var workforceOpenIdUrl = 'https://login.microsoftonline.com/${entraTenantId}/v2.0/.well-known/openid-configuration'
var openIdConfigUrls = empty(externalIssuer) ? [ workforceOpenIdUrl ] : [ workforceOpenIdUrl, '${externalIssuer}/.well-known/openid-configuration' ]
var openIdConfigXml = join(map(openIdConfigUrls, u => '<openid-config url="${u}" />'), '')
// An empty audience list fails closed: no token is accepted until scripts/entra-setup.sh has supplied one.
var audienceXml = join(map(filter(split(entraAudience, ','), a => !empty(trim(a))), a => '<audience>${trim(a)}</audience>'), '')
var jwtPolicy = replace(replace(loadTextContent('../apim/policies/auth/jwt.xml'), '__OPENID_CONFIGS__', openIdConfigXml), '__AUDIENCES__', audienceXml)
var serviceAuthEntra = replace(replace(loadTextContent('../apim/policies/auth/service-entra.xml'), '__JWT__', jwtPolicy), '__SET_KEY__', setBackendKey)
var serviceAuthDev = replace(loadTextContent('../apim/policies/auth/service-dev.xml'), '__SET_KEY__', setBackendKey)
var serviceAuth = authMode == 'entra' ? serviceAuthEntra : (authMode == 'dev' ? serviceAuthDev : setBackendKey)
var consoleAuthEntra = replace(loadTextContent('../apim/policies/auth/console-entra.xml'), '__JWT__', jwtPolicy)
var consoleAuth = authMode == 'entra' ? consoleAuthEntra : (authMode == 'apikey' ? setBackendKey : '')

var globalPolicy = replace(replace(replace(loadTextContent('../apim/policies/global.xml'), '__CORS_ORIGIN__', consoleOrigin), '__RATE_LIMIT__', rateLimit), '__AUTH_STRIP__', authStrip)

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
    displayName: 'Data Mart Service Integrations'
    description: 'Trusted server-to-server applications (D365 integrations, Databricks, partner platforms, smoke tests). Without a user token the call acts as the service principal.'
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
    value: replace(loadTextContent('../apim/policies/product-sales-order.xml'), '__SERVICE_AUTH__', serviceAuth)
  }
  dependsOn: [ nvApiKey ]
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
    value: replace(loadTextContent('../apim/policies/product-order-status.xml'), '__SERVICE_AUTH__', serviceAuth)
  }
  dependsOn: [ nvApiKey ]
}

resource prodStatusApi 'Microsoft.ApiManagement/service/products/apis@2022-08-01' = {
  parent: prodStatus
  name: 'tfso-process'
  dependsOn: [ apiRes[1] ]
}

// The browser console: user identity only (Entra token or, in non-prod, a dev persona). Never service rights.
resource prodConsole 'Microsoft.ApiManagement/service/products@2022-08-01' = {
  parent: apim
  name: 'data-mart-console'
  properties: {
    displayName: 'Data Mart Console'
    description: 'Taylor Farms Data Mart web console. Every call carries the signed-in user identity; access is enforced per role, domain and row scope.'
    subscriptionRequired: true
    approvalRequired: false
    subscriptionsLimit: 5
    state: 'published'
  }
}

resource prodConsolePolicy 'Microsoft.ApiManagement/service/products/policies@2022-08-01' = {
  parent: prodConsole
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(loadTextContent('../apim/policies/product-console.xml'), '__CONSOLE_AUTH__', consoleAuth)
  }
  dependsOn: [ nvApiKey ]
}

resource prodConsoleApis 'Microsoft.ApiManagement/service/products/apis@2022-08-01' = [for (a, i) in apis: {
  parent: prodConsole
  name: a.name
  dependsOn: [ apiRes[i] ]
}]

@description('Subscription used by the Data Mart console (key fetched by deploy.sh via listSecrets; public in config.js)')
resource consoleSub 'Microsoft.ApiManagement/service/subscriptions@2022-08-01' = {
  parent: apim
  name: 'order-console'
  properties: {
    displayName: 'Data Mart Console'
    scope: prodConsole.id
    state: 'active'
    allowTracing: false
  }
  dependsOn: [ prodConsoleApis ]
}

@description('Service subscription for trusted automation (smoke test, demo data). Keep the key secret.')
resource serviceSub 'Microsoft.ApiManagement/service/subscriptions@2022-08-01' = {
  parent: apim
  name: 'platform-service'
  properties: {
    displayName: 'Platform service (deployment automation)'
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
output serviceSubscriptionName string = serviceSub.name
