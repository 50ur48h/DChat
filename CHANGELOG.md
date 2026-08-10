# Changelog

All notable user-visible changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are added in the PR that makes the change (plan §1.3, item 9) — 1–3 lines
under "Unreleased", only for changes a user would notice.

## [Unreleased]

### Added
- API service (`apps/api`) with `GET /healthz`, reporting application version and
  the commit its image was built from.
- Web app (`apps/web`) with a landing page that reports whether the API is
  reachable, along with its version and build commit.
- The seed publishes its ground truth to `ops/seed/truths.json`, so the eval
  harness reads expected answers from the fixture instead of hardcoding them.
- One-command local stack: `make up` starts the platform database, a pizza-chain
  demo database, the API and the web app; `make seed` fills the demo database
  with 18 months of reproducible data.
