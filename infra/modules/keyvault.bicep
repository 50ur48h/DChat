// Key Vault, in RBAC mode and created empty.
//
// **No secret value is in this file, or in any file in this directory.** The
// vault is infrastructure; what goes in it is written out of band, by a person
// or by the deploy workflow, and read at runtime through a managed identity. A
// Bicep template that carried a secret would put it in the deployment history of
// the resource group, where it is readable by anyone with reader access and
// survives the secret being rotated.
//
// RBAC rather than access policies: policies are a per-vault list that drifts
// from the subscription's own role model, and Azure's own guidance is now RBAC.
// It also means the grant lives beside the other two in `roles.bicep` rather
// than in a different shape here.

@description('Where everything goes.')
param location string

@description('Environment discriminator — dev or prod.')
param env string

@description('Tags applied to every resource.')
param tags object

@description('Globally-unique suffix, derived from the resource group.')
param suffix string

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  // Vault names are globally unique and capped at 24 characters, which is why
  // this one is compressed rather than following the hyphenated convention.
  name: take('kv-dataagent-${env}-${suffix}', 24)
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    // Soft delete is not optional on new vaults and is left at its default;
    // purge protection is on because a vault that can be purged can be replaced
    // by an attacker with an empty one of the same name.
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      // Azure services and the deploy pipeline both reach this over the public
      // endpoint; the control that matters is RBAC, not the network, because a
      // caller still needs a role to read anything. Tightening this to a private
      // endpoint is a Phase 12 hardening item rather than a WP12.1 default,
      // since it would also have to admit the GitHub runner.
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

output id string = vault.id
output name string = vault.name
output uri string = vault.properties.vaultUri
