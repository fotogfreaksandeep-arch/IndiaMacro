# RBI sectoral-credit history connector contract v1

## Support and dispatch

The connector supports exactly 12 issue months, `2025-07` through `2026-06`.
Archive discovery uses the official RBI Bulletin entry page and exact v1 table
titles. Content is positively dispatched to exactly one verified contract:

| Issues | Layout contract | Current-date basis |
|---|---|---|
| 2025-07–2025-12 | v2 | last reporting Friday |
| 2026-01 | v3 | last reporting Friday |
| 2026-02–2026-04 | v4 | calendar month-end |
| 2026-05 | v5 | calendar month-end |
| 2026-06 | v1 | calendar month-end |

The issue month validates the Bulletin period but does not substitute for
positive structural, taxonomy, and methodology detection. Zero or multiple
matches retain the parser layer's unsupported- or ambiguous-layout errors.

The v2→v3 and v4→v5→v1 boundaries preserve exact taxonomy continuity. The
v3→v4 boundary also preserves taxonomy, but comparability is classified
`COMPARABLE_WITH_DATE_BASIS_CHANGE`: current values change from last reporting
Friday to month-end while the YoY base retains the old reporting-fortnight
definition.

## Observation identities

The published-vintage uniqueness key is the verified parser vintage key plus
`population_id` and `source_table`: dataset, series, observation date, measure,
column role, publication date, population, and source table. Publications are
never deduplicated across release dates.

The economic resolution key is dataset, series, observation date, measure,
unit, and population. Publication and source provenance are deliberately
excluded so that a policy can select among vintages; canonical series,
population, measure, and unit remain to prevent incompatible mixing.

Current observations contain only `CURRENT_OBSERVATION`,
`REPORTED_FINANCIAL_YEAR_GROWTH`, and `REPORTED_YOY_GROWTH` roles. The remaining
outstanding roles remain in `vintages` as published reference observations.

## Cache contract

Manifest schema 1 is immutable. Bundle IDs are SHA-256 hashes of the schema,
issue, parser layout, and sorted table provenance (discovered URL, final URL,
and raw SHA-256). A valid bundle directory must match both its issue and bundle
ID fields. Raw filenames are fixed as `major_sectors.html` and
`industries.html`.

Manifest and table metadata are type-checked. URLs and all redirect hops must
remain on the same approved RBI HTTPS host. Raw bytes are bounded, size- and
hash-checked, and positively reparsed. Reparsed dates, layout, version, contract
signatures, and both release hashes must match the manifest.

Downloads are serial, streamed in 64 KiB chunks, and limited to 5 MiB per page.
Only a fully parsed table pair is written to a temporary issue directory and
atomically renamed. A refresh failure does not return an old cache entry,
although already completed immutable bundles remain available for a later
explicit call.

## Hash contract

Semantic serialization is sorted canonical UTF-8 CSV over the 16 economic and
classification fields listed in the usage guide. Provenance-bound serialization
uses the deterministic complete 20-field release schema. The same routines are
used for release, range, current-view, and resolved-output hashes. Resolved
records are deterministically sorted before hashing.
