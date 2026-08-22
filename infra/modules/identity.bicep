// The one identity everything runs as.
//
// User-assigned rather than system-assigned, and the reason is ordering. A
// system-assigned identity does not exist until its app does, so the role
// assignments it needs cannot be written in the same deployment that creates the
// app — Key Vault access would arrive one deployment late, and the first boot
// would fail reading a secret it is entitled to. A user-assigned identity is
// created first, granted first, and then attached, so every permission is in
// place before anything tries to use it.
//
// One identity for both apps rather than two, because they need the same three
// grants and a second principal would be a second thing to audit for no
// difference in what it may do.

@description('Where everything goes.')
param location string

@description('Environment discriminator — dev or prod.')
param env string

@description('Tags applied to every resource.')
param tags object

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-dataagent-${env}'
  location: location
  tags: tags
}

output id string = identity.id
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
