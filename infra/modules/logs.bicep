// Log Analytics and Application Insights, capped on purpose.
//
// Architecture 9.1 lists this as "App Insights/Log Analytics (capped)", and the
// parenthesis is the point: telemetry is the one service here that bills by how
// much goes wrong. An uncapped workspace turns a bad afternoon into a bill, and
// 8.1's log policy — sql_hash only, no prompt bodies, sampling on — is the other
// half of the same argument.

@description('Where everything goes.')
param location string

@description('Environment discriminator — dev or prod.')
param env string

@description('Tags applied to every resource.')
param tags object

@description('Daily ingestion ceiling in GB. Past this the workspace stops taking data until the next day.')
@minValue(1)
param dailyCapGb int

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-dataagent-${env}'
  location: location
  tags: tags
  properties: {
    sku: {
      // Pay-as-you-go with a cap, rather than a commitment tier: this is the
      // cheaper shape at small volume, and the cap is what makes it bounded.
      name: 'PerGB2018'
    }
    retentionInDays: 30
    workspaceCapping: {
      dailyQuotaGb: dailyCapGb
    }
    features: {
      // Local auth off, so telemetry is written with the managed identity like
      // everything else. A workspace key would be a second credential to store,
      // which is exactly what Key Vault and managed identity exist to avoid.
      disableLocalAuth: true
    }
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-dataagent-${env}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    // Workspace-based, which is the only mode still offered — classic App
    // Insights is retired, and its resources cannot be created.
    WorkspaceResourceId: workspace.id
    IngestionMode: 'LogAnalytics'
    DisableLocalAuth: true
  }
}

output workspaceId string = workspace.id
output workspaceCustomerId string = workspace.properties.customerId
output insightsId string = insights.id
output insightsConnectionString string = insights.properties.ConnectionString
