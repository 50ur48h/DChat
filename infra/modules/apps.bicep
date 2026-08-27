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

@description('The Log Analytics workspace resource id. A diagnostic setting routes the environment logs there; the environment itself is told nothing about the workspace, which is what keeps this template free of a shared key.')
param logAnalyticsWorkspaceId string

@description('Where the identity provider publishes its discovery document. Required whenever AUTH_MODE is entra, which in a deployment it always is.')
param oidcAuthority string

@description('The audience every accepted token must carry. Comma-separated when one API is known by more than one name.')
param oidcAudience string

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

// **Unconditional, and it belongs in phase 1 after all.** It was briefly gated on
// `deployApps || deployJobs`, because a broken log configuration made a phase-4
// resource fail a phase-1 pass. That configuration is fixed, and the environment
// turns out to be genuinely phase-1: its `defaultDomain` is the only place the
// API's public hostname comes from, and `NEXT_PUBLIC_API_URL` is **inlined into
// the browser bundle at build time**, so the web image cannot be built until this
// exists. Gating it would deadlock the pipeline rather than protect it.
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
    // **`azure-monitor`, not `log-analytics`, and the difference is a
    // credential.** This block used to say `log-analytics` with a `customerId`
    // and a comment claiming the environment writes with its own identity. It
    // does not: that destination requires `customerId` **and** `sharedKey`, and
    // Azure refuses the environment at preflight with `LogAnalyticsConfiguration
    // is invalid` — which is how the first real deploy failed, after the template
    // had compiled, passed `check.infra` and passed `what-if`.
    //
    // The comment's intent was right and its mechanism was not. `azure-monitor`
    // is the keyless destination: the environment emits, and a diagnostic setting
    // below routes to the workspace. No shared key exists anywhere in this
    // template, which is the property WP12.1 chose and the reason not to solve
    // this with `listKeys()`.
    appLogsConfiguration: {
      destination: 'azure-monitor'
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
      // **External, and the previous `false` made the product unusable.** The
      // browser talks to this API directly: it holds the Entra token and
      // presents it per request (architecture 157), and run progress arrives
      // over SSE from this origin. An internal ingress has an `.internal.`
      // hostname that resolves only inside the environment, so the deployed web
      // app could not reach it from anyone's browser — which is exactly what the
      // first real page load showed.
      //
      // This is not a weaker posture than the comment it replaces implied.
      // Architecture 0.2.3 and 7 are explicit that Container Apps ingress *is*
      // the gateway for V1 — it terminates TLS, the API validates every JWT, and
      // rate limits live in the app because quotas are business logic. What
      // protects this surface is the token check, not the absence of a hostname.
      ingress: {
        external: true
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
              // **The model configuration, which this template did not have and
              // the product cannot run without** (**B-126**). `OPENAI_API_KEY`
              // above is the credential; these four are what tell the product
              // what to spend it on. Without them `llm_providers` falls to its
              // default of `('openai',)`, `llm_models` to `{}`, and
              // `registry.resolve` raises *"LLM_MODELS names no models for
              // provider 'openai'"* at the first model call — so every question
              // asked in the browser ends as `failed` with a generic reason.
              //
              // `ops/docker-compose.yml` carries that same sentence about
              // Phase 7. Compose was fixed then; this file was written later and
              // repeated it, which is what `check_env.sh`'s check 9 now stops.
              //
              // **Not secrets, and deliberately literal here.** Model ids and
              // published list prices are configuration a reviewer should be
              // able to read in the template — the argument `KEY_VAULT_URL`
              // makes above. The only secret in this block is the key, and it is
              // a `secretRef`.
              name: 'LLM_PROVIDERS'
              value: 'openai'
            }
            {
              // **No `mid`, because no role asks for one** (B-154). `mid` is the
              // default tier for `compose` alone, and `LLM_ROLE_MAP` below moves
              // it to `small` — so `gpt-5.6-terra` sat here priced and called by
              // nothing, which is a trap for whoever tunes this next: it reads as
              // a tier in play when reasoning about cost.
              //
              // Safe to remove because `/healthz` now resolves *every* role and
              // reports one that maps to a tier no model fills. Map a role back
              // to `mid` without adding a model and the probe says so, instead of
              // the first question of the day dying at its first model call.
              name: 'LLM_MODELS'
              value: '{"openai":{"small":"gpt-5.6-luna","strong":"gpt-5.6-sol"}}'
            }
            {
              // Composing is the long generation and the cheapest place to save,
              // per architecture 8.3's cost lever.
              name: 'LLM_ROLE_MAP'
              value: '{"compose":"small"}'
            }
            {
              // A model absent from this map is recorded with a NULL cost, which
              // means unpriced — never free. The embedding model is priced here
              // too, because it is metered against the same run.
              name: 'LLM_PRICES'
              value: '{"gpt-5.6-luna":{"input":0.20,"output":1.20},"gpt-5.6-sol":{"input":5.00,"output":30.00},"text-embedding-3-small":{"input":0.02,"output":0.00}}'
            }
            {
              // **Half of what a developer's `.env` sets, on purpose.** Dev has
              // a public hostname and nobody watching the bill; a lower ceiling
              // costs a truncated run — which arrives as an answer with caveats,
              // not a failure — and buys a bounded spend. Owner's call,
              // 2026-08-24.
              name: 'LLM_RUN_COST_LIMIT_USD'
              value: '1.00'
            }
            {
              // **Not the cause of B-126 and fixed with it.** A deployment with
              // no embedding model degrades correctly — `get_embedder` returns
              // None and retrieval falls back to lexical — so this was invisible
              // rather than broken. It made dev's retrieval quietly worse than
              // local, which is its own kind of misleading environment.
              name: 'EMBEDDINGS_PROVIDER'
              value: 'openai'
            }
            {
              name: 'EMBEDDINGS_MODEL'
              value: 'text-embedding-3-small'
            }
            {
              // Must equal the width of the `vector(...)` column revision 0016
              // created. `check_dimensions` compares them before the first
              // vector is written rather than at the insert that would reject it.
              name: 'EMBEDDINGS_DIMENSIONS'
              value: '1536'
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
            {
              // **Set here or the mode above is a promise the API cannot keep.**
              // `AUTH_MODE=entra` with no authority raises at the first
              // authenticated request: *"there is nothing to discover signing
              // keys from, and every token would have to be taken on trust"* —
              // which is `config.py` refusing correctly and a deployment that
              // never gave it the chance. B-120's shape a third time: a mode
              // selected without what the mode needs.
              name: 'OIDC_AUTHORITY'
              value: oidcAuthority
            }
            {
              // Not a secret and not derivable from the authority: an Entra v2
              // access token carries the resource's client-ID GUID while a v1
              // token carries its `api://` URI, and both name this API, so the
              // deployment passes whichever pair its registration issues.
              name: 'OIDC_AUDIENCE'
              value: oidcAudience
            }
            {
              // Never set before, so the API defaulted to `http://localhost:3000`
              // and would have refused the deployed web app's own browser even
              // once it could reach it. Built from the environment domain rather
              // than passed in, so it cannot drift from the hostname the web app
              // is actually served on.
              name: 'CORS_ORIGINS'
              value: 'https://ca-dataagent-web-${env}.${environment.properties.defaultDomain}'
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

// Where the environment's logs actually go. `appLogsConfiguration:
// azure-monitor` says *emit*; this says *to here*. Split in two because the
// routing is a property of the subscription's monitoring, not of the
// application — and because it is the half that needs no credential.
resource environmentDiagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'send-to-log-analytics'
  scope: environment
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        category: 'ContainerAppConsoleLogs'
        enabled: true
      }
      {
        category: 'ContainerAppSystemLogs'
        enabled: true
      }
    ]
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
// **The identity self-check** (**B-125**, and Blob before it happens again).
//
// Registering a data source writes a customer's credential to Key Vault; every
// query execution writes a result artifact to Blob. Both are things only the
// *application's* identity does, and both were first exercised by a person
// clicking a button — the vault one failed, because the identity had been granted
// a read-only role with a comment that argued for it convincingly.
//
// **Why this cannot be a step in the pipeline.** The deploy job authenticates as
// the OIDC identity, which holds broad permissions on the resource group. A vault
// write from the runner would have passed for the entire period B-125 was live:
// a check that passes for a reason unrelated to the thing it checks, which is the
// failure this repository keeps re-finding. The only way to test what the app can
// do is to run as the app, which is what this job is.
//
// It touches **no database**, so it is independent of the migration job and its
// failure means one thing only: this identity cannot store what the product
// stores.
resource selfcheck 'Microsoft.App/jobs@2024-03-01' = if (deployJobs) {
  name: 'cj-dataagent-selfcheck-${env}'
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
      // Two minutes. Four round trips to two Azure services; anything longer is
      // a hang rather than slowness, and a smoke check that waits ten minutes to
      // report a permission problem is one people stop waiting for.
      replicaTimeout: 120
      // **One retry, unlike the migration job's zero.** These operations are
      // idempotent — each writes under a fresh uuid and deletes it — so a retry
      // cannot corrupt anything, and a transient 503 from Key Vault failing a
      // deploy would be its own kind of check that fails for the wrong reason.
      replicaRetryLimit: 1
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: registryConfig
    }
    template: {
      containers: [
        {
          name: 'selfcheck'
          image: '${registryLoginServer}/dataagent-api:${imageTag}'
          command: ['sh', '-c']
          args: ['python -m dataagent.ops.selfcheck']
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          // **The app's storage configuration and nothing else.** No DSN, no
          // model configuration, no OIDC: this job answers one question, and
          // giving it more environment would let it fail for reasons that are
          // not that question.
          env: concat(commonEnv, [
            {
              name: 'SECRETS_BACKEND'
              value: 'keyvault'
            }
            {
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
          ])
        }
      ]
    }
  }
}

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

@description('The environment domain every app hostname is built from. The web build needs the API hostname before either app exists, and this is where it comes from.')
output defaultDomain string = environment.properties.defaultDomain
output apiFqdn string = api.?properties.?configuration.?ingress.?fqdn ?? ''
output webFqdn string = web.?properties.?configuration.?ingress.?fqdn ?? ''
output secretNames object = secretNames
