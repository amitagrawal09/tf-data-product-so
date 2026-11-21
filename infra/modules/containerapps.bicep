// Container Apps environment + the three Tier-2 API apps (Experience, Process, System).
param location string
param stem string
param tags object
@allowed([ 'dev', 'test', 'prod' ])
param environment string
param logAnalyticsName string
param uamiId string
param uamiClientId string
param acrLoginServer string
@description('Full image reference, e.g. myacr.azurecr.io/tfso-sales-order:abc123')
param image string
@description('true on the very first deployment, before the image exists in ACR')
param usePlaceholderImage bool
param keyVaultUri string
param serviceBusFqdn string
param serviceBusKedaRuleId string
param eventHubsFqdn string
param eventHubName string
param lakeDfsUrl string
param docIntelEndpoint string
param d365Url string
param d365TenantId string
param d365ClientId string
param d365Company string
param isaSenderId string
@description('Backend authentication mode (contract docs/07 §1): apikey | dev | entra')
@allowed([ 'apikey', 'dev', 'entra' ])
param authMode string
@description('Entra ID workforce tenant id (entra mode)')
param entraTenantId string = ''
@description('Accepted token audience(s): API client id and/or api://<client-id>, comma-separated')
param entraAudience string = ''
@description('Optional Entra External ID issuer for customers and vendors')
param entraExternalIssuer string = ''

var isProd = environment == 'prod'
var placeholderImage = 'mcr.microsoft.com/k8se/quickstart:latest'
var targetPort = usePlaceholderImage ? 80 : 8000

var names = {
  experience: 'ca-${stem}-exp'
  process: 'ca-${stem}-process'
  system: 'ca-${stem}-system'
}

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsName
}

resource cae 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${stem}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
  }
}

// Key Vault references resolved by the platform with the user-assigned identity
var kvSecretRefs = [
  { name: 'api-key', keyVaultUrl: '${keyVaultUri}secrets/tfso-api-key', identity: uamiId }
  { name: 'appi-conn', keyVaultUrl: '${keyVaultUri}secrets/appinsights-connection-string', identity: uamiId }
  { name: 'd365-client-secret', keyVaultUrl: '${keyVaultUri}secrets/d365-client-secret', identity: uamiId }
  { name: 'sb-keda', value: listKeys(serviceBusKedaRuleId, '2022-10-01-preview').primaryConnectionString }
]
var processSecrets = concat(kvSecretRefs, [
  { name: 'db-url', keyVaultUrl: '${keyVaultUri}secrets/tfso-process-db-url', identity: uamiId }
])
var systemSecrets = concat(kvSecretRefs, [
  { name: 'db-url', keyVaultUrl: '${keyVaultUri}secrets/tfso-system-db-url', identity: uamiId }
])

// Blueprint section 3 environment variables
var commonEnv = [
  { name: 'TFSO_ENV', value: environment }
  { name: 'PORT', value: '8000' }
  { name: 'AZURE_CLIENT_ID', value: uamiClientId }
  { name: 'TFSO_BUS_MODE', value: 'servicebus' }
  { name: 'TFSO_SERVICEBUS_FQDN', value: serviceBusFqdn }
  { name: 'TFSO_EVENTHUB_FQDN', value: eventHubsFqdn }
  { name: 'TFSO_EVENTHUB_NAME', value: eventHubName }
  { name: 'TFSO_PROCESS_URL', value: 'http://${names.process}' }
  { name: 'TFSO_SYSTEM_URL', value: 'http://${names.system}' }
  { name: 'TFSO_EXPERIENCE_URL', value: 'http://${names.experience}' }
  { name: 'TFSO_AUTH_MODE', value: authMode }
  { name: 'TFSO_API_KEYS', secretRef: 'api-key' }
  { name: 'TFSO_ENTRA_TENANT_ID', value: entraTenantId }
  { name: 'TFSO_ENTRA_AUDIENCE', value: entraAudience }
  { name: 'TFSO_ENTRA_EXTERNAL_ISSUER', value: entraExternalIssuer }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appi-conn' }
  { name: 'TFSO_D365_MODE', value: empty(d365Url) ? 'simulator' : 'live' }
  { name: 'TFSO_D365_URL', value: d365Url }
  { name: 'TFSO_D365_TENANT_ID', value: d365TenantId }
  { name: 'TFSO_D365_CLIENT_ID', value: d365ClientId }
  { name: 'TFSO_D365_CLIENT_SECRET', secretRef: 'd365-client-secret' }
  { name: 'TFSO_D365_COMPANY', value: d365Company }
  { name: 'TFSO_SIM_AUTOPROGRESS', value: 'false' }
  { name: 'TFSO_SIM_STEP_SECONDS', value: '5' }
  { name: 'TFSO_LAKEHOUSE_MODE', value: 'adls' }
  { name: 'TFSO_LAKEHOUSE_ACCOUNT_URL', value: lakeDfsUrl }
  { name: 'TFSO_LAKEHOUSE_CONTAINER', value: 'bronze' }
  { name: 'TFSO_DOCINTEL_ENDPOINT', value: docIntelEndpoint }
  { name: 'TFSO_DOCINTEL_MODEL_ID', value: 'prebuilt-layout' }
  { name: 'TFSO_ISA_SENDER_ID', value: isaSenderId }
]
var dbEnv = [
  { name: 'TFSO_DB_URL', secretRef: 'db-url' }
]

var probes = usePlaceholderImage ? [] : [
  {
    type: 'Liveness'
    httpGet: { path: '/health', port: 8000 }
    initialDelaySeconds: 10
    periodSeconds: 30
    failureThreshold: 3
  }
  {
    type: 'Readiness'
    httpGet: { path: '/health', port: 8000 }
    initialDelaySeconds: 5
    periodSeconds: 10
    failureThreshold: 3
  }
]

var apps = [
  {
    key: 'experience'
    name: names.experience
    service: 'experience'
    needsDb: false
    minReplicas: isProd ? 2 : 1
    maxReplicas: isProd ? 10 : 3
    topic: ''
    subscription: ''
  }
  {
    key: 'process'
    name: names.process
    service: 'process'
    needsDb: true
    minReplicas: isProd ? 2 : 1
    maxReplicas: isProd ? 10 : 3
    topic: 'system-status-events'
    subscription: 'process-api'
  }
  {
    key: 'system'
    name: names.system
    service: 'system'
    needsDb: true
    minReplicas: isProd ? 2 : 1
    maxReplicas: isProd ? 10 : 3
    topic: 'sales-order-commands'
    subscription: 'system-api'
  }
]

resource containerApps 'Microsoft.App/containerApps@2024-03-01' = [for a in apps: {
  name: a.name
  location: location
  tags: union(tags, { 'tfso-service': a.service })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${uamiId}': {} }
  }
  properties: {
    managedEnvironmentId: cae.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      // Ingress is external so APIM (outside the environment) can reach it. Direct calls that bypass
      // APIM still need an identity the services accept: an Entra access token (entra mode), the backend
      // x-api-key held only by APIM/Key Vault (service traffic), or - in non-prod dev mode only - a
      // persona header. Harden further with VNet + internal ingress for prod (docs/04, docs/08).
      ingress: {
        external: true
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: true // allows in-environment http://<app> service-to-service calls
        traffic: [ { latestRevision: true, weight: 100 } ]
      }
      registries: [
        { server: acrLoginServer, identity: uamiId }
      ]
      secrets: a.key == 'process' ? processSecrets : (a.key == 'system' ? systemSecrets : kvSecretRefs)
    }
    template: {
      containers: [
        {
          name: a.service
          image: usePlaceholderImage ? placeholderImage : image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(commonEnv, a.needsDb ? dbEnv : [], [ { name: 'SERVICE', value: a.service } ])
          probes: probes
        }
      ]
      scale: {
        minReplicas: a.minReplicas
        maxReplicas: a.maxReplicas
        rules: concat([
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ], empty(a.topic) ? [] : [
          {
            name: 'servicebus-${a.subscription}'
            custom: {
              type: 'azure-servicebus'
              metadata: {
                topicName: a.topic
                subscriptionName: a.subscription
                messageCount: '20'
              }
              auth: [ { secretRef: 'sb-keda', triggerParameter: 'connection' } ]
            }
          }
        ])
      }
    }
  }
}]

output environmentId string = cae.id
output defaultDomain string = cae.properties.defaultDomain
output experienceName string = containerApps[0].name
output processName string = containerApps[1].name
output systemName string = containerApps[2].name
output experienceFqdn string = containerApps[0].properties.configuration.ingress.fqdn
output processFqdn string = containerApps[1].properties.configuration.ingress.fqdn
output systemFqdn string = containerApps[2].properties.configuration.ingress.fqdn
