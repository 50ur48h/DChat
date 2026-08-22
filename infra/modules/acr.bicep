// The private registry, Basic tier per architecture 9.1's table.
//
// The table names GHCR as an equally fine alternative. ACR is chosen because the
// images are pulled by Container Apps using the same managed identity as
// everything else, so there is no registry credential to store — which is the
// same argument that decides every other choice in this directory.

@description('Where everything goes.')
param location string

@description('Environment discriminator — dev or prod.')
param env string

@description('Tags applied to every resource.')
param tags object

@description('Globally-unique suffix, derived from the resource group.')
param suffix string

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  // Alphanumeric only, globally unique: no hyphens available.
  name: take('crdataagent${env}${suffix}', 50)
  location: location
  tags: tags
  sku: {
    name: 'Basic'
  }
  properties: {
    // **Off, deliberately.** The admin user is a username and password that
    // grants push and pull, cannot be scoped, and is the thing a leaked
    // pipeline log usually contains. Pulls use the managed identity's AcrPull
    // grant instead; pushes use the deploy workflow's OIDC identity.
    adminUserEnabled: false
    anonymousPullEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

output id string = registry.id
output name string = registry.name
output loginServer string = registry.properties.loginServer
