// What the application identity may do, and nothing else.
//
// Three grants, each the narrowest role that does its job. This file is the
// answer to "what can the running application reach", so it is worth reading in
// full before it changes — the same standard CLAUDE.md sets for `dal/`, and for
// the same reason: it is a security boundary, and a widened role here is not
// visible in any test.
//
// **Scoped to the resource, never to the resource group.** A group-scoped
// assignment is one line shorter and grants the role over everything in the
// group, including resources added later by somebody who was not thinking about
// this file. Each scope below names exactly one resource.
//
// **Role definition ids, not role names.** Names are display strings and are not
// unique; the GUIDs are stable across every tenant and are what Azure actually
// matches on.

@description('The principal id of the user-assigned identity from identity.bicep.')
param principalId string

@description('Key Vault, from keyvault.bicep.')
param keyVaultName string

@description('Storage account, from storage.bicep.')
param storageAccountName string

@description('Container registry, from acr.bicep.')
param registryName string

// Key Vault Secrets User — read a secret's value. Deliberately not Secrets
// Officer, which can also write and delete: the application reads the OpenAI
// key and has no business rotating it.
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'

// Storage Blob Data Contributor — read and write blobs within the account.
// Contributor rather than Reader because the artifact store writes; not
// Storage Account Contributor, which is a control-plane role and could delete
// the account itself.
var blobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

// AcrPull — pull images. There is no push here; the deploy workflow's own OIDC
// identity pushes, and the runtime identity has no reason to.
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: registryName
}

// `guid()` over the scope, principal and role: deterministic, so redeploying
// updates the same assignment instead of failing on a name that already exists,
// and distinct per triple so the three cannot collide.
resource secretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, principalId, keyVaultSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsUser
    )
    principalId: principalId
    // Stated explicitly. Without it, Azure resolves the principal type by
    // lookup, which races a freshly-created identity that has not yet
    // propagated — the classic intermittent "principal does not exist" on a
    // first deployment.
    principalType: 'ServicePrincipal'
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, principalId, blobDataContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      blobDataContributor
    )
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, principalId, acrPull)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
