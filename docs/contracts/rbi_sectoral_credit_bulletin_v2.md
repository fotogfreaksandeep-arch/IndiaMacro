# RBI sectoral-credit Bulletin parser v2 contract

## Scope and boundary

`RBI_BULLETIN_SECTORAL_CREDIT_V2_2025H2` supports exactly the RBI Bulletin
Table 15/Table 16 pairs published in July, August, September, October, November,
and December 2025. It is an offline parser for caller-supplied HTML. It does not
discover, retrieve, resolve, or expose a historical series through the public
API. The existing v1 snapshot parser and `rbi.sectoral_credit()` connector remain
unchanged.

The staging output represents a publication vintage. Two identical economic
observations published on different dates remain separate records.

## Compatibility decomposition

The six cached table pairs have the same three contract signatures:

| Contract level | 2025H2 SHA-256 | June 2026 v1 SHA-256 | Result |
|---|---|---|---|
| Structural layout | `bf8b1e0070ad02fb564c08136865ab22640c7b426b309060fb89bd9aa338eeed` | `26c0828a1e14d835455ff269e9825e5538735c5715ec211c45d1f856cfca3c94` | different |
| Taxonomy schema | `399f1e78983ac95d475e6c2818c5ee32ebf35ddb932a6451dcc377c19d03a274` | `399f1e78983ac95d475e6c2818c5ee32ebf35ddb932a6451dcc377c19d03a274` | exact normalized continuity |
| Methodology regime | `7a7b6424ea67f4a4ebaf0e8ac92bfb7de502c052ca0984387aa286c3d79932bd` | `e5792073826c054123591ed6bce9876dd9481bc1945ff1c24c19add4fb5e3882` | different reporting convention |

Structural signatures cover the nested data-table structure, five header rows,
rowspan/colspan arrangements, six value-column roles, terminal seven-column
notes row, core row counts, and the explicitly recognized supplemental and empty
row shapes. They exclude dates, values, page chrome, publication month, and table
ordinal. Taxonomy signatures cover ordered row codes, normalized labels,
hierarchy, and memorandum structure. Methodology signatures cover INR crore,
Section-42/all-SCB and SIBC/select-SCB populations, approximately 95 per cent
coverage, provisional status, and reporting-date convention.

July and August contain merger-exclusion amounts in parentheses. August Table 16
uses a two-row span for the Industry total plus its parenthetical amount, followed
by an empty spacer. Those rows are validated and counted as non-emitting release
evidence. September through December omit them. December has a row-specific
Section-42 prior-year override from October 18, 2024 to November 1, 2024. These
are release parameters, so they do not split the six releases into separate
parser families.

## Why v1 rejects 2025H2

The rejection is deterministic and occurs before numeric parsing:

- v1 searches for the exact header `Growth (%)`; 2025H2 publishes `Growth(%)`;
- v1 Table 15 expects Outstanding `colspan=3`, Growth `colspan=2`, and a trailing
  blank header cell; 2025H2 uses Outstanding `colspan=4` with no blank cell;
- v1 expects parenthesized source-column numbers, while 2025H2 publishes plain
  `1` through `4`;
- v1 interprets columns (1) and (3) as a repeated date and requires equality;
  2025H2 gives them distinct financial-year-base and prior-period dates;
- v1 requires the last-day-of-month regime, while 2025H2 states last reporting
  Friday;
- v1 exact source-label mappings do not accept 2025H2 punctuation and superscript
  variants even though the normalized taxonomy is continuous.

The shared low-memory HTML engine and numeric policy are reused. Separate,
positive v1 and v2 signature specifications select a parser; failure of one
parser is never used as dispatch logic. Zero matches and multiple matches are
hard failures.

## Structural layout and dates

Both v2 tables contain a seven-column unit row, a four-level semantic header,
44 major-sector or 43 industry taxonomy rows, optional recognized supplemental
rows, and one terminal notes row. Every economic row has a label and six source
values:

1. outstanding at the financial-year base date;
2. outstanding at the prior-year reference date;
3. outstanding at the prior-period reference date in the current year;
4. outstanding at the current observation date;
5. RBI-reported financial-year growth, comparing columns 1 and 4;
6. RBI-reported year-on-year growth, comparing columns 2 and 4.

When a layout repeats a date in outstanding columns, values must agree and one
role/date observation is emitted. In 2025H2 all four outstanding dates are
distinct. For December's major Table 15 rows I, II, and III only, the published
Section-42 override replaces the visible prior-year date in both the outstanding
vintage and year-on-year comparison date. SIBC rows retain the visible header
date.

The source unit is `INR_CRORE`; reported growth is `PERCENT`. Numeric missing
markers follow the existing v1 policy. Malformed numerics, conflicting repeated
columns, or growth outside RBI's 0.11 percentage-point rounding tolerance fail.
RBI's reported growth is preserved rather than replaced by the recomputation.

## Taxonomy and continuity

The code-owned mapping contains all 44 Table 15 rows and all 43 Table 16 rows.
All 87 map as `EXACT_CONTINUITY` to the same v1 base series ID after explicit
review of code, normalized label, hierarchy, population, definition notes, and
memorandum status. Counts for likely/review, definition-changed, new,
discontinued, and unresolved are zero. A non-exact classification is forbidden
from reusing a v1 ID.

The Industry total occurs in both source tables. Its six values must agree; the
Table 16 copy is then collapsed. Table 15 has 43 emitting rows, Table 16 has 43,
and the cross-table collapse leaves 85 economic rows. Unknown, missing,
duplicated, reordered, or unexpectedly populated structural rows fail.

The mapping-count reconciliation is therefore exact: 44 Table 15 mappings plus
43 Table 16 mappings give 87 source rows. Table 15 row `5`, “Priority Sector
(Memo)”, is a deliberately non-emitting structural heading with six required
blank value cells, leaving 86 value-bearing source rows. Table 16 row `2`,
“Industries (2.1 to 2.19)”, is the proven duplicate of Table 15 row `2`,
“Industry (Micro and Small, Medium and Large)”; all six values and the shared
base-series identity are validated before the Table 16 copy is suppressed. This
leaves 85 economic rows. No other row is suppressed, so no economic series is
lost. Each row has three measure-specific series IDs—outstanding,
financial-year growth, and year-on-year growth—giving `85 × 3 = 255` unique
series IDs.

## Methodology and release parameters

Rows I, II, and III use the all-scheduled-commercial-bank Section-42 population.
Other emitting rows use the select-bank SIBC population. The required source
wording says the latter accounts for about 95 per cent of non-food credit and
uses the last reporting Friday of the month. Data are provisional.

The following remain release parameters: publication date, Bulletin period,
current date, prior-year date, prior-period date, financial-year-base date,
Section-42 override, actual reporting-bank count if stated, actual coverage,
table ordinal, official source URL, raw SHA-256, supplemental-row presence, and
page chrome. Exact Tables 15 and 16 ordinals are still validated during a real
parse even though ordinals do not affect structural identity.

## Release-observation schema

The 20 fields, in order, are:

`dataset_id`, `series_id`, `source_table`, `source_row_code`, `source_label`,
`measure`, `observation_date`, `comparison_date`, `publication_date`,
`bulletin_period`, `value`, `unit`, `population_id`, `column_role`, `layout_id`,
`parser_version`, `source_url`, `source_sha256`, `is_provisional`, and
`footnote_references`.

The six roles are `FINANCIAL_YEAR_BASE`, `PRIOR_YEAR_REFERENCE`,
`PRIOR_PERIOD_REFERENCE`, `CURRENT_OBSERVATION`,
`REPORTED_FINANCIAL_YEAR_GROWTH`, and `REPORTED_YOY_GROWTH`. The vintage key is
unique over dataset, series, observation date, measure, column role, and
publication date.

Each verified release emits 510 observations: 85 economic rows times four
outstanding roles and two reported-growth roles. Across six publications the
unresolved, uncollapsed staging evidence contains 3,060 rows and 255 unique
measure-specific series IDs.

The column-role reconciliation is `85 × 6 = 510` observations per release:

- 85 `FINANCIAL_YEAR_BASE` outstanding observations;
- 85 `PRIOR_YEAR_REFERENCE` outstanding observations;
- 85 `PRIOR_PERIOD_REFERENCE` outstanding observations;
- 85 `CURRENT_OBSERVATION` outstanding observations;
- 85 `REPORTED_FINANCIAL_YEAR_GROWTH` observations; and
- 85 `REPORTED_YOY_GROWTH` observations.

Across six releases, each role therefore has `85 × 6 = 510` observations. The
four outstanding roles give `4 × 510 = 2,040` outstanding observations, while
the two growth roles give 510 financial-year and 510 year-on-year observations.
The complete arithmetic is `2,040 + 510 + 510 = 3,060`. A repeated-date source
column would be equality-checked and collapsed, but the four outstanding dates
are distinct in all six supported 2025H2 releases.

## Failure behaviour and limitations

Parsing requires official RBI HTTPS URLs, exact supported publication periods,
matching pair dates and headers, positive structural/taxonomy/methodology
signatures, complete mappings, expected population notes, valid dates and
numbers, cross-table agreement, unique vintage keys, and reconciled growth.
Unsupported pages fail closed; there is no fallback parse.

The contract makes no support claim before July 2025 or after December 2025. It
does not retrieve archive pages, ingest incrementally, select a latest vintage,
construct a resolved history, change the public snapshot schema, provide an API,
or generate charts. Generated evidence and raw pages remain ignored under
`spike-artifacts/`.
