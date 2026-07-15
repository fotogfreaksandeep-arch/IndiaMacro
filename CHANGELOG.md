# Changelog

All notable changes to IndiaMacro are documented here.

## 0.2.0 — local release candidate

- Added verified RBI Bulletin sectoral-credit history for July 2025 through
  June 2026. This is a tested support boundary, not complete RBI history.
- Added strict parser families for the verified 2025–2026 layout transitions.
- Productionized official ASP.NET Bulletin archive discovery and a versioned,
  atomic historical cache with network-free replay.
- Added the published-vintage model, current-observation view, stable series
  selection, and explicit `latest_publication` and `as_of` resolution.
- Added methodology-boundary and source-provenance metadata.
- Added an optional Matplotlib plotting layer and reproducible Non-food Credit
  CSV, manifest, SVG, and PNG example workflow.
- Expanded deterministic, cache-integrity, parser, history, resolution,
  visualization, and regression coverage.

## 0.1.0

- Added the initial current RBI Sectoral Deployment of Bank Credit connector.
- Added current Bulletin discovery and the strict June 2026 parser.
- Added source provenance, deterministic hashing, atomic caching, and offline
  replay.
