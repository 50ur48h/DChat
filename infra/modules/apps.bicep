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

@description('Blob endpoint, for the artifact store.')
param blobEndpoint string

@description('Minimum replicas. Zero is the point of Container Apps for a dev environment; prod may want one to avoid a cold start.')
@minValue(0)
param minReplicas int

@description('Maximum replicas, so a runaway cannot scale into a bill.')
@minValue(1)
param maxReplicas int

// **Named here rather than inline**, so the set of secrets the platform expects
// is one readable list. Each is a Key Vault secret name; the vault is created
// empty and these are written out of band before the first deploy.
var secretNames = {
  openAiApiKey: 'openai-api-key'
  appDatabasePassword: 'app-database-password'
  databasePassword: 'database-password'
  localSecretsKey: 'local-secrets-key'
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

resource api 'Microsoft.App/containerApps@2024-03-01' = {
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
        {
          name: secretNames.localSecretsKey
          keyVaultUrl: '${keyVaultUri}secrets/${secretNames.localSecretsKey}'
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
              name: 'LOCAL_SECRETS_KEY'
              secretRef: secretNames.localSecretsKey
            }
            {
              // Key Vault is the secrets backend in Azure, and `config.py`
              // asserts exactly this at boot in a production build.
              name: 'SECRETS_BACKEND'
              value: 'keyvault'
            }
            {
              name: 'AZURE_KEY_VAULT_URI'
              value: keyVaultUri
            }
            {
              name: 'ARTIFACTS_BLOB_ENDPOINT'
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

resource web 'Microsoft.App/containerApps@2024-03-01' = {
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
output apiFqdn string = api.properties.configuration.ingress.fqdn
output webFqdn string = web.properties.configuration.ingress.fqdn
output secretNames object = secretNames
