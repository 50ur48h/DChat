// The platform database: Flexible Server, B-series, pgvector, private.
//
// Architecture 9.1's table justifies it in one line — *platform DB + pgvector +
// RLS in one* — and names the cheaper alternative it rejects: a container-hosted
// Postgres, refused over backups and patching. That rejection is what makes the
// two settings below load-bearing rather than decorative: if backup retention
// and a maintenance window are not configured, this is a container-hosted
// Postgres with extra steps.
//
// **Private access, not a firewall rule.** No public endpoint exists, so there
// is no allow-list to get wrong. The cost is `network.bicep`; the benefit is
// that "who can reach the platform database" has a structural answer.

@description('Where everything goes.')
param location string

@description('Environment discriminator — dev or prod.')
param env string

@description('Tags applied to every resource.')
param tags object

@description('Compute tier. B-series for dev per the owner, and the parameter is what lets prod differ without a second template.')
param skuName string

@description('Burstable, GeneralPurpose or MemoryOptimized — must match the family skuName belongs to.')
@allowed(['Burstable', 'GeneralPurpose', 'MemoryOptimized'])
param skuTier string

@description('Data disk size. 32 GB is the smallest Flexible Server offers.')
@minValue(32)
param storageGb int

@description('Days of point-in-time restore. WP12.4 drills a restore, so this has to be long enough to drill against.')
@minValue(7)
@maxValue(35)
param backupRetentionDays int

@description('The administrator login. Not a secret by itself, and never the identity the application connects as.')
param administratorLogin string

@description('The administrator password, supplied at deploy time and never stored in a tracked file.')
@secure()
param administratorPassword string

@description('The delegated subnet from network.bicep.')
param subnetId string

@description('The private DNS zone the server registers its hostname in.')
param privateDnsZoneId string

@description('The database the application uses.')
param databaseName string

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: 'psql-dataagent-${env}'
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  properties: {
    version: '16'
    administratorLogin: administratorLogin
    administratorLoginPassword: administratorPassword
    storage: {
      storageSizeGB: storageGb
      // Growing the disk by hand is a 3am job. This is the setting that makes
      // it not one, and it cannot be turned on later without a restart.
      autoGrow: 'Enabled'
    }
    backup: {
      backupRetentionDays: backupRetentionDays
      // Local rather than geo-redundant: a dev environment, and the restore
      // drill WP12.4 runs is a same-region restore. Geo-redundancy is a prod
      // parameter, which is why it is here rather than assumed.
      geoRedundantBackup: 'Disabled'
    }
    network: {
      delegatedSubnetResourceId: subnetId
      privateDnsZoneArmResourceId: privateDnsZoneId
      // Redundant with the delegated subnet — Azure refuses both — but stated
      // so that reading this file answers "is it public" without having to know
      // that rule.
      publicNetworkAccess: 'Disabled'
    }
    highAvailability: {
      // Off for a B-series dev server: the tier does not support it, and
      // pretending otherwise fails at deploy with a message about zones.
      mode: 'Disabled'
    }
    maintenanceWindow: {
      customWindow: 'Enabled'
      // Sunday 18:00 UTC — small hours in southeastasia, which is where this
      // runs and whose working day is what matters.
      dayOfWeek: 0
      startHour: 18
      startMinute: 0
    }
    authConfig: {
      // Both, deliberately. Entra authentication is how a person or a pipeline
      // reaches the server; password authentication is how `dataagent_app`
      // connects, because the application role is a Postgres role with a
      // password in Key Vault and not an Azure principal. Turning password auth
      // off would break the very separation CLAUDE.md's hard rule protects —
      // the API connects as an unprivileged Postgres role that owns nothing.
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Enabled'
      tenantId: subscription().tenantId
    }
  }
}

// **An extension is not installed by creating it.** Flexible Server refuses
// `CREATE EXTENSION` for anything not on this server-level allow-list, and the
// refusal reads like a permissions problem rather than a configuration one:
// `FeatureNotSupportedError: extension "pgcrypto" is not allow-listed for users
// in Azure Database for PostgreSQL`.
//
// **This list was wrong in both directions and nothing could see it.** It read
// `VECTOR,UUID-OSSP,PG_TRGM`. The migrations create exactly two extensions —
// `pgcrypto` (revision 0001, behind every `gen_random_uuid()` default) and
// `vector` (0001, 0016, 0018) — so two entries named extensions nobody creates,
// and the one that revision 0001 needs on its *first statement* was missing. The
// first real migration against Azure died there.
//
// The value is now exactly what `grep -r 'CREATE EXTENSION' apps/api/src/dataagent/db`
// reports, and adding an extension to a migration means adding it here in the
// same PR. This is a schema-correspondence gap of the kind **B-110** is about:
// two lists that must agree, in different languages, in different directories,
// with nothing comparing them.
//
// Changing `azure.extensions` is a **static** server parameter, so Azure restarts
// the server when this value changes. Seconds on an idle B1ms; worth knowing
// before a deploy that looks like it has stalled.
resource extensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: server
  name: 'azure.extensions'
  properties: {
    value: 'PGCRYPTO,VECTOR'
    source: 'user-override'
  }
}

// Connections are TLS-only. Off by default on some tiers, and B-013's rule for
// customer databases — anything not on this machine uses TLS — applies at least
// as strongly to the platform's own.
resource requireSsl 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2024-08-01' = {
  parent: server
  name: 'require_secure_transport'
  properties: {
    value: 'on'
    source: 'user-override'
  }
  dependsOn: [extensions]
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

output id string = server.id
output name string = server.name
output fullyQualifiedDomainName string = server.properties.fullyQualifiedDomainName
output databaseName string = database.name
