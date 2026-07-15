# RBI Bulletin sectoral-credit parsing contract, version 1

## Scope

`RBI_BULLETIN_SECTORAL_CREDIT_V1` supports the June 2026 RBI Bulletin HTML
layout for exactly these official tables:

- `15. Deployment of Gross Bank Credit by Major Sectors`
- `16. Industry-wise Deployment of Gross Bank Credit`

The parser accepts the two HTML documents as `bytes` or `str`, plus their
provenance URLs. It performs no network or filesystem access. Each input is
limited to 5 MiB. The parser is intentionally not a live connector and makes
no claim of historical-layout compatibility.

## Discovery and layout identity

The parser locates a unique container table containing the exact normalized
title. Within that container it requires one descendant table with all three
semantic anchors: `(₹ Crore)`, `Outstanding as on`, and `Growth (%)`. It does
not use a DOM position or Bulletin numeric ID. Zero or multiple matches fail.

The semantic layout signature covers the two titles, unit, column roles, the
complete exact row-label allowlists, and required population-note markers.
Whole-page hashes are provenance hashes, not layout identifiers, because RBI
page chrome can change without changing the statistical table.

The table container supplies `Date : Jun 22, 2026`, parsed as publication date
`2026-06-22`. This Bulletin page layout does not print the issue month inside
the table page. For this contract, `bulletin_period` is deterministically the
full month and year of that publication date (`June 2026`). This agrees with
the preserved Bulletin-index evidence. A future layout requiring a different
issue-date relationship needs a new contract.

## Header reconstruction and column roles

Four merged header rows are validated, including all rowspans, colspans,
labels, dates, fiscal-year label, and numbered columns. The major-sector top
header contains RBI's extra blank presentation cell; the lower header levels
and numbered columns still identify all six data columns unambiguously.

| Source column | Semantic role | Canonical treatment |
| --- | --- | --- |
| (1), Mar. 31, 2026 | Outstanding at fiscal-year end | `OUTSTANDING`, `2026-03-31` |
| (2), Apr. 18, 2025 | Prior-year outstanding | `OUTSTANDING`, normally `2025-04-18` |
| (3), Mar. 31, 2026 | Duplicate growth-base outstanding | Assert equal to (1); do not emit again |
| (4), Apr. 30, 2026 | Current outstanding | `OUTSTANDING`, `2026-04-30` |
| %, 2026-27 | Reported financial-year-so-far growth | `FINANCIAL_YEAR_GROWTH_REPORTED`; comparison `2026-03-31` |
| %, 2026 Y-o-Y | Reported year-on-year growth | `YOY_GROWTH_REPORTED`; comparison is the row's column-(2) date |

Amounts use `INR_CRORE`; reported growth uses `PERCENT`. Growth values remain
RBI's reported values and are never replaced by recomputed values.

### Row-specific date override

Table 15's note explicitly says column (2) for Section-42 rows I, II, and III
has reference date `May 2, 2025`. Their prior outstanding observations and
year-on-year comparison dates therefore use `2025-05-02`. All selected-bank
sector and industry rows retain the visible `2025-04-18` date.

## Populations

Population is assigned per mapped row, never per table:

- `ALL_SCBS_SECTION42`: Table 15 rows I Bank Credit, II Food Credit, and III
  Non-food Credit. The note says the Section-42 return covers all scheduled
  commercial banks.
- `SELECT_SCBS_SIBC`: all other emitting rows in Tables 15 and 16. The note
  says the SIBC return covers selected banks accounting for about 95 per cent
  of total non-food credit.

Both population statements and the post-December-2025 reporting-method note
are mandatory layout evidence. Their absence fails parsing.

## Explicit rows, hierarchy, and identifiers

Every supported source label has a hand-written mapping in
`MAJOR_ROW_SPECS` or `INDUSTRY_ROW_SPECS`; labels are not slugified at runtime.
The mappings preserve `source_row_code`, `parent_row_code`, up to three named
hierarchy levels, population, memorandum status, and footnote references.
Table 15 contains 44 mapped rows (43 emitting rows plus the structural
`5. Priority Sector (Memo)` heading). Table 16 contains 43 mapped rows.

Table 15's industry total and Table 16's `2. Industries (2.1 to 2.19)` are the
same economic row. All six source values must agree. The Table 15 copy is
canonical and the Table 16 copy is recorded as one collapsed duplicate source
row, yielding 42 emitting Table 16 rows. All other unknown, missing, reordered,
or duplicated labels fail rather than acquiring inferred identities.

Series IDs are deterministic combinations of population namespace, stable
base ID, and measure:

- `RBI.SECTION42.<BASE_ID>.<MEASURE>`
- `RBI.SIBC.<BASE_ID>.<MEASURE>`

Examples are `RBI.SECTION42.BANK_CREDIT.OUTSTANDING` and
`RBI.SIBC.INDUSTRY.INFRASTRUCTURE.ROADS.YOY_GROWTH_REPORTED`.

Table 15 priority-sector rows `(i)` through `(x)` are emitted with
`is_memorandum=true` beneath the non-emitting row-code `5` heading. Other rows
have `is_memorandum=false`.

## Labels, footnotes, notes, and provisional status

`source_label` retains the exact normalized RBI label. Superscripts and the
plain numeric footnote markers used in the source are separately mapped to a
JSON-array string in `footnote_references` (for example `["1"]`); the stable
hierarchy label excludes the marker. Both complete normalized source-note
blocks are returned as immutable `SourceNote` values. The parser does not
rewrite their methodology. Because the required note says the data are
provisional, every observation has `is_provisional=true`.

## Numeric and missing-value policy

Numbers may have an optional sign, standard comma thousands separators, and a
decimal part. They are parsed as `Decimal`. The exact markers empty string,
`-`, `–`, `—`, `..`, `...`, `NA`, and `N.A.` map to missing values. Any other
non-numeric token is a validation error. Missing values remain explicit rows
with a null `value`; growth reconciliation is recorded as skipped when a
required operand is missing or its comparison amount is zero.

## Canonical schema and ordering

The long-form schema is:

`dataset_id, series_id, source_table, source_row_code, source_label,
parent_row_code, sector_level_1, sector_level_2, sector_level_3, measure,
observation_date, comparison_date, value, unit, population_id,
publication_date, bulletin_period, is_provisional, is_memorandum,
footnote_references, source_url, source_sha256, parser_version, layout_id`.

Rows follow source table order (Table 15, then nonduplicate Table 16), explicit
mapping order, and within each economic row: prior outstanding, fiscal-year-end
outstanding, current outstanding, financial-year growth, and year-on-year
growth. CSV uses UTF-8, LF endings, a fixed header, stable decimal formatting,
lowercase booleans, and empty fields for nulls.

The minimum canonical key is enforced as
`(dataset_id, series_id, observation_date, publication_date)`. A repeated key
is a hard validation failure.

## Source, semantic, and provenance hashes

Each parsed result keeps the exact SHA-256 of both input HTML byte strings.
These raw-source hashes identify a byte-level source version and change for any
source-page mutation, including page chrome outside the economic table.

`semantic_observations_sha256` identifies the economic observations without
transport or implementation provenance. Its fixed inclusion order is:

1. `dataset_id`
2. `series_id`
3. `source_table`
4. `source_row_code`
5. `source_label`
6. `parent_row_code`
7. `sector_level_1`
8. `sector_level_2`
9. `sector_level_3`
10. `measure`
11. `observation_date`
12. `comparison_date`
13. `value`
14. `unit`
15. `population_id`
16. `publication_date`
17. `bulletin_period`
18. `is_provisional`
19. `is_memorandum`
20. `footnote_references`

The semantic hash excludes `source_url`, `source_sha256`, `parser_version`, and
`layout_id`, as well as connector-only retrieval timestamps, cache paths, and
bundle IDs. Rows are rendered to UTF-8 CSV in the fixed column order and then
sorted lexicographically by the complete rendered row, making DataFrame index
and input row order irrelevant. Line endings are LF. Missing values are the
literal `\N`; booleans are lowercase `true`/`false`; `Decimal` values use fixed
non-exponent notation; dates remain ISO strings. The header is included in the
hash input.

`provenance_bound_output_sha256` is the SHA-256 of the complete 24-column
canonical CSV in parser-defined canonical row order. It includes the four
provenance columns excluded above, so a source URL, raw-page hash, parser
version, layout, or economic-data change changes this identity. It excludes
ephemeral connector state such as retrieval time, cache location, temporary
paths, and in-memory object identity.

Thus a page-chrome-only change alters the raw hashes and provenance-bound hash
but leaves the semantic observations hash unchanged.

## Validation and failure behavior

The parser validates exact titles; unit; publication date; merged header
structure; all date and column roles; required notes; the Section-42 override;
complete explicit row coverage; numeric syntax; structural blank rows;
column-(1)/(3) equality; cross-table industry equality; population assignment;
canonical-key uniqueness; deterministic serialization; and growth arithmetic.

For every available growth value it recomputes
`100 × (current / comparison − 1)` and requires absolute disagreement no
greater than `0.11` percentage points, allowing RBI's one-decimal rounding.
Reported values remain canonical.

Failures raise a specific exception rather than returning an empty frame:

- `UnsupportedLayoutError` for missing semantic structure or required notes;
- `AmbiguousTableError` for multiple semantic matches;
- `UnmappedSeriesError` for unknown or missing rows;
- `DataValidationError` for malformed or inconsistent values and duplicate
  canonical keys.

## Freshness limitation

This source reflects the RBI Bulletin publication cadence and may lag the
dedicated Sectoral Deployment release. Version 1 supports only the demonstrated
June 2026 structure. Live discovery, retrieval, caching, historical ingestion,
and choosing the freshest official release are explicitly outside this parser.
