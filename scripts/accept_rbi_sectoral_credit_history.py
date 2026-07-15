#!/usr/bin/env python3
"""Run the bounded live history-connector acceptance and exact offline replay."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from indiamacro import rbi

connector = importlib.import_module("indiamacro.rbi.sectoral_credit_history")


NON_FOOD_OUTSTANDING = "RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING"
AS_OF_DATE = "2026-03-31"


def _point(item: connector.HistoryObservation) -> dict[str, Any]:
    return {
        "observation_date": item.observation_date,
        "publication_date": item.publication_date,
        "value": None if item.value is None else str(item.value),
        "unit": item.unit,
        "population_id": item.population_id,
        "measure": item.measure,
        "layout_id": item.layout_id,
        "source_url": item.source_url,
        "source_sha256": item.source_sha256,
    }


def _request_inventory(
    result: connector.SectoralCreditHistoryResult,
) -> list[dict[str, str]]:
    requests = [{"method": "GET", "url": connector.ARCHIVE_URL, "purpose": "archive entry"}]
    for manifest in result.metadata.source_manifests:
        requests.extend(
            [
                {
                    "method": "POST",
                    "url": connector.ARCHIVE_URL,
                    "purpose": f"select issue {manifest.issue_month}",
                },
                {
                    "method": "GET",
                    "url": manifest.major_sectors_url,
                    "purpose": f"{manifest.issue_month} Table 15",
                },
                {
                    "method": "GET",
                    "url": manifest.industries_url,
                    "purpose": f"{manifest.issue_month} Table 16",
                },
            ]
        )
    return requests


def _snapshot(result: connector.SectoralCreditHistoryResult) -> dict[str, Any]:
    resolved = result.resolve()
    as_of = result.resolve(as_of=AS_OF_DATE)
    points = result.select(series_id=NON_FOOD_OUTSTANDING, view="current")
    manifests = result.metadata.source_manifests
    return {
        "release_count": result.metadata.release_count,
        "vintage_observation_count": len(result.vintages),
        "current_observation_count": len(result.current_observations),
        "unique_series_count": len(result.available_series),
        "semantic_vintage_sha256": result.metadata.semantic_vintage_sha256,
        "provenance_bound_vintage_sha256": result.metadata.provenance_bound_vintage_sha256,
        "semantic_current_sha256": result.metadata.semantic_current_sha256,
        "provenance_bound_current_sha256": result.metadata.provenance_bound_current_sha256,
        "resolved_observation_count": len(resolved.observations),
        "resolved_semantic_sha256": resolved.semantic_sha256,
        "resolved_provenance_bound_sha256": resolved.provenance_bound_sha256,
        "as_of": AS_OF_DATE,
        "as_of_resolved_observation_count": len(as_of.observations),
        "as_of_semantic_sha256": as_of.semantic_sha256,
        "as_of_provenance_bound_sha256": as_of.provenance_bound_sha256,
        "non_food_series_id": NON_FOOD_OUTSTANDING,
        "non_food_point_count": len(points),
        "non_food_points": [_point(item) for item in points],
        "methodology_boundaries": [
            {
                "from_issue": item.from_issue,
                "to_issue": item.to_issue,
                "classification": item.classification,
                "description": item.description,
            }
            for item in result.metadata.methodology_boundaries
        ],
        "release_manifests": [
            {
                "issue_month": item.issue_month,
                "publication_date": item.publication_date,
                "bundle_id": item.bundle_id,
                "layout_id": item.layout_id,
                "parser_version": item.parser_version,
                "major_sectors_url": item.major_sectors_url,
                "major_sectors_final_url": item.major_sectors_final_url,
                "major_sectors_sha256": item.major_sectors_sha256,
                "major_sectors_byte_size": item.major_sectors_byte_size,
                "industries_url": item.industries_url,
                "industries_final_url": item.industries_final_url,
                "industries_sha256": item.industries_sha256,
                "industries_byte_size": item.industries_byte_size,
                "semantic_release_sha256": item.semantic_release_sha256,
                "provenance_bound_release_sha256": item.provenance_bound_release_sha256,
                "structural_signature_sha256": item.structural_signature_sha256,
                "taxonomy_signature_sha256": item.taxonomy_signature_sha256,
                "methodology_signature_sha256": item.methodology_signature_sha256,
                "retrieved_at_utc": item.retrieved_at_utc,
            }
            for item in manifests
        ],
    }


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _evidence(
    result: connector.SectoralCreditHistoryResult,
    snapshot: dict[str, Any],
    *,
    live_request_count: int,
) -> dict[str, Any]:
    return {
        "status": "PASS_HISTORY_CONNECTOR",
        "source_route": connector.SOURCE_ROUTE,
        "supported_start_issue": connector.SUPPORTED_START,
        "supported_end_issue": connector.SUPPORTED_END,
        "cache_manifest_schema_version": connector.MANIFEST_SCHEMA_VERSION,
        "network_behavior": {
            "live_request_count": live_request_count,
            "requests": _request_inventory(result),
            "offline_session_creation_disabled": True,
            "offline_request_count": 0,
            "serial_processing": True,
            "chunk_size_bytes": connector.CHUNK_SIZE,
            "maximum_response_bytes": connector.MAX_RESPONSE_BYTES,
        },
        "snapshot": snapshot,
        "offline_replay_exact": True,
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        temporary.write_bytes(content)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def run(output_dir: Path) -> dict[str, Any]:
    cache_dir = output_dir / "acceptance-cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    live = rbi.sectoral_credit_history(refresh=True, cache_dir=cache_dir)
    live_snapshot = _snapshot(live)
    if live.metadata.request_count != 37:
        raise AssertionError(f"Expected 37 application requests, got {live.metadata.request_count}")
    requests = _request_inventory(live)
    if len(requests) != live.metadata.request_count:
        raise AssertionError("Recorded request inventory does not match request count")

    original_new_session = connector._new_session

    def forbidden_session():
        raise AssertionError("offline replay attempted to create a network session")

    connector._new_session = forbidden_session
    try:
        offline = rbi.sectoral_credit_history(offline=True, cache_dir=cache_dir)
    finally:
        connector._new_session = original_new_session
    offline_snapshot = _snapshot(offline)
    if live_snapshot != offline_snapshot:
        raise AssertionError("Live and offline snapshots differ")
    if live.vintages != offline.vintages or live.current_observations != offline.current_observations:
        raise AssertionError("Live and offline observation records differ")

    evidence = _evidence(
        live,
        live_snapshot,
        live_request_count=live.metadata.request_count,
    )
    content = _canonical_bytes(evidence)
    independently_regenerated = _canonical_bytes(
        _evidence(
            offline,
            offline_snapshot,
            live_request_count=live.metadata.request_count,
        )
    )
    if content != independently_regenerated:
        raise AssertionError("Independently regenerated acceptance evidence differs")
    _atomic_write(output_dir / "live_acceptance_manifest.json", content)
    _atomic_write(
        output_dir / "live_acceptance_manifest.second.json", independently_regenerated
    )
    if (output_dir / "live_acceptance_manifest.json").read_bytes() != (
        output_dir / "live_acceptance_manifest.second.json"
    ).read_bytes():
        raise AssertionError("Acceptance evidence is not byte deterministic")
    return evidence


def replay_existing(output_dir: Path) -> dict[str, Any]:
    """Regenerate acceptance evidence from cache with session creation disabled."""
    manifest_path = output_dir / "live_acceptance_manifest.json"
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    live_request_count = existing["network_behavior"]["live_request_count"]
    original_new_session = connector._new_session

    def forbidden_session():
        raise AssertionError("offline evidence replay attempted to create a network session")

    connector._new_session = forbidden_session
    try:
        offline = rbi.sectoral_credit_history(
            offline=True,
            cache_dir=output_dir / "acceptance-cache",
        )
    finally:
        connector._new_session = original_new_session
    evidence = _evidence(
        offline,
        _snapshot(offline),
        live_request_count=live_request_count,
    )
    regenerated = _canonical_bytes(evidence)
    if regenerated != _canonical_bytes(existing):
        raise AssertionError("Offline regeneration differs from live acceptance evidence")
    _atomic_write(output_dir / "live_acceptance_manifest.second.json", regenerated)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("spike-artifacts/history-connector-v1"),
    )
    parser.add_argument("--offline-existing", action="store_true")
    args = parser.parse_args()
    evidence = replay_existing(args.output_dir) if args.offline_existing else run(args.output_dir)
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
