// The environment that exists.
//
// **Two parameters are absent on purpose** and are supplied at deploy time from
// GitHub secrets: `postgresAdminPassword` and `budgetAlertEmail`. The first is a
// credential; the second is a personal address, and this repository is public.
// Bicep will refuse a deployment that omits them, which is the behaviour worth
// having — a missing secret should stop a deploy rather than default to
// something.

using '../main.bicep'

param env = 'dev'
param location = 'southeastasia'

// B-series, the owner's choice. B1ms is one vCPU and 2 GB, which is enough for a
// platform database whose load is metadata and pgvector queries over a small
// corpus, and it is the cheapest tier that supports the extensions.
param postgresSkuName = 'Standard_B1ms'
param postgresSkuTier = 'Burstable'
param postgresStorageGb = 32

// Seven days, the owner's choice, and the number WP12.4's restore drill has to
// work against. It is also the floor Azure allows.
param postgresBackupRetentionDays = 7

// **These two come from the environment, not from this file.**
//
// `postgresAdminPassword` is a credential and `budgetAlertEmail` is a personal
// address on a public repository. Bicep will not accept a params file that
// simply omits a parameter, so they are read from the environment instead —
// supplied by the deploy workflow from GitHub secrets, and by CI from obvious
// dummies when it is only linting.
//
// **No default on either.** A default would mean a forgotten secret deploys
// something rather than failing, and a budget alert going to a placeholder
// address is indistinguishable from one going nowhere.
param postgresAdminPassword = readEnvironmentVariable('POSTGRES_ADMIN_PASSWORD')
param budgetAlertEmail = readEnvironmentVariable('BUDGET_ALERT_EMAIL')

param postgresAdminLogin = 'dataagent_admin'
param databaseName = 'dataagent'

// 1 GB a day. Generous for a dev environment answering a handful of questions,
// and the cap is what stops a retry storm becoming a bill.
param logsDailyCapGb = 1

// Matches ARTIFACT_RETENTION_DAYS' local default, so a stored result does not
// live longer in Azure than a developer would expect from running it here.
param artifactRetentionDays = 30

// A placeholder until WP12.2 pushes an image. `latest` is deliberately not used:
// a tag that moves makes a revision unreproducible, and the pipeline replaces
// this with a git sha.
param imageTag = 'bootstrap'

// Scale to zero. The point of Container Apps for an environment nobody is
// looking at most of the time, and the cold start it costs is a dev concern
// rather than a customer one.
param minReplicas = 0
param maxReplicas = 2

param monthlyBudgetUsd = 50

// Azure refuses a budget whose start date is in the past, and it must be the
// first of a month. Moved forward whenever this is next deployed into a new
// group; an existing budget ignores it.
param budgetStartDate = '2026-09-01'

// **Read from the environment, like the other two that are not in this file.**
// These name the owner's identity tenant, and this repository is public. Not
// secrets — a tenant id and an audience are in every token the SPA holds — but
// not ours to publish either, so the deploy supplies them from GitHub variables.
param oidcAuthority = readEnvironmentVariable('OIDC_AUTHORITY')
param oidcAudience = readEnvironmentVariable('OIDC_AUDIENCE')
