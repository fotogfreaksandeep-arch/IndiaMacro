# IndiaMacro

IndiaMacro is a focused, auditable data-access library for official Indian
macroeconomic data. Version 0.2.0 is a local release candidate covering one
dataset: RBI Sectoral Deployment of Bank Credit from RBI Bulletin Current
Statistics Tables 15 and 16.

Verified historical support currently covers the 12 Bulletin issues from July
2025 through June 2026. This is a tested IndiaMacro boundary, not the full
availability of RBI data.

## Install the local release candidate

Version 0.2.0 has not been published to PyPI. Install from this source checkout:

```bash
python -m pip install .
```

Or, after local release artifacts have been prepared, install the exact wheel:

```bash
python -m pip install ./dist/indiamacro-0.2.0-py3-none-any.whl
```

Matplotlib is optional and is installed only when requested:

```bash
python -m pip install ".[plot]"
# retained local artifact:
python -m pip install "./dist/indiamacro-0.2.0-py3-none-any.whl[plot]"
```

The standard published-package forms are `pip install indiamacro` and
`pip install "indiamacro[plot]"`, respectively, but they do **not** install this
unpublished 0.2.0 release candidate. Use the local commands above for this RC.

## Retrieve current data

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

## Verified publication history

The historical connector covers the 12 verified RBI Bulletin issues from July
2025 through June 2026, inclusively:

```python
from indiamacro import rbi

history = rbi.sectoral_credit_history(
    start_issue="2025-07",
    end_issue="2026-06",
)

vintages = history.vintages
current = history.current_observations

non_food = history.select(
    series_id="RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING",
    view="current",
)
non_food_yoy = history.select(
    series_id="RBI.SECTION42.NON_FOOD_CREDIT.YOY_GROWTH_REPORTED",
    view="current",
)
resolved = history.resolve(policy="latest_publication", as_of="2026-04-30")
```

Historical collections are immutable tuples of frozen records with `Decimal`
values; selection and resolution do not return pandas objects. The complete
range contains 5,950 published-vintage observations and 3,060 current
observations. Each Non-food Credit selection above yields 12 chart-ready
points. The January-to-February 2026 boundary changes the current-date basis
from the last reporting Friday to calendar month-end, so a continuous
publication sequence does not imply unchanged methodology.

See the [historical API guide](docs/usage/rbi_sectoral_credit_history.md) for
cache, offline replay, hash, resolution, and exception details.

![Two-panel RBI Non-food Credit chart showing outstanding and reported YoY growth with the January–February 2026 reporting-date boundary](docs/assets/rbi_non_food_credit_history.svg)

Plotting is an optional downstream demonstration:

```bash
python -m pip install ".[plot]"
python scripts/plot_rbi_sectoral_credit_history.py --offline
```

The [end-to-end tutorial](docs/tutorials/rbi_sectoral_credit_end_to_end.md)
walks through live retrieval, offline replay, selection, explicit resolution,
CSV export, chart generation, and provenance inspection. IndiaMacro's core
purpose remains auditable data access; the chart demonstrates what trustworthy
downstream applications can build on that infrastructure.

## Refresh, offline use, and cache

```python
# Prefer a verified cache; retrieve live only when no compatible bundle exists.
credit = rbi.sectoral_credit()

# Force exact-title live discovery and bounded retrieval.
credit = rbi.sectoral_credit(refresh=True)

# Guarantee no network session is created.
credit = rbi.sectoral_credit(offline=True)

# Replay verified historical issues from their separate history cache.
history = rbi.sectoral_credit_history(
    "2025-07",
    "2026-06",
    offline=True,
)
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

## Vintages and explicit resolution

`history.vintages` retains every published value and reference observation;
repeated publications and later revisions remain separate. The
`history.current_observations` view contains each release's current outstanding
value and RBI-reported growth measures. Neither collection silently chooses a
latest value.

Use `history.resolve(policy="latest_publication")` when a resolved view is
explicitly required. Its optional `as_of="YYYY-MM-DD"` cutoff excludes later
publications while retaining the selected publication and provenance.

## Current limitations

The current connector supports its strict v1 layout. Historical support begins
with the July 2025 Bulletin and ends with the June 2026 Bulletin. Those are
verified support boundaries, not the full availability of sectoral-credit data
from RBI. IndiaMacro does not yet provide other RBI datasets, DBIE integration,
automatic future-layout adaptation, dashboards, forecasting, or a general
Indian macro-data platform. Version 0.2.0 is not yet published to PyPI.

IndiaMacro is licensed under the [MIT License](LICENSE).
