param location string
param name string
param tags object
param administratorLogin string
@secure()
param administratorLoginPassword string
@allowed([ 'dev', 'test', 'prod' ])
param environment string
param databases array = [ 'tfso_process', 'tfso_system' ]
param version string = '16'

var isProd = environment == 'prod'

resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: name
  location: location
  tags: tags
  sku: isProd ? { name: 'Standard_D2ds_v5', tier: 'GeneralPurpose' } : { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: version
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorLoginPassword
    storage: { storageSizeGB: isProd ? 128 : 32, autoGrow: 'Enabled' }
    backup: {
      backupRetentionDays: isProd ? 35 : 7
      geoRedundantBackup: isProd ? 'Enabled' : 'Disabled'
    }
    highAvailability: { mode: isProd ? 'ZoneRedundant' : 'Disabled' }
    authConfig: { activeDirectoryAuth: 'Disabled', passwordAuth: 'Enabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

@description('0.0.0.0 - 0.0.0.0 = allow Azure services (Container Apps consumption egress). Use VNet integration + private endpoint for prod hardening.')
resource fwAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'AllowAllAzureServicesAndResourcesWithinAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource dbs 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = [for d in databases: {
  parent: pg
  name: d
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
  dependsOn: [ fwAzure ]
}]

output id string = pg.id
output name string = pg.name
output fqdn string = pg.properties.fullyQualifiedDomainName
