# Public Anchor repository

Keep planning pure and organization-neutral. Preserve the legacy plugin CLI and
YAML schema unless a migration is explicitly designed. New planning APIs use
Python 3.11+ and depend only on the standard library.

Before handing off a release, run `python -B -m pytest -q`, build/install a wheel,
run the installed CLI and example, and build/inspect the plugin archive. Keep
`pyproject.toml`, plugin manifest, package version, README and changelog aligned.
No provider calls, application state or private dependencies belong in this repo.
