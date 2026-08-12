# Changelog

All notable user-visible changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are added in the PR that makes the change (plan §1.3, item 9) — 1–3 lines
under "Unreleased", only for changes a user would notice.

## [Unreleased]

### Security
- Connections to a customer's database are encrypted unless the database is on
  the server itself. A data source may ask for stricter TLS and never for
  weaker; each one shows the mode it uses, and testing it reports whether the
  server actually encrypted the connection rather than assuming it did.
- Database credentials for a registered data source are encrypted and kept
  outside the platform database, which holds only a reference to them. No API
  response has a field that could carry one, and connector errors are scrubbed
  of connection strings, passwords and addresses before they reach a log.
- Three fixed roles are enforced on every org-scoped route. A refusal is
  recorded where its organization can find it, and refusals that belong to no
  organization are kept in a platform security log rather than dropped.
- Tenant isolation is enforced by the database: every tenant table has a
  row-level security policy, and the API connects as an unprivileged role that
  cannot bypass it. The audit log is append-only by grant.

### Added
- A registered database can be read into a catalog: its tables, views, columns
  and the joins its engine declares. Refreshing again when nothing has changed
  costs nothing and says so, and the previous catalog is kept rather than
  replaced, so anything still reading it is undisturbed.
- Register the databases an organization wants analysed: list, register, rename,
  rotate credentials, remove, and check that the address answers. Admin-only,
  except for listing, which any member may do.
- Testing a PostgreSQL data source now connects with the stored credentials and
  reports whether they can write. A data source counts as verified only when the
  database itself says the account cannot write, and rotating a credential or
  changing an address retires that verification until it is checked again.
- SQL Server databases can be registered and verified the same way, against the
  same standard of proof. `make up.mssql && make seed.mssql` builds a demo one
  to try it against.
- `make seed` creates a read-only login for the demo database, so it can be
  registered the way a real one should be.
- A screen for data sources: register a database, test it, rotate its password
  and remove it, without a terminal. Each one shows the account it connects as,
  whether its credentials have been proven read-only, and how much encryption
  the connection uses — three separate facts, because they are.
- People are no longer shown buttons their role does not permit. A Reader sees
  the members and the data sources, and one line explaining what only an Admin
  can do, instead of controls that answer with an error.
- A web app you can actually use: sign in, see who you are and which
  organizations you belong to, create one, invite people with a role, and
  manage members. Sign-in works against Microsoft Entra External ID, or
  against a local development issuer when no tenant is configured.
- Sign in, create an organization, invite people to it by email with a role,
  and accept an invitation. `GET /v1/me` shows who you are and which
  organizations you belong to. The last Admin cannot demote or remove
  themselves.
- API service (`apps/api`) with `GET /healthz`, reporting application version and
  the commit its image was built from.
- Web app (`apps/web`) with a landing page that reports whether the API is
  reachable, along with its version and build commit.
- The seed publishes its ground truth to `ops/seed/truths.json`, so the eval
  harness reads expected answers from the fixture instead of hardcoding them.
- One-command local stack: `make up` starts the platform database, a pizza-chain
  demo database, the API and the web app; `make seed` fills the demo database
  with 18 months of reproducible data.

### Fixed
- Someone whose sign-in carries no email address is shown by name, or by the
  identity their account was created with, instead of by a made-up address.
