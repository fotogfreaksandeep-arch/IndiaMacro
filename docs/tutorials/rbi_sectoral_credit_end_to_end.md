# RBI sectoral credit: retrieval to reproducible chart

This example uses IndiaMacro's public APIs to retrieve verified RBI Bulletin
publication vintages, select two canonical Non-food Credit series, export their
exact current-release observations, and produce a static chart. Plotting is an
optional downstream layer; auditable data access remains the library's primary
purpose.

## Install

Install the base package for data access:

```bash
python -m pip install indiamacro
```

Install the optional Matplotlib extra only when a chart is needed:

```bash
python -m pip install "indiamacro[plot]"
```

Importing `indiamacro` or `from indiamacro import rbi` does not import
Matplotlib. Calling the plotting helper without the extra raises an actionable
error containing the installation command above.

## First live retrieval

```python
from indiamacro import rbi

history = rbi.sectoral_credit_history(
    start_issue="2025-07",
    end_issue="2026-06",
)
```

The connector discovers Tables 15 and 16 through the official RBI Bulletin
archive, validates one verified parser contract per issue, and commits each
complete table pair to the versioned cache. Requests and parsing are serial and
bounded. A forced refresh is explicit:

```python
history = rbi.sectoral_credit_history(refresh=True)
```

## Offline replay and selection

After a successful retrieval, replay the exact cache without creating a
network session:

```python
history = rbi.sectoral_credit_history(offline=True)

outstanding = history.select(
    series_id="RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING",
    view="current",
)
reported_yoy = history.select(
    series_id="RBI.SECTION42.NON_FOOD_CREDIT.YOY_GROWTH_REPORTED",
    view="current",
)

assert len(outstanding) == len(reported_yoy) == 12
```

The selector preserves RBI observation dates, publication dates, `Decimal`
values, units, population, URLs, raw hashes, and parser methodology fields. It
does not coerce dates to month-end or convert reported growth to floats.

`history.current_observations` contains each release's current outstanding,
reported financial-year growth, and reported YoY growth. `history.vintages`
also retains every reference value and repeated publication. Neither view
silently resolves revisions.

## Explicit resolution

Resolution is a separate, named operation:

```python
latest = history.resolve(policy="latest_publication")
as_known_at_march_end = history.resolve(
    policy="latest_publication",
    as_of="2026-03-31",
)
```

`as_of` excludes every publication after the supplied date. The selected
records retain the publication and source provenance that supplied each value.

## Plot through the public API

```python
figure, axes = rbi.plot_sectoral_credit_history(
    history,
    outstanding_series_id="RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING",
    growth_series_id="RBI.SECTION42.NON_FOOD_CREDIT.YOY_GROWTH_REPORTED",
)
figure.savefig("non_food_credit.svg")
```

The helper joins the two current series on both observation date and
publication date. It plots RBI-reported YoY growth rather than recomputing it.
Outstanding values remain canonically stored in INR crore and are divided by
100,000 only for the labelled INR-lakh-crore display.

The January 2026 Bulletin is in the last-reporting-Friday regime. The February
2026 Bulletin switches current values to calendar month-end while prior-year
comparisons retain RBI's documented reporting-fortnight convention. The chart
annotation is derived from `history.metadata.methodology_boundaries`. It marks
a reporting-date basis change, not a taxonomy break and not an assertion that
the economic meaning necessarily changed.

## Reproducible export

The demonstration script uses only public IndiaMacro APIs:

```bash
python scripts/plot_rbi_sectoral_credit_history.py \
  --start-issue 2025-07 \
  --end-issue 2026-06 \
  --offline \
  --output-dir ./sectoral-credit-demo
```

It writes:

```text
rbi_non_food_credit_history.svg
rbi_non_food_credit_history.png
rbi_non_food_credit_history.csv
rbi_non_food_credit_history_manifest.json
```

The CSV has deterministic column and row order. It records exact observation
and publication dates, outstanding crore and displayed lakh-crore values,
reported YoY growth, population, units, methodology and comparability, and both
selected source URLs.

The manifest records the IndiaMacro version, selected and supported ranges,
semantic current-observation hash, canonical series IDs, publication dates,
official URLs, raw hashes, chart-data and CSV hashes, methodology boundary,
point count, and output filenames. It contains no cache path, username, cookie,
or request header. Its reproducible generation timestamp is anchored to the
latest source-ingestion timestamp and is excluded from semantic hashing.

## Scope and interpretation

Verified historical support currently begins with the July 2025 Bulletin and
ends with the June 2026 Bulletin. That is an IndiaMacro support boundary, not
the full availability of RBI data. The chart covers exact observations from
May 30, 2025 through April 30, 2026. It does not interpolate the 42-day interval,
smooth values, forecast, provide investment guidance, or claim seasonal
adjustment.
