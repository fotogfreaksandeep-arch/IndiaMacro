# RBI Sectoral Credit: Official Alternate-Access Investigation

## Executive conclusion

**Final status: `PASS_OFFICIAL_PATH`.**

The Sectoral Deployment connector can proceed with fully automated official retrieval by using the RBI Bulletin's two Current Statistics HTML tables:

- **15. Deployment of Gross Bank Credit by Major Sectors**
- **16. Industry-wise Deployment of Gross Bank Credit**

The stable entry point is <https://rbi.org.in/Scripts/BS_ViewBulletin.aspx>. A client can fetch that page, locate the two links by their exact titles, and follow the discovered URLs. The June 2026 run discovered IDs `24257` and `24258`; those IDs are evidence from this run and must not be configured as constants.

Both pages were retrieved by an ordinary `requests.Session` without browser automation, authentication, copied cookies, JavaScript execution, or human interaction. Together the tables provide the same SIBC sector/industry observations as Statements I and II, including outstanding amounts, reporting-date columns, ₹ crore units, year-on-year growth, hierarchy, and population/methodology notes. The Bulletin route has a freshness lag: the June 22, 2026 Bulletin tables contain April 30, 2026 observations, while the latest release page on June 30 covered May 2026.

## Time and scope limits

- The work was performed as one bounded investigation session and stopped after a viable official path was demonstrated, within the two-hour target and three-hour absolute limit.
- Requests were sequential and single-process. There were no parallel workers, browser tools, containers, archive crawls, or bulk downloads.
- The reproducible investigation run attempted five public requests: DBIE landing, DBIE/Fusion registry, RBI Bulletin index, and the two discovered Bulletin tables. Four returned responses; the Fusion registry closed the connection before an HTTP response.
- Responses were streamed in 64 KiB chunks with a 5 MiB per-response cap. The largest validated response was 315,633 bytes.
- No economic-data parser or production connector was built. HTML inspection was limited to discovery and format/semantic validation.
- Open-web searches were restricted to locating official RBI or Government of India sources. Third-party mirrors did not qualify and were not tested as data sources.

## Existing F5/TSPD blocker

The prior spike remains accurate. The release index and May 2026 release page returned HTTP 200 and yielded this official workbook URL:

<https://rbidocs.rbi.org.in/rdocs/content/docs/SIBCS30062026.xlsx>

The document host returned a 46,303-byte HTTP 200 `text/html` F5/TSPD JavaScript challenge rather than XLSX bytes. The response had no redirects, failed ZIP/XLSX validation, was classified `SOURCE_BLOCKED`, and was not saved as a workbook. This investigation did not repeatedly probe that blocked URL or attempt the challenge.

## Candidate-source comparison

| Candidate | Official source | Documented | Automated retrieval | Format | Semantic coverage | Stability | Result |
|---|---|---:|---:|---|---|---|---|
| Release-page “Statements I and II” workbook | RBI | Yes | No | Claimed XLSX; HTTP 200 HTML challenge received | FULL if retrievable | Stable discovery, blocked document host | `SOURCE_BLOCKED` |
| DBIE time series / public Fusion registry | RBI | Yes | Not demonstrated | HTML application; advertised SDMX service | FULL according to RBI documentation, but no data response validated | Catalogue is stable; tested SDMX registry closed the Python connection | `NOT_DEMONSTRATED` |
| RBI Bulletin Current Statistics tables 15 and 16 | RBI | Yes | **Yes** | Structured HTML tables | **FULL** | Stable index and exact titles; discovered numeric IDs | **`PASS_OFFICIAL_PATH`** |
| Open Government Data Platform India | Government of India | N/A | No matching route found | No matching artifact located | NONE demonstrated | Bounded catalogue/search-engine search only | `NO_MATCH_FOUND` |

## Candidate evidence

### 1. RBI Bulletin HTML tables — demonstrated route

Official entry point:

<https://rbi.org.in/Scripts/BS_ViewBulletin.aspx>

Deterministic discovery:

1. GET the current RBI Bulletin index.
2. Select the single links whose normalized anchor text exactly matches the two table titles.
3. Resolve relative URLs and require HTTPS on `rbi.org.in` or an RBI subdomain.
4. GET each discovered HTML page.
5. Reject HTTP errors and known challenge markers; require meaningful HTML table structure and semantic markers.

Live evidence from the final run:

| Artifact | HTTP | Final URL | Redirects | Content-Type | Bytes | SHA-256 |
|---|---:|---|---:|---|---:|---|
| Bulletin index | 200 | <https://rbi.org.in/Scripts/BS_ViewBulletin.aspx> | 0 | `text/html; charset=utf-8` | 315,633 | `8d0ee052bc46b97cc70ef158a3afa365fefb1f34a73dfaa9ae72c19a1987d229` |
| Table 15 | 200 | <https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24257> | 0 | `text/html; charset=utf-8` | 151,024 | `a7143fcd39cf1d3538de036893d0798f0cef8c1a7afe298249d0ba5cebb2d17c` |
| Table 16 | 200 | <https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24258> | 0 | `text/html; charset=utf-8` | 146,404 | `115b59651d1bc7f8b3f80480c4e7becc18e946172d4570f848a8881ea9360e8b` |

No CAPTCHA, F5/TSPD marker, login, or interaction requirement was observed. Table 15 contained 337 cells across 56 page rows; Table 16 contained 329 cells across 55 page rows. Both contained the required table headers, meaningful numeric data rows, units, observation-date headers, and year-on-year columns.

The exact whole-page hashes are preservation evidence for this run, not stable dataset identifiers: RBI's surrounding page chrome and update metadata can change independently of table values.

### 2. Database on Indian Economy

Official landing page: <https://data.rbi.org.in/DBIE/>

Official service catalogue: <https://data.rbi.org.in/FusionRegistry/webservice/structure.html>

RBI documentation establishes that DBIE contains the target time series and supports export:

- The [Comprehensive Guide for Current Statistics](https://dbieold.rbi.org.in/DBIE/doc/Comprehensive%20Guide%20for%20Monthly%20Bulletin.pdf) states that Tables 15 and 16 are monthly SIBC data from selected SCBs and that the time series are released through the real-time Handbook on DBIE.
- The [DBIE export guide](https://dbieold.rbi.org.in/DBIE/doc/Exporting_report_data_%26_related_options.pdf) documents Excel, PDF, text, and CSV export from preformatted reports.
- The [DBIE FAQ](https://dbieold.rbi.org.in/DBIE/doc/Frequently%20Asked%20Questions%20-%20DBIE.pdf) says an account is not required and data can be viewed and downloaded without login credentials.

The ordinary Python test retrieved the DBIE application landing page:

- HTTP 200, no redirects
- `text/html; charset=UTF-8`
- 50,732 bytes
- SHA-256 `05671c7cafb43d49658738be7883d978d26d1d850d77fd93940c5f6854689bca`

However, the public Fusion Registry page and tested public SDMX structure routes closed the Python connection without an HTTP response. No valid SDMX metadata or data response was obtained. The Angular application's undocumented gateway calls use an application session/payload flow; this investigation did not reverse-engineer or reproduce it. DBIE is therefore semantically promising but not a demonstrated automation route in this session.

### 3. RBI Statistics/Data Releases and static documents

The Statistics/Data Releases route is the already-tested release-page route. Its HTML discovery works, but its `rbidocs.rbi.org.in` workbook is challenged. The Bulletin is the successful RBI-controlled alternative: the data are directly embedded in `rbi.org.in` HTML, so no document-host challenge is involved.

The Bulletin pages also advertise XLSX and PDF versions on `rbidocs.rbi.org.in`. They were not needed or tested after the directly embedded official HTML succeeded; avoiding those links also avoids unnecessary requests to the known challenged document host.

### 4. Open Government Data Platform India

Bounded searches of `data.gov.in` for the exact dataset title and RBI-origin sectoral/industry deployment terms did not locate a matching resource. Results found related or historical banking resources, but none demonstrated the monthly SIBC dataset with equivalent coverage. This is a scoped negative finding, not a claim that no such resource can exist anywhere in the catalogue.

## Semantic-equivalence assessment

| Requirement | Bulletin evidence | Coverage |
|---|---|---|
| Sector/subsector hierarchy | Table 15 contains agriculture, industry by size, services and subsectors, personal loans and subsectors, and priority-sector memorandum items. | FULL |
| Industry hierarchy | Table 16 contains industries 2.1–2.19 plus subgroups such as food processing, textiles, chemicals, engineering and infrastructure. | FULL |
| Outstanding values | Both tables contain numeric “Outstanding as on” columns. | FULL |
| Unit | Both tables state `(₹ Crore)`. | FULL |
| Exact observation dates | Header structure records March 31, April 18 and April 30 under their respective 2025/2026 headings; the current observation is April 30, 2026. | FULL |
| Year-on-year growth | Both tables contain a `Y-o-Y` percentage column. | FULL |
| Statements I and II coverage | Table 15 supplies the sector statement; Table 16 supplies the industry statement. | FULL/equivalent |
| Population definition | Table 15 notes that SIBC covers selected banks accounting for about 95% of SCB non-food credit; Section-42 aggregates cover all SCBs. | FULL |
| Release/publication metadata | Bulletin period is June 2026 and both pages state June 22, 2026. | FULL |

Semantic spot-check against RBI's [April 2026 release](https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=62827):

| Measure | Release highlight Y-o-Y | Bulletin table Y-o-Y |
|---|---:|---:|
| Non-food credit | 15.8% | 15.8% |
| Agriculture and allied activities | 13.7% | 13.7% |
| Industry | 15.1% | 15.1% |
| Services | 18.6% | 18.6% |
| Personal loans | 16.0% | 16.0% |

This exact match, plus the shared SIBC methodology/population note and equivalent row coverage, supports the `FULL` classification.

## Risks and stability concerns

- **Freshness:** the Bulletin route trails the dedicated monthly release. On this run, June 22 tables covered April 30 while the June 30 release covered May. It is suitable for automated official retrieval but not for earliest-possible release ingestion.
- **Legacy HTML:** the pages use nested presentation tables. Production parsing should target the titled data table and semantic headers, not fixed DOM positions or numeric IDs.
- **Discovery changes:** table numbering or titles could change in a future Bulletin redesign. The connector should fail closed when exact discovery or semantic validation becomes ambiguous.
- **Intermittent access controls:** the tested Python run succeeded without challenge, but RBI infrastructure can exhibit WAF behavior. Retrieval should preserve response evidence and reject challenge HTML.
- **Whole-page hash variability:** navigation and website-update content may change. Hashes prove exact preservation of a retrieval, but data-level comparison will eventually require canonical table parsing outside this investigation.
- **History not crawled:** only the current Bulletin was tested. Archive discovery and long-run schema drift remain follow-up work; no historical archive was downloaded here.

## Recommended project decision

**Proceed with the alternate official route.**

Use the RBI Bulletin current index as the entry point, discover Tables 15 and 16 by exact title, retrieve the structured HTML pages, preserve and hash the responses, validate their semantic markers, and only then parse them in a separate connector task. Document the one-release freshness lag explicitly. Keep the dedicated release-workbook route marked retrieval-incomplete until RBI's document host becomes normally automation-compatible.

No pivot datasets were investigated because the target dataset met the hard success condition.

## Files changed

- `scripts/investigate_rbi_sectoral_credit_sources.py` — bounded live investigation and evidence generator.
- `tests/test_investigate_rbi_sectoral_credit_sources.py` — focused discovery and structured-HTML validation tests.
- `docs/investigations/rbi_sectoral_credit_official_access.md` — this report.
- `spike-artifacts/source-investigation/candidates.json` — machine-readable candidate matrix and HTTP evidence.
- `spike-artifacts/source-investigation/bulletin_major_sectors.html` — preserved validated Table 15 response.
- `spike-artifacts/source-investigation/bulletin_industries.html` — preserved validated Table 16 response.

No production connector, parser, package architecture, dashboard, or API file was changed.

## Commands run and results

Final reproducible commands:

```text
.venv/bin/ruff check scripts tests
All checks passed!

.venv/bin/pytest -q
..........                                                               [100%]
10 passed in 0.04s

.venv/bin/python scripts/investigate_rbi_sectoral_credit_sources.py spike-artifacts/source-investigation
status: PASS_OFFICIAL_PATH
best_candidate_id: rbi_bulletin_html_tables_15_16
```

Additional bounded diagnostics used ordinary Python `requests` calls to the DBIE landing/registry URLs and the two discovered Bulletin URLs. Local read-only inspections confirmed the Table 15 population note and spot-checked the April 2026 values. Public web searches covered DBIE, RBI Statistics/Data Releases, RBI Bulletin/current-statistics pages, the RBI Fusion/SDMX service page, and `data.gov.in`; only official result pages were treated as source evidence.
