using 'main.bicep'

// ---------------------------------------------------------------------------
// Default parameter values for func-secure-score deployment
//
// Override any values as needed:
//   az deployment group create \
//     --resource-group rg-secure-score \
//     --template-file infra/main.bicep \
//     --parameters infra/main.bicepparam \
//     --parameters location=westeurope
// ---------------------------------------------------------------------------

param location = 'northeurope'
param appName = 'func-secure-score'
param acrSku = 'Basic'
param containerImage = ''          // Defaults to <acrName>.azurecr.io/func-secure-score:latest
param cpuCores = '0.5'
param memoryGi = '1.0'
param minReplicas = 0
param maxReplicas = 3
