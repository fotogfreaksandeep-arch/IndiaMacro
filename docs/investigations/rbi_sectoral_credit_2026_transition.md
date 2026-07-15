# RBI sectoral-credit 2026 transition report

## Finding

The five January–May 2026 publications require three new parser contracts.

- January is structurally close to v2 and uses the same last-reporting-Friday
  methodology, but has a new exact label-presentation profile and lies outside
  v2's verified interval.
- February–April share one month-end methodology and one four-outstanding role
  model. March's reversed year colspans are the deterministic consequence of a
  December-to-January header crossing; April's comparison-date override and
  exact label profile are bounded release parameters.
- May changes to a six-column financial-year-close representation where one base
  amount serves both FY and YoY comparisons.
- June is already covered by v1 and repeats its financial-year base across two
  source columns.

One new family would be too broad because January and February have different
date methodologies and May has a different source-role structure. Five
month-specific parsers would be too narrow because February–April share the same
methodology, taxonomy, numeric rules, population rules, and semantic column
roles; their exact header and label variants can be validated as named release
parameters. Three is therefore the minimum supported family count justified by
the cached evidence.

## Twelve-release sequence

| Publication | Layout | Current observation | Date regime | Observations |
|---|---|---:|---|---:|
| July 2025 | v2 | 2025-05-30 | Last reporting Friday | 510 |
| August 2025 | v2 | 2025-06-27 | Last reporting Friday | 510 |
| September 2025 | v2 | 2025-07-25 | Last reporting Friday | 510 |
| October 2025 | v2 | 2025-08-22 | Last reporting Friday | 510 |
| November 2025 | v2 | 2025-09-19 | Last reporting Friday | 510 |
| December 2025 | v2 | 2025-10-31 | Last reporting Friday | 510 |
| January 2026 | V3 | 2025-11-28 | Last reporting Friday | 510 |
| February 2026 | V4 | 2025-12-31 | Calendar month-end; prior old fortnight | 510 |
| March 2026 | V4 | 2026-01-31 | Calendar month-end; prior old fortnight | 510 |
| April 2026 | V4 | 2026-02-28 | Calendar month-end; prior old fortnight | 510 |
| May 2026 | V5 | 2026-03-31 | Calendar month-end; prior old fortnight | 425 |
| June 2026 | v1 | 2026-04-30 | Calendar month-end; prior old fortnight | 425 |

The actual dates match the twelve expected cached publications. There are no
duplicate current-observation dates and no deviations from the expected
sequence. Day intervals are 28, 28, 28, 28, 42, 28, 33, 31, 28, 31, and 30.
Those unequal intervals are retained; they are not converted to month-end.

## Counts and roles

Ten releases from July 2025 through April 2026 have four distinct outstanding
roles plus two growth roles: `10 × 85 × 6 = 5,100` observations. May and June
each have three distinct outstanding roles plus two growth roles:
`2 × 85 × 5 = 850`. Total published-vintage observations are therefore 5,950.

Across all releases:

- Outstanding: 3,910
- Financial-year growth: 1,020
- Year-on-year growth: 1,020
- All-SCB Section-42: 210
- Select-SCB SIBC: 5,740
- Unique measure-specific series IDs: 255

The evidence reports 2,040 emitted growth observations but 2,042 growth
reconciliation checks. The additional two checks are deliberate source-level
validations in June's v1 parser: Table 16 repeats the Industry total already
published in Table 15, and v1 checks both its financial-year and year-on-year
growth before the proven duplicate row is collapsed. Those two validations emit
no observations. Thus `2,040 + 2 = 2,042` checks is not an accounting error; it
records the two growth fields on the one collapsed source row.

Role counts are:

- `CURRENT_OBSERVATION`: 1,020
- `FINANCIAL_YEAR_BASE`: 935
- `PRIOR_YEAR_REFERENCE`: 935
- `PRIOR_PERIOD_REFERENCE`: 935
- `FINANCIAL_YEAR_AND_PRIOR_YEAR_REFERENCE`: 85
- `REPORTED_FINANCIAL_YEAR_GROWTH`: 1,020
- `REPORTED_YOY_GROWTH`: 1,020

May's 85 combined-role rows replace separate FY-base and prior-year rows without
inventing an observation. June has separate FY-base and prior-year rows but no
prior-period row after its repeated source base is equality-checked and
collapsed.

## Continuity and comparability

All reviewed mappings across every layout boundary are `EXACT_CONTINUITY` for
taxonomy identity. Comparability is separate:

| Boundary | Taxonomy | Comparability |
|---|---|---|
| v2 → January V3 | Exact | Fully comparable |
| January V3 → February V4 | Exact | Comparable with date-basis change |
| April V4 → May V5 | Exact | Fully comparable; source representation changes |
| May V5 → June v1 | Exact | Fully comparable; duplicate-column representation changes |

The January–February methodology boundary prevents treating the full publication
sequence as an automatically homogeneous economic time series. This evidence is
a vintage-preserving source layer, not a resolved history.

## Deterministic evidence

Generated artifacts are written only to ignored
`spike-artifacts/2026-transition/`. They include all required compatibility,
mapping, growth, continuity, comparability, methodology, current-date, per-release,
combined-vintage, and validation evidence. The builder processes one cached table
pair at a time and performs no network requests.

Validated combined hashes are:

- semantic vintage SHA-256:
  `1c20933973f8652fffdfa4a83e9914fa929bf811c503bd209c430c43d6f6e431`;
- provenance-bound vintage SHA-256:
  `0c14ad8c9110c8a113f329c96726da353afc26ec3192a068564d2571d6d445f1`.

The semantic serialization excludes URLs, raw hashes, parser versions, and
layout identifiers. The provenance serialization includes the complete source
provenance. Neither hash represents a resolved or “latest” time series.
