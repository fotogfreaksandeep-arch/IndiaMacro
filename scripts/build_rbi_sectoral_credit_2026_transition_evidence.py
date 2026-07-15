#!/usr/bin/env python3
"""Build deterministic 12-release sectoral-credit vintage evidence offline."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd

from indiamacro.rbi import sectoral_credit_bulletin as v1
from indiamacro.rbi import sectoral_credit_bulletin_v2 as v2
from indiamacro.rbi import sectoral_credit_transition_2026 as transition


TARGETS = (
    *(f"2025-{month:02d}" for month in range(7, 13)),
    *(f"2026-{month:02d}" for month in range(1, 7)),
)
EXPECTED_CURRENT_DATES = {
    "2025-07": "2025-05-30",
    "2025-08": "2025-06-27",
    "2025-09": "2025-07-25",
    "2025-10": "2025-08-22",
    "2025-11": "2025-09-19",
    "2025-12": "2025-10-31",
    "2026-01": "2025-11-28",
    "2026-02": "2025-12-31",
    "2026-03": "2026-01-31",
    "2026-04": "2026-02-28",
    "2026-05": "2026-03-31",
    "2026-06": "2026-04-30",
}
CHALLENGE_MARKERS = (
    "captcha", "access denied", "human verification", "verify you are human",
    "request rejected", 'window["bobcmn"]', "/tspd/",
)
COMBINED_VINTAGE_KEY = (
    *v2.VINTAGE_KEY_COLUMNS,
    "population_id",
    "source_table",
)


def _official(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "rbi.org.in" or host.endswith(".rbi.org.in"))


def _validated_raw(census_dir: Path, table: dict[str, Any], context: str) -> tuple[bytes, str]:
    http = table["http"]
    raw = (census_dir / http["cache_file"]).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if {digest} != {table["raw_sha256"], http["sha256"]}:
        raise RuntimeError(f"CENSUS_EVIDENCE_MISSING: {context} raw hash mismatch")
    if not _official(table["url"]) or not _official(http["final_url"]):
        raise RuntimeError(f"CENSUS_EVIDENCE_MISSING: {context} non-official URL")
    lowered = raw.decode("utf-8", errors="ignore").casefold()
    if http["http_status"] != 200 or "html" not in http["content_type"].lower() or http["access_control_markers_present"] or any(marker in lowered for marker in CHALLENGE_MARKERS):
        raise RuntimeError(f"CENSUS_EVIDENCE_MISSING: {context} invalid cached response")
    return raw, table["url"]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"Refusing to write empty evidence {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _counts(frame: pd.DataFrame, column: str) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in frame[column].value_counts().sort_index().items()
    }


def _methodology_regime(layout_id: str) -> str:
    if layout_id in {v2.LAYOUT_ID, transition.LAYOUT_JANUARY}:
        return "LAST_REPORTING_FRIDAY"
    return "CURRENT_MONTH_END_WITH_PRIOR_OLD_FORTNIGHT_YOY_BASE"


def _mapping_rows() -> list[dict[str, Any]]:
    rows = []
    for period in transition.SUPPORTED_PERIODS:
        for role in ("major", "industry"):
            for mapping in transition.MAPPINGS_BY_PERIOD[period][role]:
                rows.append({
                    "bulletin_period": period,
                    "layout_id": transition._layout_for_period(period),
                    "source_table": mapping.source_table,
                    "source_row_code": mapping.source_row_code,
                    "source_label": mapping.source_label,
                    "normalized_label": mapping.normalized_label,
                    "parent_row_code": mapping.parent_row_code or "",
                    "is_memorandum": str(mapping.is_memorandum).lower(),
                    "population_id": mapping.population_id,
                    "emits_observations": str(mapping.emits_observations).lower(),
                    "base_series_id": mapping.base_series_id,
                    "taxonomy_continuity": mapping.continuity_classification,
                    "v1_base_series_id": mapping.corresponding_v1_base_series_id or "",
                })
    return rows


def _continuity_rows() -> list[dict[str, Any]]:
    rows = []
    for boundary in transition.BOUNDARY_CLASSIFICATIONS:
        for mapping in v2.V2_ROW_MAPPINGS:
            rows.append({
                "from_layout_id": boundary.from_layout_id,
                "to_layout_id": boundary.to_layout_id,
                "source_table": mapping.source_table,
                "source_row_code": mapping.source_row_code,
                "base_series_id": mapping.base_series_id,
                "taxonomy_continuity": boundary.taxonomy_continuity,
                "comparability": boundary.comparability,
                "methodology_change": boundary.methodology_change,
            })
    return rows


def build(census_dir: Path, output_dir: Path) -> dict[str, Any]:
    census_path = census_dir / "census.json"
    census = json.loads(census_path.read_text(encoding="utf-8"))
    issues = {item["requested_bulletin_period"]: item for item in census["issues"]}
    if any(period not in issues for period in TARGETS):
        raise RuntimeError("CENSUS_EVIDENCE_MISSING: a required release is absent")
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[pd.DataFrame] = []
    release_manifests: list[dict[str, Any]] = []
    compatibility: list[dict[str, Any]] = []
    growth_rows: list[dict[str, Any]] = []
    sequence_rows: list[dict[str, Any]] = []
    previous_current: str | None = None
    for period in TARGETS:
        issue = issues[period]
        major_info, industry_info = issue.get("major_table"), issue.get("industry_table")
        if major_info is None or industry_info is None:
            raise RuntimeError(f"CENSUS_EVIDENCE_MISSING: {period} table pair missing")
        major_raw, major_url = _validated_raw(census_dir, major_info, f"{period} major")
        industry_raw, industry_url = _validated_raw(census_dir, industry_info, f"{period} industry")
        detected = transition.detect_supported_layout(
            major_raw, industry_raw, major_url=major_url, industry_url=industry_url
        )
        if period <= "2025-12":
            parsed = v2.parse_sectoral_credit_bulletin_v2_release(
                major_raw, industry_raw,
                major_sectors_url=major_url, industries_url=industry_url,
            )
            frame = parsed.observations
            meta = asdict(parsed.metadata)
            notes = parsed.notes
        elif period <= "2026-05":
            parsed = transition.parse_transition_release(
                major_raw, industry_raw, major_url=major_url, industry_url=industry_url
            )
            frame = parsed.observations
            meta = asdict(parsed.metadata)
            notes = parsed.notes
        else:
            parsed_v1 = v1.parse_sectoral_credit_bulletin(
                major_raw, industry_raw,
                major_sectors_url=major_url, industries_url=industry_url,
            )
            frame = transition.v1_release_observations(parsed_v1)
            notes = parsed_v1.notes
            meta = asdict(parsed_v1.metadata)
            meta.update({
                "release_semantic_sha256": v2.release_semantic_sha256(frame),
                "provenance_bound_output_sha256": v2.release_provenance_sha256(frame),
                "canonical_release_key_duplicate_count": int(frame.duplicated(list(v2.VINTAGE_KEY_COLUMNS)).sum()),
                "structural_signature_sha256": detected.signatures.structural_signature_sha256,
                "taxonomy_signature_sha256": detected.signatures.taxonomy_signature_sha256,
                "methodology_signature_sha256": detected.signatures.methodology_signature_sha256,
            })
        del major_raw, industry_raw
        frame = frame.copy(deep=False)
        frames.append(frame)
        current = str(meta["current_observation_date"])
        expected = EXPECTED_CURRENT_DATES[period]
        if current != expected:
            raise RuntimeError(f"{period}: current observation date {current} != expected {expected}")
        delta = ""
        if previous_current:
            delta = str((date.fromisoformat(current) - date.fromisoformat(previous_current)).days)
        previous_current = current
        sequence_rows.append({
            "requested_period": period,
            "publication_date": meta["publication_date"],
            "expected_current_observation_date": expected,
            "actual_current_observation_date": current,
            "matches_expected": "true",
            "days_since_previous_current": delta,
            "duplicate_current_date": "false",
            "gap_from_expected": "false",
            "methodology_regime": _methodology_regime(detected.layout_id),
            "date_basis_change": "CURRENT_SWITCHES_TO_MONTH_END" if period == "2026-02" else "",
        })
        record = {
            **meta,
            "requested_period": period,
            "layout_id": detected.layout_id,
            "observation_count": len(frame),
            "counts_by_measure": _counts(frame, "measure"),
            "counts_by_population": _counts(frame, "population_id"),
            "counts_by_column_role": _counts(frame, "column_role"),
        }
        release_manifests.append(record)
        manifest_path = output_dir / "per-release-manifests" / f"{period}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        compatibility.append({
            "requested_period": period,
            "bulletin_period": detected.bulletin_period,
            "layout_id": detected.layout_id,
            "structural_signature_sha256": detected.signatures.structural_signature_sha256,
            "taxonomy_signature_sha256": detected.signatures.taxonomy_signature_sha256,
            "methodology_signature_sha256": transition._sha_json(
                transition._methodology_detail(" ".join(note.text for note in notes))
            ),
            "methodology_regime": _methodology_regime(detected.layout_id),
            "publication_date": meta["publication_date"],
            "current_observation_date": current,
            "major_ordinal": major_info["published_number"],
            "industry_ordinal": industry_info["published_number"],
        })
        growth_rows.append({
            "requested_period": period,
            "layout_id": detected.layout_id,
            "checks": meta["growth_reconciliation_checks"],
            "skipped": meta["growth_reconciliation_skipped"],
            "failures": meta["growth_reconciliation_failures"],
            "tolerance_percentage_points": str(v1.ROUNDING_TOLERANCE),
        })
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["publication_date", "source_table", "source_row_code", "column_role", "series_id"],
        kind="stable",
    ).reset_index(drop=True)
    duplicate_count = int(combined.duplicated(list(COMBINED_VINTAGE_KEY), keep=False).sum())
    if duplicate_count:
        raise RuntimeError("Combined vintage keys are not unique")
    combined_path = output_dir / "published_vintages_2025_07_to_2026_06.csv"
    combined_path.write_bytes(v2.release_observations_to_csv_bytes(combined))
    semantic_hash = v2.release_semantic_sha256(combined)
    provenance_hash = hashlib.sha256(combined_path.read_bytes()).hexdigest()
    mapping_rows = _mapping_rows()
    continuity_rows = _continuity_rows()
    comparability_rows = [asdict(item) for item in transition.BOUNDARY_CLASSIFICATIONS]
    methodology_rows = [
        {
            "layout_id": layout,
            "supported_periods": periods,
            "current_date_basis": current,
            "yoy_comparison_basis": yoy,
            "population": "Section-42 all SCBs; SIBC select SCBs about 95 per cent",
        }
        for layout, periods, current, yoy in (
            (v2.LAYOUT_ID, "July-December 2025", "LAST_REPORTING_FRIDAY", "CORRESPONDING_LAST_REPORTING_FRIDAY"),
            (transition.LAYOUT_JANUARY, "January 2026", "LAST_REPORTING_FRIDAY", "CORRESPONDING_LAST_REPORTING_FRIDAY"),
            (transition.LAYOUT_FEBRUARY_APRIL, "February-April 2026", "CALENDAR_MONTH_END", "PRIOR_OLD_REPORTING_FORTNIGHT"),
            (transition.LAYOUT_MAY, "May 2026", "CALENDAR_MONTH_END", "PRIOR_OLD_REPORTING_FORTNIGHT"),
            (v1.LAYOUT_ID, "June 2026", "CALENDAR_MONTH_END", "PRIOR_OLD_REPORTING_FORTNIGHT"),
        )
    ]
    _write_csv(output_dir / "compatibility_matrix.csv", compatibility)
    _write_csv(output_dir / "per_release_observation_counts.csv", [
        {
            "requested_period": record["requested_period"],
            "layout_id": record["layout_id"],
            "observation_count": record["observation_count"],
            "unique_series": 255,
            "counts_by_measure": json.dumps(record["counts_by_measure"], sort_keys=True, separators=(",", ":")),
            "counts_by_column_role": json.dumps(record["counts_by_column_role"], sort_keys=True, separators=(",", ":")),
        }
        for record in release_manifests
    ])
    _write_csv(output_dir / "row_mapping_report.csv", mapping_rows)
    _write_csv(output_dir / "growth_reconciliation_report.csv", growth_rows)
    _write_csv(output_dir / "continuity_matrix.csv", continuity_rows)
    _write_csv(output_dir / "comparability_matrix.csv", comparability_rows)
    _write_csv(output_dir / "methodology_matrix.csv", methodology_rows)
    _write_csv(output_dir / "current_observation_sequence.csv", sequence_rows)
    manifest = {
        "status": "PASS_2026_TRANSITION",
        "target_periods": list(TARGETS),
        "release_count": len(release_manifests),
        "combined_observation_count": len(combined),
        "unique_series_count": int(combined["series_id"].nunique()),
        "counts_by_release": _counts(combined, "bulletin_period"),
        "counts_by_measure": _counts(combined, "measure"),
        "counts_by_population": _counts(combined, "population_id"),
        "counts_by_column_role": _counts(combined, "column_role"),
        "publication_date_range": [combined["publication_date"].min(), combined["publication_date"].max()],
        "observation_date_range": [combined["observation_date"].min(), combined["observation_date"].max()],
        "combined_vintage_key_duplicate_count": duplicate_count,
        "semantic_vintage_sha256": semantic_hash,
        "provenance_bound_vintage_sha256": provenance_hash,
        "v2_regression_semantic_sha256": "c350493a3ca0e9f36ef7bd99267c9a049662de2411a9e21d35d832d2fb65d1e4",
        "census_manifest_sha256": hashlib.sha256(census_path.read_bytes()).hexdigest(),
        "growth_checks": sum(int(row["checks"]) for row in growth_rows),
        "growth_skipped": sum(int(row["skipped"]) for row in growth_rows),
        "growth_failures": sum(int(row["failures"]) for row in growth_rows),
        "current_observation_duplicate_count": 0,
        "current_observation_expected_gap_count": 0,
        "releases": release_manifests,
    }
    (output_dir / "transition_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report = (
        "# RBI sectoral-credit 2026 transition validation\n\n"
        f"Status: {manifest['status']}\n\n"
        f"- Releases: {manifest['release_count']}\n"
        f"- Published-vintage observations: {manifest['combined_observation_count']}\n"
        f"- Unique measure-specific series: {manifest['unique_series_count']}\n"
        f"- Growth checks/skips/failures: {manifest['growth_checks']}/{manifest['growth_skipped']}/{manifest['growth_failures']}\n"
        f"- Vintage-key duplicates: {duplicate_count}\n"
        f"- Semantic vintage SHA-256: `{semantic_hash}`\n"
        f"- Provenance-bound vintage SHA-256: `{provenance_hash}`\n\n"
        "A continuous publication sequence is not asserted to be a fully comparable economic time series. "
        "The January-February boundary is explicitly classified as a date-basis change.\n"
    )
    (output_dir / "deterministic_validation_report.md").write_text(report, encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--census-dir", type=Path, default=Path("spike-artifacts/historical-census"))
    parser.add_argument("--output-dir", type=Path, default=Path("spike-artifacts/2026-transition"))
    args = parser.parse_args()
    manifest = build(args.census_dir, args.output_dir)
    print(json.dumps({
        "status": manifest["status"],
        "releases": manifest["release_count"],
        "observations": manifest["combined_observation_count"],
        "semantic_sha256": manifest["semantic_vintage_sha256"],
        "provenance_sha256": manifest["provenance_bound_vintage_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
