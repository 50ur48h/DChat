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

## Signing in: GitHub's subject claim is not what #92's instructions assume

**The first real deploy failed here, and the instructions were not wrong when
they were written.** PR #92 tells an owner to create federated credentials whose
subject is `repo:<owner>/<repo>:environment:dev`. That is the format GitHub
documented and the format the two original credentials on
`dataagent-github-oidc` carry. The token GitHub actually presented on
2026-08-24 was:

```
repo:50ur48h@130345252/DChat@1329894088:environment:dev
```

— owner and repository **IDs** embedded, so that renaming either cannot silently
redirect somebody else's federation at this app registration. Azure answered
`AADSTS700213: No matching federated identity record found for presented
assertion subject`, every later step skipped, and nothing was created.

The repository's own setting is worth reading, because it explains why this is
not a misconfiguration anybody made:

```console
$ gh api repos/50ur48h/DChat/actions/oidc/customization/sub
{"use_default":true,"use_immutable_subject":false,
 "sub_claim_prefix":"repo:50ur48h@130345252/DChat@1329894088"}
```

`use_default` is true and `use_immutable_subject` is **false**, and the prefix
GitHub applies is nonetheless the ID-qualified one. The platform default moved
after #92 was written.

**What to do when setting this up again.** Do not copy a subject out of #92. Ask
the repository what it will send, and create the credential from that:

```sh
prefix=$(gh api repos/<owner>/<repo>/actions/oidc/customization/sub --jq .sub_claim_prefix)
for env in dev prod; do
  az ad app federated-credential create --id <app-id> --parameters "{
    \"name\": \"github-${env}-immutable\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"${prefix}:environment:${env}\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"
done
```

**The credentials in the old format are deliberately kept.** They cost nothing,
they document what the format used to be, and a credential that stops matching is
a refusal rather than a widening — the failure mode is a deploy that will not
start, which is the safe direction.

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
