#!/usr/bin/env python3
"""Build deterministic offline parser-v2 evidence from the validated census cache."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from indiamacro.rbi import sectoral_credit_bulletin as v1
from indiamacro.rbi import sectoral_credit_bulletin_v2 as v2


TARGETS = tuple(f"2025-{month:02d}" for month in range(7, 13))
CHALLENGE_MARKERS = (
    "captcha",
    "access denied",
    "human verification",
    "verify you are human",
    "request rejected",
    'window["bobcmn"]',
    "/tspd/",
)


def _official(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "rbi.org.in" or host.endswith(".rbi.org.in"))


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _validated_raw(
    census_dir: Path, table: dict[str, Any], *, context: str
) -> tuple[bytes, str]:
    http = table["http"]
    cache_path = census_dir / http["cache_file"]
    raw = cache_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = {table["raw_sha256"], http["sha256"]}
    if expected != {digest}:
        raise RuntimeError(f"{context}: cached raw hash mismatch")
    url = table["url"]
    if not _official(url) or not _official(http["final_url"]):
        raise RuntimeError(f"{context}: non-official source URL")
    if http["http_status"] != 200 or "html" not in http["content_type"].lower():
        raise RuntimeError(f"{context}: invalid cached HTTP metadata")
    lowered = raw.decode("utf-8", errors="ignore").casefold()
    if http["access_control_markers_present"] or any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise RuntimeError(f"{context}: cached source contains an access-control marker")
    return raw, url


def _continuity_rows() -> list[dict[str, Any]]:
    return [
        {
            "source_table": mapping.source_table,
            "source_row_code": mapping.source_row_code,
            "v2_source_label": mapping.source_label,
            "normalized_label": mapping.normalized_label,
            "parent_row_code": mapping.parent_row_code or "",
            "population_id": mapping.population_id,
            "emits_observations": str(mapping.emits_observations).lower(),
            "continuity_classification": mapping.continuity_classification,
            "v2_base_series_id": mapping.base_series_id,
            "corresponding_v1_base_series_id": mapping.corresponding_v1_base_series_id or "",
        }
        for mapping in v2.V2_ROW_MAPPINGS
    ]


def build(census_dir: Path, output_dir: Path) -> dict[str, Any]:
    census = json.loads((census_dir / "census.json").read_text(encoding="utf-8"))
    by_period = {item["requested_bulletin_period"]: item for item in census["issues"]}
    if any(period not in by_period for period in TARGETS):
        raise RuntimeError("CENSUS_EVIDENCE_MISSING: one or more 2025H2 issues are absent")
    output_dir.mkdir(parents=True, exist_ok=True)
    release_frames: list[pd.DataFrame] = []
    release_records: list[dict[str, Any]] = []
    compatibility: list[dict[str, Any]] = []
    for period in TARGETS:
        issue = by_period[period]
        major_info = issue.get("major_table")
        industry_info = issue.get("industry_table")
        if major_info is None or industry_info is None:
            raise RuntimeError(f"CENSUS_EVIDENCE_MISSING: {period} has no complete table pair")
        major_raw, major_url = _validated_raw(
            census_dir, major_info, context=f"{period} major"
        )
        industry_raw, industry_url = _validated_raw(
            census_dir, industry_info, context=f"{period} industry"
        )
        parsed = v2.parse_sectoral_credit_bulletin_v2_release(
            major_raw,
            industry_raw,
            major_sectors_url=major_url,
            industries_url=industry_url,
        )
        del major_raw, industry_raw
        expected_bulletin = f"{parsed.metadata.publication_date[:4]}-{int(parsed.metadata.publication_date[5:7]):02d}"
        if expected_bulletin != period:
            raise RuntimeError(f"{period}: target period and table publication month disagree")
        release_frames.append(parsed.observations)
        record = asdict(parsed.metadata)
        record["observation_count"] = len(parsed.observations)
        release_records.append(record)
        manifest_path = output_dir / "per-release manifests" / f"{period}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        compatibility.append(
            {
                "bulletin_period": parsed.metadata.bulletin_period,
                "layout_id": parsed.metadata.layout_id,
                "structural_signature_sha256": parsed.metadata.structural_signature_sha256,
                "taxonomy_signature_sha256": parsed.metadata.taxonomy_signature_sha256,
                "methodology_signature_sha256": parsed.metadata.methodology_signature_sha256,
                "publication_date": parsed.metadata.publication_date,
                "current_observation_date": parsed.metadata.current_observation_date,
                "prior_year_reference_date": parsed.metadata.prior_year_reference_date,
                "financial_year_base_date": parsed.metadata.financial_year_base_date,
                "section42_override_date": parsed.metadata.section42_prior_year_override_date or "",
            }
        )
    combined = pd.concat(release_frames, ignore_index=True)
    combined = combined.sort_values(
        ["publication_date", "source_table", "source_row_code", "column_role", "series_id"],
        kind="stable",
    ).reset_index(drop=True)
    if tuple(combined.columns) != v2.RELEASE_OBSERVATION_COLUMNS:
        raise RuntimeError("Combined release schema changed")
    duplicate_count = int(combined.duplicated(list(v2.VINTAGE_KEY_COLUMNS), keep=False).sum())
    if duplicate_count:
        raise RuntimeError("Cross-release vintage-key duplicates detected")
    observations_path = output_dir / "release_observations_2025h2.csv"
    observations_path.write_bytes(v2.release_observations_to_csv_bytes(combined))
    continuity = _continuity_rows()
    _write_csv(
        output_dir / "continuity_mapping_v2_to_v1.csv",
        tuple(continuity[0]),
        continuity,
    )
    _write_csv(
        output_dir / "compatibility_matrix.csv",
        tuple(compatibility[0]),
        compatibility,
    )
    continuity_counts: dict[str, int] = {name: 0 for name in v2.CONTINUITY_CLASSES}
    for row in continuity:
        continuity_counts[row["continuity_classification"]] += 1
    combined_semantic_hash = hashlib.sha256(
        v1._observations_csv_bytes(
            combined,
            v2.RELEASE_SEMANTIC_COLUMNS,
            sort_rows=True,
            missing_value=v1.SEMANTIC_MISSING_VALUE,
        )
    ).hexdigest()
    manifest = {
        "layout_id": v2.LAYOUT_ID,
        "parser_version": v2.PARSER_VERSION,
        "census_manifest_sha256": hashlib.sha256(
            (census_dir / "census.json").read_bytes()
        ).hexdigest(),
        "target_periods": list(TARGETS),
        "release_count": len(release_records),
        "combined_observation_count": len(combined),
        "unique_series_count": int(combined["series_id"].nunique()),
        "publication_date_range": [combined["publication_date"].min(), combined["publication_date"].max()],
        "observation_date_range": [combined["observation_date"].min(), combined["observation_date"].max()],
        "counts_by_release": {
            key: int(value) for key, value in combined["bulletin_period"].value_counts().sort_index().items()
        },
        "counts_by_measure": {
            key: int(value) for key, value in combined["measure"].value_counts().sort_index().items()
        },
        "counts_by_population": {
            key: int(value) for key, value in combined["population_id"].value_counts().sort_index().items()
        },
        "counts_by_column_role": {
            key: int(value) for key, value in combined["column_role"].value_counts().sort_index().items()
        },
        "continuity_class_counts": continuity_counts,
        "unresolved_continuity_count": continuity_counts[v2.UNRESOLVED],
        "cross_release_vintage_key_duplicate_count": duplicate_count,
        "growth_reconciliation_checks": sum(item["growth_reconciliation_checks"] for item in release_records),
        "growth_reconciliation_skipped": sum(item["growth_reconciliation_skipped"] for item in release_records),
        "growth_reconciliation_failures": 0,
        "combined_semantic_sha256": combined_semantic_hash,
        "combined_provenance_sha256": hashlib.sha256(observations_path.read_bytes()).hexdigest(),
        "releases": release_records,
    }
    (output_dir / "parser_v2_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = (
        "# RBI sectoral-credit parser-v2 validation\n\n"
        f"Status: PASS_V2\n\n"
        f"- Releases: {manifest['release_count']}\n"
        f"- Combined observations: {manifest['combined_observation_count']}\n"
        f"- Unique series: {manifest['unique_series_count']}\n"
        f"- Growth checks/skips/failures: {manifest['growth_reconciliation_checks']}/"
        f"{manifest['growth_reconciliation_skipped']}/0\n"
        f"- Cross-release vintage duplicates: {duplicate_count}\n"
        f"- Unresolved continuity mappings: {manifest['unresolved_continuity_count']}\n"
        f"- Combined semantic SHA-256: `{combined_semantic_hash}`\n"
        f"- Combined provenance SHA-256: `{manifest['combined_provenance_sha256']}`\n"
    )
    (output_dir / "parser_v2_validation_report.md").write_text(report, encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--census-dir", type=Path, default=Path("spike-artifacts/historical-census")
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("spike-artifacts/parser-v2")
    )
    args = parser.parse_args()
    manifest = build(args.census_dir, args.output_dir)
    print(json.dumps({
        "status": "PASS_V2",
        "releases": manifest["release_count"],
        "observations": manifest["combined_observation_count"],
        "semantic_sha256": manifest["combined_semantic_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
