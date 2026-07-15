# RBI sectoral-credit historical Bulletin census

## 1. Executive conclusion

Status: **PASS_CENSUS**.

The official RBI Bulletin archive can be selected deterministically through its public ASP.NET form. The bounded census inspected the hard maximum of 40 Bulletin issues using 113 live HTTP attempts, with no access-control response, retrieval failure, redirect outside RBI, or ambiguous table match.

The directly embedded HTML pair is verified from January 2013 onward in the boundary sample; December 2012 and the March 2010–2012 annual samples do not contain either target table. Every present pair retained the exact published titles and numbers 15 and 16 used today.

Parser `RBI_BULLETIN_SECTORAL_CREDIT_V1` passes only June 2026. May 2026 is an adjacent, verified failure, so the largest fully verified contiguous v1 interval is the single month June 2026. No earlier month should be described as v1-compatible. The most defensible first parser-v2 implementation window is July–December 2025: all six monthly issues were inspected and share one semantic layout signature.

## 2. Census status

- Completion status: `PASS_CENSUS`
- Branch: `research/historical-layout-census`
- Base, `origin/main`, local `main`, and peeled `v0.1.0`: `d47ce76d6404454b961ff74c44196a0f3f612ccf`
- Commit, push, tag, or release created: no
- Production parser, connector, cache contract, public API, and version files changed: no
- Source controls encountered: none

## 3. Request and time budget

- Live HTTP attempts: 113 of 120.
- Issues inspected: 40 of 40.
- Responses: streamed sequentially in 64 KiB chunks with a 5 MiB per-response cap.
- Request interval: at least 0.75 seconds.
- Processes/workers: one process, no parallel workers, browser automation, containers, PDFs, XLSX files, or archive crawl.
- Cumulative cache hits during development and final cache-only verification: 1,047. The first 37-issue live pass used 7 cache hits; the final 40-issue rerun was fully cache-backed.
- Raw HTML and compact outputs are under ignored `spike-artifacts/historical-census/`.

The exact live-attempt count is persistent across reruns; cache-only reruns did not increase it.

## 4. Official archive discovery method

The official entry point is `https://rbi.org.in/Scripts/BS_ViewBulletin.aspx`.

The page publishes year/month anchors whose public JavaScript calls `GetYearMonth(year, month)`. That function writes the selected values to `hdnYear` and `hdnMonth`, resets `ddlSubSection`, and clicks the form's hidden submit control. The census reproduces this ordinary form submission by POSTing to `./BS_ViewBulletin.aspx` with:

- `hdnYear=<YYYY>`;
- `hdnMonth=<1-12>`;
- `ddlSubSection=0`;
- the hidden submit control `UsrFontCntr$btn`;
- normal page-provided `__VIEWSTATE`, `__VIEWSTATEGENERATOR`, and `__EVENTVALIDATION` state.

The six required anchors—January 2026 and June 2025, 2024, 2023, 2022, and 2020—each returned the requested Bulletin period and one unique official HTML link for each target table. Numeric table IDs are outputs of discovery only; no IDs were guessed or treated as the discovery mechanism.

Every discovered table page was `https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=...`. PDF and XLSX links on `rbidocs.rbi.org.in` were not requested.

## 5. Exact sample schedule

The 37-issue initial plan was written before full table retrieval. Three boundary issues were then added, reaching the 40-issue cap.

- Older annual: 2010-03, 2011-03, 2012-03, 2013-03, 2014-03, 2015-03, 2016-03, 2017-03, 2018-03, 2019-03, 2020-03, 2021-03, 2022-03, 2023-03.
- Anchors not already represented by another category: 2020-06, 2022-06, 2023-06, 2024-06, 2025-06.
- Intermediate quarterly: 2023-09, 2023-12, 2024-03, 2024-09, 2024-12, 2025-03. June 2024 and June 2025 are present as anchors.
- Recent monthly: every month from 2025-07 through 2026-06. January 2026 is also the required anchor.
- Boundary expansion: 2012-12, 2013-01, 2013-02.

The schedule is deterministic for the latest archive period, deduplicates overlapping anchors, and remains bounded before retrieval.

## 6. Issue-by-issue results

For present pairs, “15” and “16” mean the exact titles `15. Deployment of Gross Bank Credit by Major Sectors` and `16. Industry-wise Deployment of Gross Bank Credit`. Full URLs, redirects, content types, byte sizes, raw hashes, raw/normalized header grids, notes, labels, and comparison dates are in `census.json`.

| Bulletin period | Publication date | Major table | Industry table | Layout signature | V1 result | Observation date | Notes |
|---|---|---|---|---|---|---|---|
| 2010-03 | 2010-03-10 | — | — | `—` | MISSING_TABLE_PAIR | — | Pair absent |
| 2011-03 | 2011-03-10 | — | — | `—` | MISSING_TABLE_PAIR | — | Pair absent |
| 2012-03 | 2012-03-12 | — | — | `—` | MISSING_TABLE_PAIR | — | Pair absent |
| 2012-12 | 2012-12-10 | — | — | `—` | MISSING_TABLE_PAIR | — | Pair absent |
| 2013-01 | 2013-01-11 | 15 | 16 | `218b36e84961` | NEW_LAYOUT | 2012-11-30 | `UnsupportedLayoutError` |
| 2013-02 | 2013-02-11 | 15 | 16 | `218b36e84961` | NEW_LAYOUT | 2012-12-28 | `UnsupportedLayoutError` |
| 2013-03 | 2013-03-11 | 15 | 16 | `218b36e84961` | NEW_LAYOUT | 2012-12-28 | `UnsupportedLayoutError` |
| 2014-03 | 2014-03-10 | 15 | 16 | `47ac71419722` | NEW_LAYOUT | 2014-01-24 | `UnsupportedLayoutError` |
| 2015-03 | 2015-03-10 | 15 | 16 | `98bf4ae1c749` | NEW_LAYOUT | 2015-01-23 | `UnsupportedLayoutError` |
| 2016-03 | 2016-03-10 | 15 | 16 | `0ef1d2ffbb86` | NEW_LAYOUT | 2016-01-22 | `UnsupportedLayoutError` |
| 2017-03 | 2017-03-10 | 15 | 16 | `ffab783abd92` | NEW_LAYOUT | 2017-01-20 | `UnsupportedLayoutError` |
| 2018-03 | 2018-03-10 | 15 | 16 | `364e5b23ae7b` | NEW_LAYOUT | 2018-01-19 | `UnsupportedLayoutError` |
| 2019-03 | 2019-03-12 | 15 | 16 | `364e5b23ae7b` | NEW_LAYOUT | 2019-01-18 | `UnsupportedLayoutError` |
| 2020-03 | 2020-03-11 | 15 | 16 | `e164176cf7c6` | NEW_LAYOUT | 2020-01-31 | `UnsupportedLayoutError` |
| 2020-06 | 2020-06-10 | 15 | 16 | `ebddd3eb273d` | NEW_LAYOUT | 2020-04-24 | `UnsupportedLayoutError` |
| 2021-03 | 2021-03-19 | 15 | 16 | `b09109e79a15` | NEW_LAYOUT | 2021-01-29 | `UnsupportedLayoutError` |
| 2022-03 | 2022-03-17 | 15 | 16 | `3f49d137a74a` | NEW_LAYOUT | 2022-01-28 | `UnsupportedLayoutError` |
| 2022-06 | 2022-06-16 | 15 | 16 | `f2f7eb2600c4` | NEW_LAYOUT | 2022-04-22 | `UnsupportedLayoutError` |
| 2023-03 | 2023-03-21 | 15 | 16 | `c2dd446437b8` | NEW_LAYOUT | 2023-01-27 | `UnsupportedLayoutError` |
| 2023-06 | 2023-06-23 | 15 | 16 | `568069baa304` | NEW_LAYOUT | 2023-04-21 | `UnsupportedLayoutError` |
| 2023-09 | 2023-09-18 | 15 | 16 | `11475c99bbbe` | NEW_LAYOUT | 2023-07-28 | `UnsupportedLayoutError` |
| 2023-12 | 2023-12-20 | 15 | 16 | `7c7e35b73c7f` | NEW_LAYOUT | 2023-10-20 | `UnsupportedLayoutError` |
| 2024-03 | 2024-03-19 | 15 | 16 | `8207789ffd04` | NEW_LAYOUT | 2024-01-26 | `UnsupportedLayoutError` |
| 2024-06 | 2024-06-19 | 15 | 16 | `7c7e35b73c7f` | NEW_LAYOUT | 2024-04-19 | `UnsupportedLayoutError` |
| 2024-09 | 2024-09-20 | 15 | 16 | `7c7e35b73c7f` | NEW_LAYOUT | 2024-07-26 | `UnsupportedLayoutError` |
| 2024-12 | 2024-12-24 | 15 | 16 | `7c7e35b73c7f` | NEW_LAYOUT | 2024-10-18 | `UnsupportedLayoutError` |
| 2025-03 | 2025-03-19 | 15 | 16 | `ebd3a667d9c2` | NEW_LAYOUT | 2025-01-24 | `UnsupportedLayoutError` |
| 2025-06 | 2025-06-25 | 15 | 16 | `63711db09802` | NEW_LAYOUT | 2025-04-18 | `UnsupportedLayoutError` |
| 2025-07 | 2025-07-23 | 15 | 16 | `ff2385e2858c` | NEW_LAYOUT | 2025-05-30 | `UnsupportedLayoutError` |
| 2025-08 | 2025-08-28 | 15 | 16 | `ff2385e2858c` | NEW_LAYOUT | 2025-06-27 | `UnsupportedLayoutError` |
| 2025-09 | 2025-09-24 | 15 | 16 | `ff2385e2858c` | NEW_LAYOUT | 2025-07-25 | `UnsupportedLayoutError` |
| 2025-10 | 2025-10-01 | 15 | 16 | `ff2385e2858c` | NEW_LAYOUT | 2025-08-22 | `UnsupportedLayoutError` |
| 2025-11 | 2025-11-24 | 15 | 16 | `ff2385e2858c` | NEW_LAYOUT | 2025-09-19 | `UnsupportedLayoutError` |
| 2025-12 | 2025-12-22 | 15 | 16 | `ff2385e2858c` | NEW_LAYOUT | 2025-10-31 | `UnsupportedLayoutError` |
| 2026-01 | 2026-01-21 | 15 | 16 | `63711db09802` | NEW_LAYOUT | 2025-11-28 | `UnsupportedLayoutError` |
| 2026-02 | 2026-02-06 | 15 | 16 | `803f126bc972` | NEW_LAYOUT | 2025-12-31 | `UnsupportedLayoutError` |
| 2026-03 | 2026-03-23 | 15 | 16 | `3dc8cea81855` | NEW_LAYOUT | 2026-01-31 | `UnsupportedLayoutError` |
| 2026-04 | 2026-04-23 | 15 | 16 | `783c513d6a95` | NEW_LAYOUT | 2026-02-28 | `UnsupportedLayoutError` |
| 2026-05 | 2026-05-22 | 15 | 16 | `6df18d4028b6` | NEW_LAYOUT | 2026-03-31 | `UnsupportedLayoutError` |
| 2026-06 | 2026-06-22 | 15 | 16 | `181d2bfc0970` | V1_PARSE_PASS | 2026-04-30 | Parsed exactly |

## 7. Layout groups

The census found 24 semantic pair layouts among 36 retrieved table pairs. The signature includes normalized title, unit, header spans and roles, ordered codes and labels, hierarchy, memorandum structure, population markers, coverage percentages, reporting convention, and measure columns. Dates are normalized; raw page hashes are not used as layout identities.

All present groups use the same exact two published titles. “Rows” below is major/industry. Full 64-character signatures, normalized grids, exact ordered row-label inventories, note text, population wording, date structure, and taxonomy deltas are preserved in `layout_inventory.json`.

| Signature | Sampled periods | n | Rows | Unit | Coverage % | Reporting convention | V1 | New parser? |
|---|---|---:|---:|---|---|---|---|---|
| `218b36e84961` | 2013-01, 2013-02, 2013-03 | 3 | 29/40 | Billion (legacy rupee glyph) | not stated | legacy | NEW_LAYOUT | yes |
| `47ac71419722` | 2014-03 | 1 | 29/40 | Billion (legacy rupee glyph) | not stated | legacy | NEW_LAYOUT | yes |
| `98bf4ae1c749` | 2015-03 | 1 | 29/40 | Billion (legacy rupee glyph) | 95 | legacy | NEW_LAYOUT | yes |
| `0ef1d2ffbb86` | 2016-03 | 1 | 29/40 | `(₹ Billion)` | 95 | legacy | NEW_LAYOUT | yes |
| `ffab783abd92` | 2017-03 | 1 | 29/40 | `(₹ Billion)` | 95 | legacy | NEW_LAYOUT | yes |
| `364e5b23ae7b` | 2018-03, 2019-03 | 2 | 29/40 | `(₹ Billion)` | 90 | legacy | NEW_LAYOUT | yes |
| `e164176cf7c6` | 2020-03 | 1 | 29/40 | `(₹ Crore)` | 90 | legacy | NEW_LAYOUT | yes |
| `ebddd3eb273d` | 2020-06 | 1 | 29/40 | `(₹ Crore)` | 90 | legacy | NEW_LAYOUT | yes |
| `b09109e79a15` | 2021-03 | 1 | 33/43 | `(₹ Crore)` | 90 | legacy | NEW_LAYOUT | yes |
| `3f49d137a74a` | 2022-03 | 1 | 44/43 | `(₹ Crore)` | 92 | legacy | NEW_LAYOUT | yes |
| `f2f7eb2600c4` | 2022-06 | 1 | 44/43 | `(₹ Crore)` | 93 | legacy | NEW_LAYOUT | yes |
| `c2dd446437b8` | 2023-03 | 1 | 44/43 | `(₹ Crore)` | 93 | legacy | NEW_LAYOUT | yes |
| `568069baa304` | 2023-06 | 1 | 44/43 | `(₹ Crore)` | 93 | legacy | NEW_LAYOUT | yes |
| `11475c99bbbe` | 2023-09 | 1 | 44/43 | `(₹ Crore)` | 93 | legacy | NEW_LAYOUT | yes |
| `7c7e35b73c7f` | 2023-12, 2024-06, 2024-09, 2024-12 | 4 | 44/43 | `(₹ Crore)` | 95 | legacy | NEW_LAYOUT | yes |
| `8207789ffd04` | 2024-03 | 1 | 44/43 | `(₹ Crore)` | 95 | legacy | NEW_LAYOUT | yes |
| `ebd3a667d9c2` | 2025-03 | 1 | 44/43 | `(₹ Crore)` | 95 | legacy | NEW_LAYOUT | yes |
| `63711db09802` | 2025-06, 2026-01 | 2 | 44/43 | `(₹ Crore)` | 95 | legacy | NEW_LAYOUT | yes |
| `ff2385e2858c` | 2025-07–2025-12 | 6 | 44/43 | `(₹ Crore)` | 95 | legacy | NEW_LAYOUT | yes |
| `803f126bc972` | 2026-02 | 1 | 44/43 | `(₹ Crore)` | 95 | month-end note | NEW_LAYOUT | yes |
| `3dc8cea81855` | 2026-03 | 1 | 44/43 | `(₹ Crore)` | 95 | month-end note | NEW_LAYOUT | yes |
| `783c513d6a95` | 2026-04 | 1 | 44/43 | `(₹ Crore)` | 95 | month-end note | NEW_LAYOUT | yes |
| `6df18d4028b6` | 2026-05 | 1 | 44/43 | `(₹ Crore)` | 95 | month-end + Section-42 reference | NEW_LAYOUT | yes |
| `181d2bfc0970` | 2026-06 | 1 | 44/43 | `(₹ Crore)` | 95 | month-end + Section-42 reference | V1_PARSE_PASS | no |

The principal header families are:

- 2013–2020: seven-column unit row; `Item`/`Industry`; four outstanding columns and two growth columns.
- 2021: six-column unit row; `Sector`/`Industry`; three outstanding and two growth columns.
- 2022–February 2026: generally seven columns and four outstanding columns, with publication-specific span, year, punctuation, note, and footnote changes.
- March–April 2026: seven columns and four outstanding columns, but not the v1 major-table header contract.
- May 2026: six columns and three outstanding columns.
- June 2026: the exact v1 seven-column contract; major has three named outstanding columns plus the structural blank cell required by v1, while industry has four outstanding columns.

## 8. Parser-v1 compatibility

- `V1_PARSE_PASS`: 1 issue, June 2026.
- `V1_SIGNATURE_MATCH_PARSE_FAIL`: 0 issues.
- `NEW_LAYOUT`: 35 issues.
- Missing pair: 4 issues.

The production parser was called unchanged for every retrieved pair, and the exact exception was recorded. Failures were not weakened or reclassified as successes. Common failures include a missing exact v1 semantic data-table selector on older pages, different major-table outstanding-column spans, a six-column May 2026 unit row, and historical row mappings that do not equal the immutable v1 mappings.

## 9. Earliest verified v1-compatible month

June 2026 is both the earliest and latest sampled v1-compatible month.

## 10. Largest verified contiguous v1 window

The largest fully verified contiguous interval is **June 2026 through June 2026**. May 2026 was inspected and fails, so the lower boundary is exact. June 2026 is the latest Bulletin exposed by the archive during the run.

This is monthly verification, not inference. No broader v1 interval exists in the sampled evidence.

## 11. Gaps and access failures

- Missing target pair: March 2010, March 2011, March 2012, and December 2012.
- First present pair: January 2013. The availability boundary is therefore bounded to December 2012/January 2013.
- Source-blocked issues: 0.
- Retrieval failures: 0.
- Ambiguous issues or table candidates: 0.
- Non-official redirects: 0.

The four missing pages were inspected for credit/industry/sector link text. Their other Bulletin tables did not provide a credible title variant for the target pair. This is evidence of absence in those issues, not evidence that no other RBI publication carried related data.

## 12. Taxonomy changes

The exact inventories show several material families:

1. **January 2013–June 2020 sampled family:** 29 major rows and 40 industry rows. Major codes are nested under `1`, such as `1.2.3.6.1 Wholesale Trade`; industry codes are rooted at `1`. Relative to v1, the code system and hierarchy are materially different. Aviation, HFC/PFI detail, gold-jewellery loans, priority-sector memorandum rows, and airport/port/railway infrastructure detail are absent. These are not safe automatic continuities.
2. **March 2021 transition:** 33 major and 43 industry rows. Aviation, HFCs, PFIs, gold-jewellery loans, and airport/port/railway detail appear, but the old `1.2...` hierarchy remains. Coverage is stated as about 90 per cent.
3. **March 2022 onward sampled family:** 44 major and 43 industry rows. Top-level major rows change to Roman `I/II/III`, sector groups move to `1–5`, and the ten priority-sector memorandum children are present. The industry inventory matches v1 economically by exact sampled labels from March 2022, while major footnote text and memorandum codes continue to change.
4. **2023–2026 refinements:** `Gross Bank Credit` becomes `Bank Credit`; priority memorandum codes move from `5.1–5.10` to `(i)–(x)`; footnote numbering and superscript/plain rendering vary; punctuation in `2. Industries` and several major labels changes.

The compact delta uses all required continuity outcomes. Exact label/code matches are `EXACT_CONTINUITY`; unique exact-label code moves are `LIKELY_CONTINUITY_REQUIRES_REVIEW`; dissimilar labels on reused codes are `DEFINITION_CHANGED`; additions/removals are `NEW_SERIES`/`DISCONTINUED_SERIES`; duplicate-label matches such as generic “Others” are `UNRESOLVED`. No rename is automatically stitched.

## 13. Methodology and population changes

- Unit changes from billion in the 2013–2019 samples to crore by March 2020.
- Estimated SIBC coverage is unstated in the earliest samples, about 95 per cent in 2015–2017, about 90 per cent in 2018–2021, 92 per cent in March 2022, 93 per cent in June 2022–September 2023 samples, and 95 per cent from December 2023 onward in the sampled evidence.
- No reliable explicit reporting-bank count was extracted from the sampled notes; no bank-count continuity claim is made.
- The population notes evolve from sparse legacy wording to explicit Section-42/all-SCB versus select-bank SIBC wording.
- Reporting dates are generally reporting Fridays in older tables. The notes introduce last-day-of-month/reporting-fortnight methodology by February 2026 and an explicit Section-42 comparison-date reference by May 2026.
- Observation dates lag Bulletin publication and can repeat across Bulletins. Bulletin period must not be treated as observation date.

## 14. Historical continuity risks

- A reused row code can represent a different concept; the old code `1` is Gross Bank Credit, while v1 code `1` is Agriculture & Allied Activities.
- Generic labels such as “Others” occur under multiple parents and cannot be matched by label alone.
- Coverage changes from 90 to 92 to 93 to 95 per cent can create level breaks even when labels are unchanged.
- Billion-to-crore conversion is mechanical only after confirming the economic definition and population are unchanged.
- Priority-sector memorandum identifiers and footnotes change without guaranteeing identical definitions.
- Reporting-Friday and month-end observations are not interchangeable vintages.
- Repeated table data across consecutive Bulletin issues means issue continuity and observation continuity are different questions.

## 15. Recommended first backfill window

For parser v1 unchanged, the exact supported window is only **June 2026**; there is no earlier v1 backfill window.

For the next implementation, target **July 2025 through December 2025** first. All six monthly Bulletins were actually inspected, both tables are present, and all six share semantic signature `ff2385e2858c...`. This gives a bounded, contiguous parser-v2 target without claiming compatibility for unsampled months.

## 16. Required parser versions

- v1 remains unchanged for June 2026.
- A new version is required for July–December 2025.
- The January–May 2026 header/method transitions should be explicit fixtures or sub-layouts before extending that parser through the v1 boundary.
- At least one additional historical family is likely required for the 2022-era Roman/current taxonomy, and another for the 2013–2020 nested-code taxonomy. March 2021 is a distinct transition that may need its own version.

One additional parser version is sufficient for a useful six-month backfill, but the evidence does not support claiming that one version will safely cover the full January 2013–May 2026 history.

## 17. Recommended next implementation task

Implement an offline parser-v2 spike for the exact July–December 2025 signature, using the six cached pairs as non-committed source evidence and compact synthetic/derived fixtures in tests. Keep taxonomy stitching out of scope. Require exact header variants, population notes, coverage wording, observation dates, and cross-table duplicate validation. After that passes, add January–May 2026 one transition at a time, then perform a monthly continuity census for the sampled-compatible 2022–June 2025 region before claiming a longer interval.

## 18. Exact files changed

- `scripts/census_rbi_sectoral_credit_history.py` — bounded investigation-only census runner.
- `tests/test_sectoral_credit_history_census.py` — offline census tests.
- `docs/investigations/rbi_sectoral_credit_history_census.md` — this evidence report.

Ignored artifacts created under `spike-artifacts/historical-census/` include `anchor_discovery.json`, `census.json`, `census_summary.csv`, `layout_inventory.json`, `request_log.json`, `sample_schedule.json`, and cached raw HTML/metadata. `.gitignore` did not need modification.

## 19. Ruff and pytest results

- Census tests: 12 passed serially.
- Ruff: passed on the census script and test before report creation.
- Full repository Ruff: passed (`ruff check .`).
- Full repository pytest: 100 passed serially in 1.35 seconds.
- Live census: completed; 40/40 issues, 113/120 live attempts, 4 missing pairs, 35 new-layout issues, 1 v1 pass, 0 blocks, 0 retrieval failures.
