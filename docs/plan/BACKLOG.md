# BACKLOG — deferred work (append-only)

Rules (plan §1.5 / §2.3): IDs are `B-001, B-002, …`, append-only, **never renumbered,
never deleted** — a dead item becomes `dropped (reason)`, not a gap.
`Prio` ∈ P1 (blocks V1) / P2 (should fix before V1 ships) / P3 (V1.1+).
`Status` ∈ open / planned / done (PR#) / dropped (reason).
In-code TODOs must cite an ID here: `TODO(B-017): …`. FIXME/HACK/XXX are banned.

| ID | Date | Found during | Title & detail | Suggested phase | Prio | Status |
|----|------|--------------|----------------|-----------------|------|--------|
| B-001 | 2026-08-10 | P0.1 | **Choose a license.** Repo is public with no LICENSE file, so the default is "all rights reserved" — which is a poor fit for a public repo and blocks outside contribution. Decide MIT / Apache-2.0 / proprietary-with-notice, then add `LICENSE`, a README badge, and the SPDX line in `pyproject.toml`. Owner decision, not a code task. | P0 | P3 | open |
| B-002 | 2026-08-10 | P0.2 | **GNU make is absent on the Windows dev host.** CLAUDE.md and the plan use `make` as the developer interface, and CI (ubuntu) has it, but the local host does not — every target has to be run as its raw `uv run --directory apps/api …` equivalent. Fix by installing make (`winget install ezwinports.make`) and noting the prerequisite in README, or by adding a thin `make.ps1`/`Taskfile` shim. Decide before WP0.4, when `make up`/`make seed` become the documented quickstart. | P0 | P2 | open |
| B-003 | 2026-08-10 | P0.3 | **Generate the TypeScript API client from OpenAPI.** `apps/web/src/lib/api-client/` is hand-written with a runtime type guard. Replace with `openapi-typescript` output generated from the FastAPI schema, plus a CI drift check, so a contract change breaks the build instead of production (arch Part 3.1). Pre-seeded by plan WP0.3. | P7 | P2 | open |
| B-004 | 2026-08-10 | P0.3 | **Decide the styling approach before the chat UI.** WP0.3 ships CSS Modules (the Next default) with a small token set in `globals.css`; Tailwind was declined to keep the skeleton minimal. Phase 7 starts real UI, and Phase 11 is a polish pass — pick one then, while there are still only a handful of components to migrate. | P7 | P3 | open |
