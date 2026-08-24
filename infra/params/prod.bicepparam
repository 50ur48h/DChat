// The environment that does not exist yet.
//
// **Nothing deploys this** (**D-041**, owner's call of 2026-08-22): Phase 12
// stands up dev only, and `v1.0.0` is tagged from dev. This file exists so that
// standing prod up later is a parameter file and an approval rather than a
// rewrite — and it is kept honest for the same reason a test that is never run
// is worse than no test: a parameter file that has drifted from `main.bicep`
// would be discovered at the worst possible moment.
//
// `az bicep build-params` runs over it in CI alongside dev's, so a parameter
// added to `main.bicep` and not to this file fails the build rather than waiting
// for the day somebody needs it.
//
// The values below are a *starting point recorded with its reasoning*, not a
// decision. Whoever deploys prod owns them.

using '../main.bicep'

param env = 'prod'
param location = 'southeastasia'

// **Not Burstable.** A burstable tier accrues credits and then throttles, which
// is survivable in dev and is a latency cliff under sustained load. GeneralPurpose
// is the first tier with a predictable floor.
param postgresSkuName = 'Standard_D2ds_v5'
param postgresSkuTier = 'GeneralPurpose'
param postgresStorageGb = 128

// The maximum Flexible Server offers. Retention is the cheapest insurance in
// this file, and the restore drill is only as good as the window it can reach
// back into.
param postgresBackupRetentionDays = 35

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

param logsDailyCapGb = 5
param artifactRetentionDays = 30

param imageTag = 'bootstrap'

// **One, not zero.** Scale-to-zero costs a cold start on the first request after
// a quiet period, which is a customer waiting rather than a developer.
param minReplicas = 1
param maxReplicas = 10

param monthlyBudgetUsd = 200

param budgetStartDate = '2026-09-01'

param oidcAuthority = readEnvironmentVariable('OIDC_AUTHORITY')
param oidcAudience = readEnvironmentVariable('OIDC_AUDIENCE')
