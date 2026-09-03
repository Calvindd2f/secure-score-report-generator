// ---------------------------------------------------------------------------
// Azure Container App + Container App Environment
// ---------------------------------------------------------------------------

@description('Base name used for resource naming')
param appName string

@description('Azure region for all resources')
param location string

@description('Full container image reference (e.g. myacr.azurecr.io/func-secure-score:latest)')
param containerImage string

@description('ACR resource ID — used to assign AcrPull role to the managed identity')
param acrId string

@description('CPU cores allocated to the container (e.g. 0.5)')
param cpuCores string = '0.5'

@description('Memory in Gi allocated to the container (e.g. 1.0)')
param memoryGi string = '1.0'

@description('Minimum number of replicas (0 enables scale-to-zero)')
@minValue(0)
param minReplicas int = 0

@description('Maximum number of replicas')
@minValue(1)
param maxReplicas int = 3

@description('Tags to apply to all resources')
param tags object = {}

// ---------------------------------------------------------------------------
// Log Analytics Workspace (required by Container App Environment)
// ---------------------------------------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${appName}-logs'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------
// Container App Environment
// ---------------------------------------------------------------------------

resource containerAppEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${appName}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

// ---------------------------------------------------------------------------
// User-Assigned Managed Identity (for keyless ACR pull)
// ---------------------------------------------------------------------------

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${appName}-identity'
  location: location
  tags: tags
}

// AcrPull built-in role: 7f951dda-4ed3-4680-a7ca-43fe172d538d
var acrPullRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '7f951dda-4ed3-4680-a7ca-43fe172d538d'
)

resource acrPullAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acrId, identity.id, acrPullRoleId)
  scope: resourceGroup()
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: acrPullRoleId
  }
}

// ---------------------------------------------------------------------------
// Container App
// ---------------------------------------------------------------------------

resource containerApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: appName
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: split(containerImage, '/')[0]   // e.g. myacr.azurecr.io
          identity: identity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: appName
          image: containerImage
          resources: {
            cpu: json(cpuCores)
            memory: '${memoryGi}Gi'
          }
          env: [
            {
              name: 'AzureWebJobsScriptRoot'
              value: '/home/site/wwwroot'
            }
            {
              name: 'AzureFunctionsJobHost__Logging__Console__IsEnabled'
              value: 'true'
            }
            {
              name: 'PLAYWRIGHT_BROWSERS_PATH'
              value: '/ms-playwright'
            }
            {
              name: 'FUNCTIONS_WORKER_RUNTIME'
              value: 'python'
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-scaling'
            http: {
              metadata: {
                concurrentRequests: '10'
              }
            }
          }
        ]
      }
    }
  }
  dependsOn: [
    acrPullAssignment
  ]
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('FQDN of the Container App')
output fqdn string = containerApp.properties.configuration.ingress.fqdn

@description('Container App resource ID')
output containerAppId string = containerApp.id

@description('Managed Identity principal ID')
output identityPrincipalId string = identity.properties.principalId
