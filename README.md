# IndiaMacro

IndiaMacro v0.1.0 is a focused proof of concept for programmatic access to one
official Indian macroeconomic dataset: RBI Sectoral Deployment of Bank Credit.
It discovers and parses RBI Bulletin Current Statistics Tables 15 and 16.

## Quick start

```python
from indiamacro import rbi

credit = rbi.sectoral_credit()

print(credit.observations.head())
print(credit.metadata.latest_observation_date)
print(credit.metadata.freshness_status)
print(credit.metadata.semantic_observations_sha256)
```

The result exposes a long-form pandas DataFrame as `credit.observations`, typed
release and provenance metadata as `credit.metadata`, and the two complete RBI
methodology notes as `credit.notes`.

The Bulletin can lag RBI's dedicated monthly Sectoral Deployment release.
`latest_observation_date` is the latest date represented by the returned data,
while `freshness_status` reports the comparison with the dedicated release
index. IndiaMacro never requests the dedicated release's blocked XLSX file.

## Refresh, offline use, and cache

```python
# Prefer a verified cache; retrieve live only when no compatible bundle exists.
credit = rbi.sectoral_credit()

# Force exact-title live discovery and bounded retrieval.
credit = rbi.sectoral_credit(refresh=True)

# Guarantee no network session is created.
credit = rbi.sectoral_credit(offline=True)
```

An explicit `cache_dir` may be passed to any call. Otherwise
`INDIAMACRO_CACHE_DIR` is used when set, followed by the platform-standard user
cache directory. Raw HTML pages and a versioned manifest are committed only
after both tables validate and parse. Every cache read recalculates raw and
output hashes. Missing, incompatible, or corrupt caches raise specific errors;
the API never returns an empty DataFrame as an error substitute.

Pre-release manifest schema 1 bundles used the misleading field
`normalized_output_sha256`. v0.1.0 classifies those bundles as incompatible.
Run an online call to create a schema 2 bundle, or remove the obsolete bundle.
Immutable old bundles are not rewritten.

## Data and provenance identities

IndiaMacro distinguishes three hash concepts:

- `major_sectors_sha256` and `industries_sha256` identify the exact raw HTML
  bytes. RBI page-chrome changes therefore change these hashes.
- `semantic_observations_sha256` identifies sorted normalized economic data
  and classifications, excluding transport and implementation provenance. It
  remains stable across page-chrome, source-URL, DataFrame-index, and row-order
  changes.
- `provenance_bound_output_sha256` identifies the complete canonical output,
  including source URLs, raw source hashes, parser version, and layout ID. It
  changes when either the economic data or provenance changes.

The exact columns and serialization rules are documented in
[`docs/contracts/rbi_sectoral_credit_bulletin_v1.md`](docs/contracts/rbi_sectoral_credit_bulletin_v1.md).

## Current limitations

Only `RBI_BULLETIN_SECTORAL_CREDIT_V1` is supported. IndiaMacro does not yet
provide historical ingestion, archive compatibility, other RBI datasets,
DBIE integration, forecasting, or a general Indian macro-data platform.

IndiaMacro is licensed under the [MIT License](LICENSE).
