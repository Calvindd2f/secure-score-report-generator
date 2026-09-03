// ---------------------------------------------------------------------------
// main.bicep — Azure Container Registry + Container App deployment
//
// Deploys all infrastructure for func-secure-score:
//   1. Azure Container Registry (ACR)
//   2. Container App Environment + Container App
//   3. User-Assigned Managed Identity with AcrPull role
//
// Usage:
//   az deployment group create \
//     --resource-group <rg-name> \
//     --template-file infra/main.bicep \
//     --parameters infra/main.bicepparam
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

// ── Parameters ──────────────────────────────────────────────────────────────

@description('Azure region for all resources')
param location string = 'northeurope'

@description('Base name used for resource naming (letters, numbers, hyphens)')
@minLength(3)
@maxLength(24)
param appName string = 'func-secure-score'

@description('ACR SKU tier')
@allowed(['Basic', 'Standard', 'Premium'])
param acrSku string = 'Basic'

@description('Full container image reference. Leave empty to default to <acrName>.azurecr.io/<appName>:latest')
param containerImage string = ''

@description('CPU cores for the Container App')
param cpuCores string = '0.5'

@description('Memory (Gi) for the Container App')
param memoryGi string = '1.0'

@description('Minimum replicas (0 = scale to zero)')
@minValue(0)
param minReplicas int = 0

@description('Maximum replicas')
@minValue(1)
param maxReplicas int = 3

// ── Computed values ─────────────────────────────────────────────────────────

// ACR names must be alphanumeric only, 5-50 chars
var acrName = replace(appName, '-', '')

var tags = {
  project: appName
  managedBy: 'bicep'
}

var resolvedImage = !empty(containerImage)
  ? containerImage
  : '${acrName}.azurecr.io/${appName}:latest'

// ── Modules ─────────────────────────────────────────────────────────────────

module acr 'modules/acr.bicep' = {
  name: 'deploy-acr'
  params: {
    acrName: acrName
    location: location
    sku: acrSku
    tags: tags
  }
}

module containerApp 'modules/containerapp.bicep' = {
  name: 'deploy-container-app'
  params: {
    appName: appName
    location: location
    containerImage: resolvedImage
    acrId: acr.outputs.acrId
    cpuCores: cpuCores
    memoryGi: memoryGi
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    tags: tags
  }
}

// ── Outputs ─────────────────────────────────────────────────────────────────

@description('ACR login server URL')
output acrLoginServer string = acr.outputs.acrLoginServer

@description('Container App FQDN')
output containerAppFqdn string = containerApp.outputs.fqdn

@description('Full URL to the running application')
output applicationUrl string = 'https://${containerApp.outputs.fqdn}'
