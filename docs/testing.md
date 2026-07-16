# Testing IndiaMacro

IndiaMacro separates tests by the evidence they require. Files under `spike-artifacts/`
may be generated outputs or optional local acceptance inputs, but ordinary tests and
GitHub Actions never require them.

## Portable tests

Portable unit and contract tests use committed compact fixtures, synthetic records, and
mocked responses. They never make live RBI requests and run in every clean checkout:

```bash
ruff check .
pytest -m "not local_evidence and not live" -rs
```

This is the pytest layer run serially in GitHub Actions on Python 3.11 and 3.12.

## Preserved-evidence acceptance tests

Tests marked `local_evidence` replay complete preserved RBI pages, the historical census,
or the 12-release acceptance cache. These inputs remain ignored because they are operational
evidence rather than package source or compact deterministic fixtures. When the evidence is
available in its documented `spike-artifacts/` location, run:

```bash
pytest -m local_evidence -rs
```

To run all available offline tests while still excluding network acceptance:

```bash
pytest -m "not live" -rs
```

An unavailable local-evidence test reports the specific missing evidence instead of making a
network request.

## Live acceptance

Tests marked `live` are opt-in checks against current RBI source behavior. They are excluded
from CI and from the normal offline suite. If live tests are present and network access has been
explicitly authorized, run:

```bash
pytest -m live -rs
```

The standalone current-source acceptance script remains explicitly opt-in:

```bash
python scripts/run_rbi_sectoral_credit_live_acceptance.py
```
