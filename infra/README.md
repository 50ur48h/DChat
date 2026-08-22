# infra — the deployment, as code

Bicep modules for the Azure shape architecture 9.1 fixes. Nothing here is
deployed by WP12.1: this work package builds and lints, and `what-if` starts
once the OIDC identity exists in WP12.2.

## What is here

```
main.bicep              the resource group's whole deployment, one module per service
modules/network.bicep   VNet + the two subnets private Postgres and ACA need
modules/logs.bicep      Log Analytics + Application Insights, with a daily cap
modules/identity.bicep  the user-assigned identity everything runs as
modules/keyvault.bicep  RBAC-mode Key Vault; values are never in Bicep
modules/storage.bicep   Blob, one container for artifacts
modules/acr.bicep       Basic registry
modules/postgres.bicep  Flexible Server, B-series, pgvector, VNet-integrated
modules/roles.bicep     the three role assignments the identity needs
modules/apps.bicep      Container Apps environment + api and web
params/dev.bicepparam   the environment that exists
params/prod.bicepparam  the environment that does not exist yet (D-041)
```

## Two rules that shaped every file

**No secret value appears in this directory.** Key Vault is created empty and its
secrets are written out of band; Container Apps reference them by URI and read
them with a managed identity. A parameter that would carry a secret — the budget
alert address is the only one — has no default and no value in a tracked
`.bicepparam`, and is supplied at deploy time from a GitHub secret. Grepping this
directory for a credential should find nothing, and that is checkable.

**Every resource in `main.bicep` is in architecture 9.1's justification table.**
That table is the deliberate list, and the column that matters is the one saying
what was left out — APIM, Service Bus, AI Search, Functions. A module for any of
them belongs in a PR that first amends the table.

## Dev only, in this phase

Prod is deferred (**D-041**, owner's call of 2026-08-22). `params/prod.bicepparam`
exists and is kept honest so that standing prod up later is a parameter file and
an approval, not a rewrite — but nothing here deploys it, and no pipeline
references it yet.

## Naming

`rg-dataagent-dev`, approved by the owner. Inside it, names are
`<abbrev>-dataagent-<env>` where Azure allows hyphens, and
`<abbrev>dataagent<env><suffix>` where it does not — ACR, storage and Key Vault
have globally unique namespaces, so those take a `uniqueString(resourceGroup().id)`
suffix. The suffix is derived rather than passed: a name nobody types is a name
nobody typos, and it stays stable across redeployments of the same group.
