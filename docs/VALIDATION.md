# Validation — 2026-09-29

Version: **0.4.0**. Local macOS arm64, Python 3.11.

| Check | Result |
|---|---|
| `python -B -m pytest -q` | 48 passed |
| Wheel and source distribution build | Passed |
| Clean-environment wheel installation without runtime dependencies | Passed |
| Installed `anchor-plan` CLI | Passed; mandatory effect verifier included |
| Installed planning example | Passed; incremental closure, bounded batching and selective resume |
| Extracted plugin archive, pure compiler and legacy scaffolding | Passed |
| Public code dependency/path scan | No private package imports, owner filesystem paths or credential patterns found |
| `git diff --check` | Passed |

The suite covers the original YAML helper's ordering, verification, gates and
completion behavior; strict compiler schema/artifact/DAG checks; deterministic
identity; commit-cohort dependency analysis; 100+ item bounded allocation;
selective retry and drift rejection; incremental invalidation and conservative
fallback; budget/status validation and canonical-key collision rejection.

Run `python scripts/build_plugin.py` and
`python scripts/check_plugin.py dist/anchor.plugin` to verify the distributable
plugin in a clean temporary project. The latter checks archive paths and contents,
the manifest version, the bundled pure compiler and legacy scaffolding.

Linux/Python 3.11, 3.12 and 3.13 [GitHub CI passed](https://github.com/pr9868/anchor-harness/actions/runs/36664843979)
for the implementation merged in [PR #1](https://github.com/pr9868/anchor-harness/pull/1). Planning does not establish live host execution, durable state,
provider correctness, semantic proof quality or approval to perform effects.

`python scripts/check_release.py` checks version agreement, parses all public
Python files and compares the full repository file inventory to
`docs/SOURCE-MANIFEST.json`. After an intentional edit, review it and regenerate
with `--write-manifest` before revalidating or publishing.

## README expansion

The expanded landing page was checked for balanced code fences and valid local
file/section links. Its Python examples ran successfully against installed packages
in a temporary workspace. Anchor's documented legacy-helper init/validate/plan
sequence also passed in a fresh project. GitHub About metadata was checked for
field length and topic syntax and applied to the public repository. Both Mermaid
diagrams rendered on GitHub, and every README section anchor resolved. The source
and built archives passed a separate scan for private imports, owner paths and
credential patterns. Runtime behavior and the recorded test suites are unchanged.
