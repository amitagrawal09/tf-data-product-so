// Deployer managed identity for the one-click deployment, with Owner on this resource group only.
// (The platform template assigns roles to the workload identity, which needs roleAssignments/write.)
targetScope = 'resourceGroup'

param prefix string
param environment string
param location string
param tags object

var ownerRoleId = '8e3af657-a8ff-443c-a75c-2fe8c4bcb635'

resource deployerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}-${environment}-deployer'
  location: location
  tags: tags
}

resource deployerOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployerIdentity.id, ownerRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', ownerRoleId)
    principalId: deployerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    description: 'One-click deployer for the Sales Order data product (resource-group scope)'
  }
}

output id string = deployerIdentity.id
output principalId string = deployerIdentity.properties.principalId
output ownerAssignmentId string = deployerOwner.id
