# BACKLOG — deferred work (append-only)

Rules (plan §1.5 / §2.3): IDs are `B-001, B-002, …`, append-only, **never renumbered,
never deleted** — a dead item becomes `dropped (reason)`, not a gap.
`Prio` ∈ P1 (blocks V1) / P2 (should fix before V1 ships) / P3 (V1.1+).
`Status` ∈ open / planned / done (PR#) / dropped (reason).
In-code TODOs must cite an ID here: `TODO(B-017): …`. FIXME/HACK/XXX are banned.

| ID | Date | Found during | Title & detail | Suggested phase | Prio | Status |
|----|------|--------------|----------------|-----------------|------|--------|
| B-001 | 2026-08-10 | P0.1 | **Choose a license.** Repo is public with no LICENSE file, so the default is "all rights reserved" — which is a poor fit for a public repo and blocks outside contribution. Decide MIT / Apache-2.0 / proprietary-with-notice, then add `LICENSE`, a README badge, and the SPDX line in `pyproject.toml`. Owner decision, not a code task. | P0 | P3 | open |
