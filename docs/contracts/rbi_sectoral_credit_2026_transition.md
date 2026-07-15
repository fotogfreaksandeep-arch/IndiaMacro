# RBI sectoral-credit 2026 transition contracts

## Scope

This contract covers the cached official RBI Bulletin Table 15/Table 16 pairs
published from January through May 2026. It also defines positive dispatch at
the boundaries with the July–December 2025 v2 family and the June 2026 v1
family. It produces publication-vintage staging observations only. It does not
resolve vintages or change the public `rbi.sectoral_credit()` connector.

Three new parser contracts are required by the evidence:

| Layout ID | Exact publication support | Source structure | Methodology |
|---|---|---|---|
| `RBI_BULLETIN_SECTORAL_CREDIT_V3_2026_01_LAST_FRIDAY` | January 2026 | Four outstanding columns and two reported-growth columns | Last reporting Friday |
| `RBI_BULLETIN_SECTORAL_CREDIT_V4_2026_02_04_MONTH_END` | February–April 2026 | Four outstanding columns and two reported-growth columns; date-driven year-header grouping | Calendar month-end current value with prior-year old-fortnight comparison |
| `RBI_BULLETIN_SECTORAL_CREDIT_V5_2026_05_FY_CLOSE` | May 2026 | Three outstanding columns and two reported-growth columns | Same month-end regime; one source base serves FY and YoY comparisons |

The contracts make no claim outside those exact publications.

## Positive detection

Detection first inspects normalized semantic titles, publication dates, unit
span, header cells, merged-cell structure, exact supported label presentation,
normalized taxonomy, and methodology notes. Publication period selects a bounded
set of eligible specifications, but period alone never proves a match.

All candidates require:

- exact Tables 15 and 16 semantic titles and ordinals;
- matching table publication dates and Bulletin periods;
- INR crore units;
- exact supported header and source-column shapes;
- complete, ordered, explicit labels for that release;
- 44 major rows and 43 industry rows;
- required Section-42/all-SCB and SIBC/select-SCB population wording;
- approximately 95 per cent SIBC coverage;
- provisional status; and
- the family-specific reporting methodology marker.

Exactly one candidate must match. Zero matches raise an unsupported-layout
error; multiple matches raise an ambiguous-layout error. Parser failures are
not used as dispatch signals.

## Family V3: January 2026

V3 retains v2's seven-column table, five header rows, plain `1`–`4` amount
column numbers, four outstanding values, and two reported growth values. Its
exact labels use plain footnote numbers for the first three major-sector notes
and omit the dot after the industry-total code. These are explicit presentation
changes, not inferred aliases.

The six staging roles per economic row are:

1. `FINANCIAL_YEAR_BASE`;
2. `PRIOR_YEAR_REFERENCE`;
3. `PRIOR_PERIOD_REFERENCE`;
4. `CURRENT_OBSERVATION`;
5. `REPORTED_FINANCIAL_YEAR_GROWTH`; and
6. `REPORTED_YOY_GROWTH`.

The current and comparison observations remain on the last-reporting-Friday
basis. The v2-to-V3 boundary is therefore `EXACT_CONTINUITY` for taxonomy and
`FULLY_COMPARABLE` for observation methodology.

## Family V4: February–April 2026

V4 retains four outstanding and two growth values, but requires the source note
that, from December 31, 2025, the current observation is calendar month-end
while the year-on-year comparison remains the corresponding old-definition last
reporting fortnight. It preserves the published dates exactly.

February and April group the prior-year date under one year cell and the two
current-year dates under a two-column year cell. March's December prior-period
and January current dates cross a calendar-year boundary, so the first year cell
spans two columns and the second spans one. V4 accepts both exact merged-cell
forms only when the expanded years produce the three published dates in their
semantic order. This is a bounded date-driven header parameter, not a relaxed
header parser. February publishes `Growth(%)`, while March and April publish
`Growth (%)`; the exact spelling is pinned to each supported release.

April's major rows I, II, and III override the visible February 21, 2025 prior
date with March 7, 2025. The note text, referenced source column, visible date,
and replacement date are all validated. SIBC rows retain February 21.

The January-to-February boundary remains `EXACT_CONTINUITY` for series identity
but is `COMPARABLE_WITH_DATE_BASIS_CHANGE`: a continuous publication sequence
does not erase the switch from reporting Friday to calendar month-end.

## Family V5: May 2026 financial-year close

May has a six-column table: one label plus five values. The unit and terminal
note span six columns; Outstanding spans three and Growth spans two. Source
columns are numbered `1`–`5`.

The first outstanding source column is simultaneously the financial-year base
and prior-year comparison base. One source amount is therefore emitted once
with the explicit combined role
`FINANCIAL_YEAR_AND_PRIOR_YEAR_REFERENCE`. The other roles are
`PRIOR_PERIOD_REFERENCE`, `CURRENT_OBSERVATION`, and the two reported growth
roles. This produces five observations per economic row without inventing a
second source amount.

For major rows I, II, and III, the visible March 21, 2025 combined base is
replaced by the documented April 4, 2025 Section-42 reference. SIBC rows retain
March 21. Both reported growth values are checked against the same published
base and current amount.

The V4-to-V5 boundary is `EXACT_CONTINUITY` and `FULLY_COMPARABLE`: methodology
is unchanged, while the source representation contracts naturally at the
financial-year close.

## June 2026 v1 boundary

June remains `RBI_BULLETIN_SECTORAL_CREDIT_V1`. It uses four outstanding source
columns, but columns 1 and 3 repeat the financial-year-base date and value. V1
validates equality and collapses them, leaving
`FINANCIAL_YEAR_BASE`, `PRIOR_YEAR_REFERENCE`, `CURRENT_OBSERVATION`, and two
growth roles. The Section-42 prior-year override remains May 2, 2025.

The V5-to-v1 boundary is `EXACT_CONTINUITY` and `FULLY_COMPARABLE`. The
month-end/mixed-prior-fortnight methodology is unchanged; only source-column
encoding changes.

## Taxonomy, hierarchy, and duplicates

Every supported release maps 44 Table 15 rows and 43 Table 16 rows. Table 15 row
`5`, “Priority Sector (Memo)”, is an explicit non-emitting structural heading.
The Table 16 Industry total is validated against Table 15 row `2` across every
source value and then collapsed. Thus `87 - 1 - 1 = 85` economic rows remain.

All 87 mappings retain the reviewed v1 base identity and are classified
`EXACT_CONTINUITY`. Parent codes, hierarchy, memorandum status, population, and
footnote references are code-owned. Unknown, missing, duplicated, reordered, or
unexpectedly populated rows fail.

Taxonomy continuity and observation comparability are independent fields. Exact
taxonomy identity does not override the January–February date-basis change.

## Populations, notes, and growth

Major rows I, II, and III use the all-SCB Section-42 population. Other emitting
rows use the select-SCB SIBC population covering about 95 per cent of non-food
credit. Full source notes and exact footnote references remain attached to each
parsed release; all observations are provisional.

Financial-year and year-on-year growth are reconciled from their corresponding
published outstanding values using the established 0.11 percentage-point RBI
rounding tolerance. Reported growth is preserved. Malformed values, unexplained
missing values, wrong comparison dates, cross-table disagreement, or growth
outside tolerance fail explicitly.

## Published-vintage schema and duplicate handling

The common 20-field v2 release schema is retained. Its semantic vintage hash
excludes source URLs, raw hashes, parser version, and layout ID. The
provenance-bound hash includes the complete 20-field serialized rows.

Vintage uniqueness additionally includes population and source table along with
dataset, series, observation date, measure, role, and publication date. Values
published again or revised in a later Bulletin remain separate records. Only
proven within-release duplicate columns and the cross-table Industry row are
collapsed. No gaps are filled, dates normalized, values interpolated, or latest
vintages selected.

## Known incompatibilities and non-support

- V3 cannot parse the v2 exact label presentation without an explicit contract.
- V4 requires the month-end transition note and accepts only its three named
  publications.
- V5 is the only supported six-column financial-year-close representation.
- V1's repeated financial-year column is not accepted as V5.
- A publication sequence is not treated as proof of economic comparability.
- No live archive retrieval, historical API, vintage resolution, or public
  connector change is part of this contract.
