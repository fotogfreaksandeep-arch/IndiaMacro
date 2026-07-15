# RBI sectoral credit

IndiaMacro's first end-to-end dataset uses the official RBI Bulletin Current
Statistics Tables 15 and 16:

- `15. Deployment of Gross Bank Credit by Major Sectors`
- `16. Industry-wise Deployment of Gross Bank Credit`

```python
from indiamacro import rbi

credit = rbi.sectoral_credit()

print(credit.observations.head())
print(credit.metadata.latest_observation_date)
print(credit.metadata.freshness_status)
```

`credit.observations` is the validated long-form DataFrame.
`credit.metadata` is a typed `SectoralCreditMetadata` value containing source
URLs, exact source hashes, publication and observation dates, parser identity,
cache identity, and dedicated-release freshness. `credit.notes` contains both
complete normalized RBI source notes.

## Cache and refresh behavior

The default call first looks for the latest verified cache bundle. If one is
present, its raw pages are hash-checked and reparsed without making a network
request. If no bundle exists, IndiaMacro discovers and retrieves the current
two Bulletin tables.

```python
# Force current live discovery and retrieval.
credit = rbi.sectoral_credit(refresh=True)

# Guarantee that no network session is created.
credit = rbi.sectoral_credit(offline=True)

# Choose a cache root explicitly.
credit = rbi.sectoral_credit(cache_dir="/path/to/cache")
```

`refresh=True` never silently falls back to an older bundle. `offline=True`
raises `CacheNotFoundError` when no committed bundle exists. Combining the two
flags is invalid.

An explicit `cache_dir` takes precedence over `INDIAMACRO_CACHE_DIR`; otherwise
IndiaMacro uses the platform's standard user-cache directory. Bundles have the
form:

```text
<cache-root>/
  rbi/
    sectoral_credit/
      <publication-date>/
        <deterministic-bundle-id>/
          major_sectors.html
          industries.html
          manifest.json
```

The bundle ID is a SHA-256 over the two source hashes and their discovered and
final URLs. Downloads are streamed sequentially in 64 KiB chunks and capped at
5 MiB per page. Both raw pages are validated and parsed before an atomic cache
commit. Cache reads recalculate file hashes and both output identities;
corruption raises `CacheIntegrityError` instead of triggering an unverified
fallback. Pre-release schema 1 bundles are reported as incompatible and must be
refreshed online or removed; they are never rewritten in place.

## Dates and freshness

`latest_observation_date` is the latest date represented by the returned data,
not the retrieval date or RBI website update date. The Bulletin can lag RBI's
dedicated monthly Sectoral Deployment release. During live retrieval,
IndiaMacro inspects only the dedicated release index—never its blocked XLSX—to
report one of:

- `CURRENT_WITH_DEDICATED_RELEASE`
- `LAGGING_DEDICATED_RELEASE`
- `AHEAD_OF_DEDICATED_RELEASE`
- `UNKNOWN`

A supplementary freshness failure produces `UNKNOWN` and a diagnostic without
discarding otherwise valid Bulletin data.

## Hash identities

Metadata exposes exact `major_sectors_sha256` and `industries_sha256` values for
raw source bytes, `semantic_observations_sha256` for the economic observations
independent of page chrome and transport provenance, and
`provenance_bound_output_sha256` for all canonical fields including source URLs
and raw hashes. The exact field lists and serialization rules are in the parser
contract.

## Supported layout

Only `RBI_BULLETIN_SECTORAL_CREDIT_V1` is supported. Table links are discovered
by exact normalized anchor text; numeric Bulletin IDs are not configured.
Historical layout compatibility is not claimed. The canonical parsing details
are defined in
[`docs/contracts/rbi_sectoral_credit_bulletin_v1.md`](../contracts/rbi_sectoral_credit_bulletin_v1.md).
