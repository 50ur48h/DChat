// The network private Postgres needs, and nothing more.
//
// Architecture 9.1 puts the platform database behind **private access**, which
// on Flexible Server means VNet integration rather than a firewall rule: the
// server gets no public endpoint at all, and is reachable only from a delegated
// subnet. That is a stronger claim than an allow-list — there is no public
// address to leak, mis-scope, or leave open to 0.0.0.0/0 for an afternoon.
//
// It costs this file. A public server with a firewall would be three lines, and
// the whole of Part 7's argument is that a control which cannot be bypassed
// beats one that can be misconfigured. Two subnets, because Azure requires them
// to be separate: Postgres delegates its subnet exclusively, and the Container
// Apps environment claims its own for infrastructure.

@description('Where everything goes. Passed rather than defaulted so a module cannot silently land in a second region.')
param location string

@description('Environment discriminator — dev or prod. Names derive from it.')
param env string

@description('Tags applied to every resource, so cost and ownership are answerable.')
param tags object

// /16 for the VNet with /24s inside it: far more address space than this will
// use, and the alternative — tight ranges chosen now — is the thing that makes
// a later subnet impossible without renumbering.
var vnetPrefix = '10.20.0.0/16'
var postgresPrefix = '10.20.1.0/24'

// Container Apps wants a /23 at minimum for a consumption environment. Sized to
// the requirement rather than to taste, because getting it wrong fails at
// deploy time with an error that names a number and not a reason.
var appsPrefix = '10.20.4.0/23'

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'vnet-dataagent-${env}'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [vnetPrefix]
    }
    subnets: [
      {
        name: 'snet-postgres'
        properties: {
          addressPrefix: postgresPrefix
          // Delegation is what makes this subnet Postgres's and nothing else's.
          // Azure enforces it: no other resource type can be placed here, which
          // is the property that makes "reachable only from the app subnet"
          // true by construction rather than by convention.
          delegations: [
            {
              name: 'postgres-flexible'
              properties: {
                serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers'
              }
            }
          ]
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-apps'
        properties: {
          addressPrefix: appsPrefix
          delegations: [
            {
              name: 'container-apps'
              properties: {
                serviceName: 'Microsoft.App/environments'
              }
            }
          ]
        }
      }
    ]
  }
}

// The private DNS zone that makes the server's hostname resolve inside the VNet.
// Without it the connection string names a host that answers nowhere, which
// presents as a timeout rather than as a DNS failure and is the single most
// confusing way this can be got wrong.
resource dns 'Microsoft.Network/privateDnsZones@2024-06-01' = {
  name: 'dataagent-${env}.private.postgres.database.azure.com'
  location: 'global'
  tags: tags
}

resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01' = {
  parent: dns
  name: 'link-${env}'
  location: 'global'
  tags: tags
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: vnet.id
    }
  }
}

output vnetId string = vnet.id
output postgresSubnetId string = vnet.properties.subnets[0].id
output appsSubnetId string = vnet.properties.subnets[1].id
output privateDnsZoneId string = dns.id
// Named so `main.bicep` can depend on the link rather than on the zone: the
// server must not be created before the link exists, or its hostname resolves
// nowhere on first boot.
output privateDnsLinkId string = dnsLink.id
