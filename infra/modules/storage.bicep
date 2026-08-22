// Blob storage: document originals and stored query results.
//
// WP5.2b's claim is that no unmasked value reaches a stored file, and these are
// those files. So the account is closed by default in every way it can be: no
// public blob access, no shared-key authorisation, TLS 1.2, HTTPS only. What
// reads them is the managed identity with a data-plane role, which means there
// is no account key anywhere to leak — and `allowSharedKeyAccess: false` makes
// that true by refusing key-based calls outright rather than trusting nobody to
// make one.

@description('Where everything goes.')
param location string

@description('Environment discriminator — dev or prod.')
param env string

@description('Tags applied to every resource.')
param tags object

@description('Globally-unique suffix, derived from the resource group.')
param suffix string

@description('How long a stored result lives before lifecycle management deletes it.')
@minValue(1)
param artifactRetentionDays int

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  // Lower-case alphanumeric, 24 characters, globally unique.
  name: take('stdataagent${env}${suffix}', 24)
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    accessTier: 'Hot'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource blob 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource artifacts 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'artifacts'
  properties: {
    publicAccess: 'None'
  }
}

resource documents 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blob
  name: 'documents'
  properties: {
    publicAccess: 'None'
  }
}

// **The retention promise, enforced by the platform rather than by the app.**
// `ARTIFACT_RETENTION_DAYS` is a promise the product makes about how long a
// stored result lives; a promise kept only by application code is one that
// stops being kept the first time that code does not run. This deletes them
// whether or not anything is running.
resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: account
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-artifacts'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['artifacts/']
            }
            actions: {
              baseBlob: {
                delete: {
                  daysAfterModificationGreaterThan: artifactRetentionDays
                }
              }
            }
          }
        }
      ]
    }
  }
}

output id string = account.id
output name string = account.name
output blobEndpoint string = account.properties.primaryEndpoints.blob
