// ---------------------------------------------------------------------------
// Azure Container Registry (ACR)
// ---------------------------------------------------------------------------

@description('Name of the Azure Container Registry (must be globally unique, alphanumeric only)')
param acrName string

@description('Azure region for the ACR resource')
param location string

@description('SKU for the Container Registry')
@allowed(['Basic', 'Standard', 'Premium'])
param sku string = 'Basic'

@description('Tags to apply to the ACR resource')
param tags object = {}

// ---------------------------------------------------------------------------
// Container Registry
// ---------------------------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  tags: tags
  sku: {
    name: sku
  }
  properties: {
    adminUserEnabled: false          // Use managed identity for pulls
    publicNetworkAccess: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('Resource ID of the ACR')
output acrId string = acr.id

@description('Login server URL (e.g. myacr.azurecr.io)')
output acrLoginServer string = acr.properties.loginServer

@description('ACR resource name')
output acrName string = acr.name
