// The Container Apps environment and the two deployables.
//
// Architecture 9.1 picks Container Apps for scale-to-zero, revisions and
// rollback, and no Kubernetes to operate — and 9.3 leans on revisions as the
// rollback story, which is why nothing here pins a revision name.
//
// **Two properties are load-bearing and easy to lose in a diff.**
//
// *Secrets arrive by reference, never by value.* Each secret names a Key Vault
// URI and the identity to read it with; the value is fetched at start-up and
// never appears in this template, in a parameter file, or in the deployment
// history. A secret written as a literal would be visible to anyone with reader
// access on the resource group, and would outlive its own rotation.
//
// *The web app is public and the api is not.* Only `web` takes external ingress.
// The api is reachable inside the environment, so the browser talks to the web
// app and the web app talks to the api — which is what keeps the API's surface
// off the public internet without a second network control.

@description('Where everything goes.')
param location string

@description('Environment discriminator — dev or prod.')
param env string

@description('Tags applied to every resource.')
param tags object

@description('The subnet from network.bicep that this environment is injected into.')
param infrastructureSubnetId string

@description('Log Analytics workspace GUID — the environment wants the customer id, not the ARM id.')
param logAnalyticsCustomerId string

@description('Application Insights connection string, for OTel.')
param insightsConnectionString string

@description('Resource id of the user-assigned identity everything runs as.')
param identityId string

@description('Client id of that identity, which the app needs to pick it up from DefaultAzureCredential.')
param identityClientId string

@description('Registry hostname, e.g. crdataagentdev0000.azurecr.io.')
param registryLoginServer string

@description('Key Vault URI, for secret references.')
param keyVaultUri string

@description('Image tag to deploy. A git sha in the pipeline; a placeholder until WP12.2 pushes one.')
param imageTag string

@description('Postgres hostname, from postgres.bicep.')
param postgresHost string

@description('The platform database name.')
param postgresDatabase string

@description('The owner/migration login. Only the migration job is given it; the API connects as dataagent_app and owns nothing.')
param postgresAdminLogin string

@description('Blob endpoint, for the artifact store.')
param blobEndpoint string

@description('Minimum replicas. Zero is the point of Container Apps for a dev environment; prod may want one to avoid a cold start.')
@minValue(0)
param minReplicas int

@description('Maximum replicas, so a runaway cannot scale into a bill.')
@minValue(1)
param maxReplicas int

@description('Deploy the two container apps. False for the first pass of a deploy, before the vault has been seeded and before the migration has run — an app revision cannot start without its secrets, and must not start before its schema.')
param deployApps bool = true

@description('Deploy the migration job. Separated from deployApps so a deploy can create the job, run it, and only then roll the apps — which is the ordering the plan requires and the one a single flag cannot express.')
param deployJobs bool = true

// **Named here rather than inline**, so the set of secrets the platform expects
// is one readable list. Each is a Key Vault secret name; the vault is created
// empty and these are written out of band before the first deploy.
// **`local-secrets-key` is deliberately absent** (B-120's third finding). This
// template sets `SECRETS_BACKEND=keyvault` and `config.py` refuses the local
// backend in a production build, so the Fernet key is unreadable by construction
// — and a Key Vault secret that must exist for nothing to read is a secret to
// rotate, audit and explain forever. Three secrets, each of which something uses.
var secretNames = {
  openAiApiKey: 'openai-api-key'
  appDatabasePassword: 'app-database-password'
  databasePassword: 'database-password'
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-dataagent-${env}'
  location: location
  tags: tags
  properties: {
    vnetConfiguration: {
      infrastructureSubnetId: infrastructureSubnetId
      // The environment is not internal: `web` needs a public hostname. The api
      // is kept private by its own ingress setting below rather than by making
      // the whole environment internal, which would also hide the web app.
      internal: false
    }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalyticsCustomerId
        // No shared key: the environment writes with its own identity. A key
        // here would be the one credential this template could not avoid, and
        // it can.
      }
    }
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

// Shared by both apps: the registry they pull from, the identity they use, and
// the observability wiring. Written once so the two cannot drift.
var registryConfig = [
  {
    server: registryLoginServer
    identity: identityId
  }
]

var commonEnv = [
  {
    name: 'ENV'
    value: env
  }
  {
    name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
    value: insightsConnectionString
  }
  {
    // What `DefaultAzureCredential` needs in order to pick the user-assigned
    // identity rather than guessing among several.
    name: 'AZURE_CLIENT_ID'
    value: identityClientId
  }
]

resource api 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: 'ca-dataagent-api-${env}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      // **Internal.** Reachable from inside the environment and nowhere else.
      ingress: {
        external: false
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registryConfig
      secrets: [
        {
          name: secretNames.openAiApiKey
          keyVaultUrl: '${keyVaultUri}secrets/${secretNames.openAiApiKey}'
          identity: identityId
        }
        {
          name: secretNames.appDatabasePassword
          keyVaultUrl: '${keyVaultUri}secrets/${secretNames.appDatabasePassword}'
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${registryLoginServer}/dataagent-api:${imageTag}'
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(commonEnv, [
            {
              // Built here rather than stored, so the password is the only part
              // that comes from Key Vault and the rest is readable in the
              // template. `sslmode=require` is not optional against a server
              // configured with `require_secure_transport`.
              name: 'APP_DATABASE_URL'
              value: 'postgresql+asyncpg://dataagent_app@${postgresHost}:5432/${postgresDatabase}?ssl=require'
            }
            {
              name: 'APP_DB_PASSWORD'
              secretRef: secretNames.appDatabasePassword
            }
            {
              name: 'OPENAI_API_KEY'
              secretRef: secretNames.openAiApiKey
            }
            {
              // Key Vault is the secrets backend in Azure, and `config.py`
              // asserts exactly this at boot in a production build.
              name: 'SECRETS_BACKEND'
              value: 'keyvault'
            }
            {
              // **These names are the `Settings` field names, and that is a
              // constraint rather than a preference** (**B-120**). This template
              // previously set `AZURE_KEY_VAULT_URI` and `ARTIFACTS_BLOB_ENDPOINT`,
              // which read well and which `config.py` has never looked for — so a
              // deployed API would have had `SECRETS_BACKEND=keyvault` and no
              // vault address, and written artifacts to a container filesystem
              // that vanishes on the next revision. `scripts/check_env.sh` now
              // compares this file against `Settings` and fails on a name nothing
              // reads.
              name: 'KEY_VAULT_URL'
              value: keyVaultUri
            }
            {
              name: 'ARTIFACTS_BACKEND'
              value: 'blob'
            }
            {
              name: 'ARTIFACTS_ACCOUNT_URL'
              value: blobEndpoint
            }
            {
              name: 'AUTH_MODE'
              value: 'entra'
            }
          ])
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 8000
              }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

// **The migration job** (WP12.2). A one-off Container Apps job rather than a step
// in the pipeline, and the reasons are structural rather than convenience:
//
//   * The Postgres server has **no public endpoint**. It answers inside this
//     environment's subnet and nowhere else, so a GitHub runner cannot reach it
//     — and the answer to that is not a firewall rule opening the platform
//     database to a cloud provider's address range.
//   * Migrations run as the **owner** role and the API deliberately does not.
//     That separation is a hard rule; a migration step borrowing the app's
//     credential would collapse it quietly.
//
// `manualTriggerConfig` with no schedule: it runs when `deploy.yml` starts it,
// between pushing the image and swapping the app revision. Nothing runs it on a
// timer, because a migration that happens when nobody asked is a schema change
// nobody reviewed.
resource migrate 'Microsoft.App/jobs@2024-03-01' = if (deployJobs) {
  name: 'cj-dataagent-migrate-${env}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Manual'
      // Ten minutes, matching what `ops/scripts/deploy_migrate.sh` waits for.
      // Two numbers that must agree, and the script names this one.
      replicaTimeout: 600
      // **No retries.** Alembic is not idempotent mid-failure: a migration that
      // died halfway leaves a partial transaction the next attempt would run
      // against a schema neither revision describes. A person looks instead.
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: registryConfig
      secrets: [
        {
          name: secretNames.databasePassword
          keyVaultUrl: '${keyVaultUri}secrets/${secretNames.databasePassword}'
          identity: identityId
        }
        {
          name: secretNames.appDatabasePassword
          keyVaultUrl: '${keyVaultUri}secrets/${secretNames.appDatabasePassword}'
          identity: identityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'migrate'
          image: '${registryLoginServer}/dataagent-api:${imageTag}'
          // **Two steps, in this order, and the second is not optional.**
          // `alembic upgrade head` creates `dataagent_app` and its grants
          // (migration 0002) but deliberately gives it no password — a migration
          // in git must never contain a credential. `grant_app_login` supplies
          // one from the vault and then reads back what the role can actually
          // do, refusing the deploy if it can log in with more privilege than
          // RLS assumes. Without it the role exists and cannot connect at all,
          // which is the state a deployed API would otherwise have found.
          command: ['sh', '-c']
          args: ['alembic upgrade head && python -m dataagent.db.grant_app_login']
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            {
              // The owner DSN, password-less here and rejoined by
              // `_with_password` in config.py — the same arrangement the API
              // uses for its own role, and the reason DB_PASSWORD is a field
              // rather than a name the template invented (**B-120**).
              name: 'DATABASE_URL'
              value: 'postgresql+asyncpg://${postgresAdminLogin}@${postgresHost}:5432/${postgresDatabase}?ssl=require'
            }
            {
              name: 'DB_PASSWORD'
              secretRef: secretNames.databasePassword
            }
            {
              // The password this job is about to give `dataagent_app`. The job
              // holds it because it is the only process that can reach the
              // server; the API receives the same value as its own connection
              // secret and never sets it.
              name: 'APP_DB_PASSWORD'
              secretRef: secretNames.appDatabasePassword
            }
            {
              name: 'ENV'
              value: env
            }
          ]
        }
      ]
    }
  }
}

resource web 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: 'ca-dataagent-web-${env}'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      // **External.** The one public surface.
      ingress: {
        external: true
        targetPort: 3000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registryConfig
    }
    template: {
      containers: [
        {
          name: 'web'
          image: '${registryLoginServer}/dataagent-web:${imageTag}'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: commonEnv
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output environmentId string = environment.id
output apiFqdn string = api.?properties.?configuration.?ingress.?fqdn ?? ''
output webFqdn string = web.?properties.?configuration.?ingress.?fqdn ?? ''
output secretNames object = secretNames
