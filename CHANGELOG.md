# Changelog

All notable user-visible changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are added in the PR that makes the change (plan §1.3, item 9) — 1–3 lines
under "Unreleased", only for changes a user would notice.

## [Unreleased]

### Security
- Every query the platform runs against a customer's database is checked before
  it is sent: one statement, read-only, no system schemas, and every table and
  column resolved against that organization's own catalog. A column an admin has
  marked unqueryable is refused wherever it appears — in the result, in a
  filter, in a join — and a column marked sensitive comes back masked. Each
  query is capped in rows and in time, and every attempt is recorded, including
  the ones refused before the database was contacted.
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
- A run's trace is append-only too: the record of how an answer was reached can
  be written and never rewritten. Conversations are private to the person who
  started them — a colleague in the same organization cannot open one, even
  knowing its address.

### Fixed
- Searching an organization's documents now really does look for meaning and not
  only wording: nothing had ever been embedded, because no part of the
  application built an embedder. Uploading fills it in, and re-indexing fills it
  in for anything uploaded before.
- When the search by meaning cannot run — no embedding model configured, or a
  question that has spent its cost allowance — the wording search still answers
  and says the other half did not run, instead of reporting that nothing has been
  written down.
- Asking a question in ordinary words now finds the tables it is about — by
  meaning as well as by wording. "Which day of the week is busiest?" shares no
  word with any table description and used to find nothing at all, leaving the
  agent with no tables to work from; it now finds the orders table.
- Asking a question in ordinary words now finds the tables it is about. Searching
  the catalog used to require every word of the question to appear in a table's
  description, so a real question matched nothing at all.

### Added
- When a review of an answer does not pass and the answer is shown anyway, the
  review's objection is now the first thing listed beside it, in the reviewer's
  own words, and the answer can no longer describe itself as highly confident.
  An answer that overstates its own rigour is worse than one that admits doubt.
- Metrics can be defined once and then enforced. A definition says what a metric
  means and which filters it requires; the agent is shown it, and the query it
  writes is checked against it — an answer computed without a required filter is
  stopped rather than explained away.
- An answer that relies on a definition read out of your documents now says so,
  naming the term: the wording informed the agent, but nothing checked that the
  query actually followed it. Defining that metric properly removes the caveat,
  because then it is enforced.
- The agent can now look a term up in your documents in the middle of answering.
  When a question turns on how your business defines something — net revenue, an
  active customer — it searches what you have written down, and the answer's
  timeline shows which document it consulted and what it found.
- A documents screen: upload a policy or a definition, see how much of it is
  searchable, re-index it, or remove it. A document that could not be read says
  why in words you can act on — a scanned PDF says it needs OCR — and one whose
  text has landed but whose meaning-search is still catching up says that too
  rather than claiming to be finished.
- The agent can consult the organization's own documents when a question turns on
  how the business defines something — what counts as net revenue, what an active
  customer is — and then queries the database for the values rather than reporting
  a number it read in a document. Retrieved text is presented to it as a record,
  never as an instruction.
- An organization's own documents can be uploaded and searched — markdown, plain
  text, and PDFs that carry a text layer. Passages are found by meaning as well
  as by wording, so a policy that says "revenue" answers a question that says
  "takings", and every passage names the document and section it came from. A
  scanned PDF is reported as needing OCR rather than accepted as an empty upload.
- A conversation now behaves like one. A follow-up — "check again", "and in
  June?", "why?" — is answered knowing the questions and answers that came
  before it, instead of being read as though it were the first thing said.
  Recent turns are shown to the agent as a record of what was said, never as
  instructions, and a follow-up runs its own query rather than repeating a
  number it has only been told.
- Every answer can show its working: a live timeline of what the agent did —
  which tables it read, what it asked the database, what it concluded, and where
  it stopped — appearing as it happens and still there afterwards. Refreshing the
  page mid-question loses none of it.
- A question that this database genuinely cannot answer is now refused before any
  query runs, and the refusal says which link is missing and what would unlock it
  — rather than joining unrelated tables and presenting whatever comes back.
- A question is now investigated rather than answered in one shot: the agent can
  run several queries, build on what each one told it, and stop by itself when it
  has enough. It is bounded — a question cannot run away with your money or your
  time — and if a limit stops it, the answer says so instead of quietly presenting
  a partial result as a complete one.
- Ask a question of your data in the browser and read the answer with the query
  behind it. Pick the database, type the question, watch the run report what it
  is doing, and expand any answer to see the SQL that produced it and the rows it
  returned. When the data cannot answer, it says so and says why, rather than
  guessing.
- A conversation is started against a chosen database, and every question in it
  is answered from that one. An organization with a single database needs no
  choice; one with several will say so rather than pick for you.
- The evidence behind an answer can be opened: the exact query that was run, the
  tables it read, how many rows came back and how long it took, and a preview of
  those rows with any masked column still masked. A query the platform refused to
  send shows what refused it instead of an empty result.
- Conversations: ask a question and get back a run to follow. Each run carries
  its status, its answer when there is one, the findings behind it, and a
  step-by-step trace you can re-read from any point — so a refreshed page picks
  up exactly where it left off. Sending the same question twice does not start
  it twice.
- Every table gets a plain-language card describing what it holds, its columns
  and roles, and the tables it joins to — or that it joins to none, which is the
  answer that matters most often. Searching in ordinary words finds the right
  table: "revenue" finds orders.
- A catalog browser: tables, columns, what a sample looked like, and which
  columns are masked. An Admin can change a column's policy from there, and the
  screen distinguishes "the classifier suspects this" from "somebody decided
  this" rather than showing one tick for both.
- Columns are profiled from a bounded sample — how often they are empty, how
  many distinct values, their range, and their commonest values — and anything
  that looks like personal data is masked before it is stored and defaults to
  masked for everyone. An Admin can override that per column, with a reason,
  and the decision survives every later refresh.
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
