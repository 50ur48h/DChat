// The monthly spend ceiling, and who hears about it.
//
// A budget does not stop anything — Azure has no hard cap on a subscription, and
// pretending otherwise would be worse than useless. What it does is tell a person
// early, which is the only control available at this layer. The application's own
// hard-stop is `quotas/` in WP12.3, and that one does refuse.
//
// **The alert address is not in a tracked file.** It is a personal email, it has
// no default, and no `.bicepparam` in this repo carries it — it is supplied at
// deploy time from a GitHub secret. That is the owner's instruction and it is
// also the right shape: a repository that is public should not carry the address
// its owner reads alerts on.

targetScope = 'resourceGroup'

@description('Environment discriminator — dev or prod.')
param env string

@description('Monthly ceiling in USD. Alerts fire at fractions of it, not at it.')
@minValue(1)
param monthlyLimitUsd int

@description('Where the alert goes. No default, and never written to a tracked file.')
param alertEmail string

@description('First day of the budget period, as YYYY-MM-01. Azure requires a start date and refuses one in the past.')
param startDate string

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-dataagent-${env}'
  properties: {
    category: 'Cost'
    amount: monthlyLimitUsd
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      // **Three thresholds, and the first two are the useful ones.** An alert at
      // 100% arrives when the money is already spent; 50% is the one that gives
      // a week to look. The third is `Forecasted` rather than `Actual`, which
      // fires when the *trend* would exceed the cap — the only one of the three
      // that can arrive before the overspend rather than during it.
      half: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 50
        contactEmails: [alertEmail]
        thresholdType: 'Actual'
      }
      most: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 90
        contactEmails: [alertEmail]
        thresholdType: 'Actual'
      }
      forecast: {
        enabled: true
        operator: 'GreaterThan'
        threshold: 100
        contactEmails: [alertEmail]
        thresholdType: 'Forecasted'
      }
    }
  }
}

output id string = budget.id
