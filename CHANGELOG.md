# Changelog

## 0.4.0 — 2026-09-29 (local candidate)

- Added a pure Python library and `anchor-plan` CLI alongside the original plugin.
- Added deterministic DAG compilation, strict artifact contracts, required effect
  verifier closure, stable plan digests, and explained mode-based skips.
- Added generic connected-component commit-cohort planning.
- Adapted newer bounded-cohort allocation and exact selective-resume identities to
  arbitrary stable stage names, removing application-specific stage semantics.
- Added generic incremental planning: changed-node descendants, exact cached-input
  bindings, and full-plan fallback when dependency proof or baseline is uncertain.
- Hardened canonical mapping keys, dependency-order determinism, integer budgets,
  manifest membership/bounds and response status validation.
- Preserved legacy CLI/YAML behavior and carried forward its regression tests.
- Added Python packaging, synthetic examples, CI and publication handoff notes.
- Python 3.11+ is required for the packaged library. No automatic workflow/state migration.
