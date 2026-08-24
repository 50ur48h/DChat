// The whole deployment for one environment, one module per service.
//
// **Every resource below is in architecture 9.1's justification table.** That
// table is a deliberate list with a column for what was left out — APIM, Service
// Bus, AI Search, Functions — and adding a module for any of them belongs in a
// PR that amends the table first. Reviewing this file against that table is
// WP12.1's own acceptance criterion.
//
// Ordering is by dependency and almost entirely implicit: Bicep infers it from
// the references between modules, and referencing one output depends on the
// whole module. Exactly one place states it outright, because there the two
// modules share no reference at all — the apps must wait for the role
// assignments, or the identity they run as has no permission on its first boot.
//
// Deployed at resource-group scope. The group itself is created by the pipeline,
// not here: a template that made its own group would have to be deployed at
// subscription scope, which is a wider blast radius than anything in this file
// needs.

targetScope = 'resourceGroup'

@description('dev or prod. Names, sizes and replica counts all derive from it.')
@allowed(['dev', 'prod'])
param env string

@description('Azure region. Everything lands in one.')
param location string = resourceGroup().location

@description('Postgres compute SKU.')
param postgresSkuName string

@description('The tier postgresSkuName belongs to.')
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param postgresSkuTier string

@description('Postgres data disk in GB.')
@minValue(32)
param postgresStorageGb int

@description('Days of point-in-time restore.')
@minValue(7)
@maxValue(35)
param postgresBackupRetentionDays int

@description('Postgres administrator login. Not the role the application connects as.')
param postgresAdminLogin string

@description('Administrator password, supplied at deploy time from a secret and never written to a tracked file.')
@secure()
param postgresAdminPassword string

@description('The platform database name.')
param databaseName string = 'dataagent'

@description('Log Analytics daily ingestion cap in GB.')
@minValue(1)
param logsDailyCapGb int

@description('How long a stored query result lives, enforced by blob lifecycle rather than by application code.')
@minValue(1)
param artifactRetentionDays int

@description('Container image tag. A git sha once WP12.2 pushes one.')
param imageTag string

@description('Minimum replicas. Zero gives scale-to-zero, which is why Container Apps was chosen.')
@minValue(0)
param minReplicas int

@description('Maximum replicas, so a runaway cannot scale into a bill.')
@minValue(1)
param maxReplicas int

@description('Monthly spend ceiling in USD.')
@minValue(1)
param monthlyBudgetUsd int

@description('Where budget alerts go. No default: supplied at deploy time from a GitHub secret, never from a tracked file.')
param budgetAlertEmail string

@description('Budget period start, YYYY-MM-01. Azure refuses a start date in the past.')
param budgetStartDate string

@description('Where the identity provider publishes its discovery document. Supplied at deploy time: it names the owner tenant and this repository is public.')
param oidcAuthority string

@description('The audience every accepted token must carry.')
param oidcAudience string

@description('Deploy the two container apps. The deploy pipeline sets this false on its first pass, before the vault is seeded and before the migration has run.')
param deployApps bool = true

@description('Deploy the migration job. Separate from deployApps so the job can be created and run before any app revision is rolled.')
param deployJobs bool = true

// Derived, not passed. A globally-unique suffix nobody types is a suffix nobody
// typos, and it is stable for the life of the resource group — so a redeploy
// finds the same registry and the same vault rather than making new ones.
var suffix = take(uniqueString(resourceGroup().id), 6)

var tags = {
  application: 'dataagent'
  environment: env
  managedBy: 'bicep'
}

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    env: env
    tags: tags
  }
}

module logs 'modules/logs.bicep' = {
  name: 'logs'
  params: {
    location: location
    env: env
    tags: tags
    dailyCapGb: logsDailyCapGb
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    env: env
    tags: tags
  }
}

module keyvault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    location: location
    env: env
    tags: tags
    suffix: suffix
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    env: env
    tags: tags
    suffix: suffix
    artifactRetentionDays: artifactRetentionDays
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  params: {
    location: location
    env: env
    tags: tags
    suffix: suffix
  }
}

module postgres 'modules/postgres.bicep' = {
  name: 'postgres'
  params: {
    location: location
    env: env
    tags: tags
    skuName: postgresSkuName
    skuTier: postgresSkuTier
    storageGb: postgresStorageGb
    backupRetentionDays: postgresBackupRetentionDays
    administratorLogin: postgresAdminLogin
    administratorPassword: postgresAdminPassword
    subnetId: network.outputs.postgresSubnetId
    privateDnsZoneId: network.outputs.privateDnsZoneId
    databaseName: databaseName
  }
  // No `dependsOn`. The first draft had one, on the reasoning that the private
  // DNS *link* is a separate resource nothing here references — but referencing
  // any output of a module depends on the whole module, link included, and the
  // linter said so. Worth recording rather than quietly deleting: the ordering
  // is real, and it is already guaranteed.
}

module roles 'modules/roles.bicep' = {
  name: 'roles'
  params: {
    principalId: identity.outputs.principalId
    keyVaultName: keyvault.outputs.name
    storageAccountName: storage.outputs.name
    registryName: acr.outputs.name
  }
}

module apps 'modules/apps.bicep' = {
  name: 'apps'
  params: {
    location: location
    env: env
    tags: tags
    infrastructureSubnetId: network.outputs.appsSubnetId
    logAnalyticsWorkspaceId: logs.outputs.workspaceId
    insightsConnectionString: logs.outputs.insightsConnectionString
    oidcAuthority: oidcAuthority
    oidcAudience: oidcAudience
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    registryLoginServer: acr.outputs.loginServer
    keyVaultUri: keyvault.outputs.uri
    imageTag: imageTag
    postgresHost: postgres.outputs.fullyQualifiedDomainName
    postgresDatabase: postgres.outputs.databaseName
    postgresAdminLogin: postgresAdminLogin
    blobEndpoint: storage.outputs.blobEndpoint
    minReplicas: minReplicas
    maxReplicas: maxReplicas
    deployApps: deployApps
    deployJobs: deployJobs
  }
  // **The one ordering that has to be written down.** The apps read their
  // secrets from Key Vault at start-up using the managed identity. If the role
  // assignments have not landed, the first revision fails to start with a
  // permissions error against a secret it is entitled to — an intermittent
  // failure that looks like a bad secret and is a race.
  dependsOn: [roles]
}

module budget 'modules/budget.bicep' = {
  name: 'budget'
  params: {
    env: env
    monthlyLimitUsd: monthlyBudgetUsd
    alertEmail: budgetAlertEmail
    startDate: budgetStartDate
  }
}

@description('The public address of the web app.')
output webUrl string = deployApps ? 'https://${apps.outputs.webFqdn}' : ''

@description('The api, reachable only inside the environment.')
output apiInternalFqdn string = deployApps ? apps.outputs.apiFqdn : ''

@description('The API the browser calls. Needed at web *build* time, because NEXT_PUBLIC_* is inlined into the bundle.')
output apiUrl string = 'https://ca-dataagent-api-${env}.${apps.outputs.defaultDomain}'

@description('Where images are pushed and pulled.')
output registryLoginServer string = acr.outputs.loginServer

@description('The vault whose secrets must exist before the first deploy.')
output keyVaultName string = keyvault.outputs.name

@description('The secret names the apps expect to find there.')
output expectedSecretNames object = apps.outputs.secretNames

@description('Postgres hostname, for the migration job.')
output postgresHost string = postgres.outputs.fullyQualifiedDomainName
