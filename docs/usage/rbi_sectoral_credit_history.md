# RBI sectoral-credit publication history

`rbi.sectoral_credit_history()` retrieves and parses RBI Bulletin Current
Statistics Tables 15 and 16 for an inclusive issue-month range. Verified
support currently begins with the July 2025 Bulletin and ends with the June
2026 Bulletin. This boundary describes tested IndiaMacro support, not the full
historical availability of RBI data.

## Live retrieval and offline replay

```python
from indiamacro import rbi

history = rbi.sectoral_credit_history("2025-07", "2026-06")

# Force fresh official-archive discovery; never fall back to stale cache.
refreshed = rbi.sectoral_credit_history(refresh=True)

# Reparse an already cached range without creating a network session.
replayed = rbi.sectoral_credit_history(offline=True)
```

The source archive is the official RBI ASP.NET Bulletin archive. IndiaMacro
submits its required hidden state, selects each year and month deterministically,
and requires exactly one exact-title link for each table. It does not hardcode
table IDs. Requests and parsing are serial. Each response is streamed in 64 KiB
chunks, bounded at 5 MiB, and restricted to same-host RBI HTTPS redirects.

Cache-root precedence is an explicit `cache_dir`, then
`INDIAMACRO_CACHE_DIR`, then the platform-standard user cache directory. The
history cache is separate from the current connector cache:

```text
<cache-root>/rbi/sectoral_credit_history/
  <issue-month>/<bundle-id>/
    major_sectors.html
    industries.html
    manifest.json
```

Schema-1 manifests bind an immutable bundle to both raw pages, titles, URLs,
byte sizes, raw SHA-256 hashes, parser layout and version, three contract
signatures, publication/current dates, ingestion time, and release hashes.
Both pages are parsed before an atomic directory rename. Every cache read
checks the schema and manifest, recalculates raw hashes, and reparses the HTML.
An incompatible schema raises `CacheIncompatibleError`; corrupt metadata or
bytes raise `CacheIntegrityError`. `HistoryCacheNotFoundError` reports all
missing offline issues in issue order. `refresh=True` never returns stale data
after a failed refresh.

## Published vintages, current observations, and resolution

`history.vintages` retains all values and reference columns as published in
each release. No revision is selected, no older publication is discarded, and
dates are not coerced to month-end. The complete interval contains 5,950
vintage observations with no duplicate vintage keys.

`history.current_observations` is an explicit release view: for each of 85
economic rows and 12 publications it includes current outstanding, reported
financial-year growth, and reported year-on-year growth. It therefore contains
85 × 3 × 12 = 3,060 observations.

Resolution is always explicit:

```python
latest = history.resolve(policy="latest_publication")
as_known = history.resolve(
    policy="latest_publication",
    as_of="2026-03-31",
)
```

The economic resolution key is `(dataset_id, series_id, observation_date,
measure, unit, population_id)`. It excludes publication-specific provenance
but cannot mix populations, units, measures, or canonical series. For each key,
`latest_publication` chooses the most recently published eligible value and
keeps its publication, layout, method regime, URL, and raw hash. `as_of=None`
means the latest publication in the retrieved result, not the wall-clock date.
Same-publication roles must agree exactly; disagreement raises
`ResolutionConflictError` rather than applying a hidden role priority.

All public collections are immutable tuples of frozen dataclasses and retain
canonical `Decimal` values. Selection and resolution do not return pandas
objects.

## Series discovery and chart-ready selection

`history.available_series` lists the canonical measure-specific IDs. For
example:

```python
outstanding = history.select(
    series_id="RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING",
    view="current",
)
yoy = history.select(
    series_id="RBI.SECTION42.NON_FOOD_CREDIT.YOY_GROWTH_REPORTED",
    view="current",
)

assert len(outstanding) == len(yoy) == 12
for point in outstanding:
    print(point.observation_date, point.publication_date, point.value, point.unit)
```

Views are `current`, `vintages`, and `latest_publication`. Optional `start` and
`end` bounds filter exact observation dates and results are chronologically
ordered. Unknown IDs raise `UnknownSeriesError`; unsupported views fail with a
clear `ValueError`.

The January 2026 issue uses the last-reporting-Friday regime. February 2026
switches current observations to calendar month-end while YoY bases retain the
prior reporting-fortnight convention. This boundary is exposed in
`history.metadata.methodology_boundaries`; continuous publication is not a
claim of unchanged methodology.

## Hashes and source manifests

Metadata exposes semantic and provenance-bound hashes for both all vintages
and the current view. Each source manifest exposes both release hashes. A
resolved result exposes its own two hashes.

Semantic hashes use deterministic sorted CSV serialization of dataset, series,
source table/row/label, measure, observation and comparison dates, publication
and Bulletin period, canonical value, unit, population, column role,
provisional status, and footnotes. They exclude URLs, raw hashes, parser
versions, and layout IDs. Provenance-bound hashes use the complete ordered
20-field observation record and therefore include those four provenance fields.
Missing values use the parser contract's canonical marker for semantic hashing
and empty fields for complete-record hashing.

The complete supported semantic vintage hash is
`1c20933973f8652fffdfa4a83e9914fa929bf811c503bd209c430c43d6f6e431`.
Page-chrome changes may change raw and provenance-bound hashes while leaving
the semantic hash unchanged. An offline replay of the exact cache reproduces
all hashes exactly.

Representative failures include `UnsupportedIssueRangeError`,
`HistoryCacheNotFoundError`, `SourceDiscoveryError`, `SourceAccessBlockedError`,
`SourceUnavailableError`, `SourceValidationError`, `CacheIntegrityError`, and
`CacheIncompatibleError`.

The existing `rbi.sectoral_credit()` current connector is unchanged and returns
its current-release DataFrame. The history connector instead models verified
publication vintages. Dates outside July 2025–June 2026, future layouts,
automatic adaptation, and other datasets are not supported.
