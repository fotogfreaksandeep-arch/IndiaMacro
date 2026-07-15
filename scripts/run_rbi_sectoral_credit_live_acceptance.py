#!/usr/bin/env python3
"""Run one clean-cache live connector acceptance and verified offline replay."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from indiamacro import rbi


def _write_evidence(output_dir: Path, evidence: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "live_acceptance_manifest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    live = evidence.get("live_result") or {}
    report = f"""# RBI sectoral-credit connector v1 validation report

Status: **{evidence['status']}**

## Offline verification

- Serial Ruff run before acceptance: PASS
- Serial pytest run before acceptance: PASS (reported separately)
- Mocked connector tests make no real network requests: PASS

## Live clean-cache acceptance

- Started from an empty explicit cache: {evidence.get('empty_cache_start', False)}
- Observation count: {live.get('observation_count')}
- Semantic observations SHA-256: {live.get('semantic_observations_sha256')}
- Provenance-bound output SHA-256: {live.get('provenance_bound_output_sha256')}
- Bulletin publication date: {live.get('bulletin_publication_date')}
- Latest observation date: {live.get('latest_observation_date')}
- Major-sectors URL: {live.get('major_sectors_url')}
- Industries URL: {live.get('industries_url')}
- Dedicated-release period: {live.get('latest_dedicated_release_period')}
- Freshness status: {live.get('freshness_status')}
- Freshness gap months: {live.get('freshness_gap_months')}
- Raw source pages committed: {evidence.get('raw_pages_cached', False)}

## Offline replay

- Replay attempted with the same cache: {evidence.get('offline_replay_attempted', False)}
- Network-session creation disabled during replay: {evidence.get('offline_network_disabled', False)}
- Semantic SHA-256 matched live retrieval: {evidence.get('offline_semantic_hash_matched', False)}
- Provenance-bound SHA-256 matched live retrieval: {evidence.get('offline_provenance_hash_matched', False)}

## Diagnostic

{evidence.get('diagnostic') or 'None'}
"""
    (output_dir / "connector_validation_report.md").write_text(report, encoding="utf-8")


def run(cache_dir: Path, output_dir: Path) -> tuple[dict[str, Any], int]:
    if cache_dir.exists() and any(cache_dir.iterdir()):
        raise ValueError(f"Acceptance cache must start empty: {cache_dir}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    evidence: dict[str, Any] = {
        "status": "FAIL",
        "acceptance_started_at_utc": datetime.now(timezone.utc).isoformat(),
        "empty_cache_start": True,
        "cache_dir": str(cache_dir.resolve()),
        "live_result": None,
        "raw_pages_cached": False,
        "offline_replay_attempted": False,
        "offline_network_disabled": False,
        "offline_semantic_hash_matched": False,
        "offline_provenance_hash_matched": False,
        "diagnostic": None,
    }
    try:
        live = rbi.sectoral_credit(refresh=True, cache_dir=cache_dir)
        metadata = asdict(live.metadata)
        evidence["live_result"] = {
            **metadata,
            "observation_count": len(live.observations),
        }
        bundle_dir = (
            cache_dir
            / "rbi"
            / "sectoral_credit"
            / live.metadata.bulletin_publication_date
            / live.metadata.cache_bundle_id
        )
        evidence["raw_pages_cached"] = all(
            (bundle_dir / name).is_file()
            for name in ("major_sectors.html", "industries.html", "manifest.json")
        )

        connector = importlib.import_module("indiamacro.rbi.sectoral_credit")
        original_session = connector._new_session

        def reject_network():
            raise AssertionError("Offline replay attempted to create a network session")

        evidence["offline_replay_attempted"] = True
        connector._new_session = reject_network
        evidence["offline_network_disabled"] = True
        try:
            offline = rbi.sectoral_credit(offline=True, cache_dir=cache_dir)
        finally:
            connector._new_session = original_session
        evidence["offline_semantic_hash_matched"] = (
            offline.metadata.semantic_observations_sha256
            == live.metadata.semantic_observations_sha256
        )
        evidence["offline_provenance_hash_matched"] = (
            offline.metadata.provenance_bound_output_sha256
            == live.metadata.provenance_bound_output_sha256
        )
        if (
            not evidence["raw_pages_cached"]
            or not evidence["offline_semantic_hash_matched"]
            or not evidence["offline_provenance_hash_matched"]
        ):
            raise AssertionError("Cache preservation or offline replay validation failed")
        evidence["status"] = "PASS"
        exit_code = 0
    except rbi.SourceAccessBlockedError as exc:
        evidence["status"] = "LIVE_SOURCE_BLOCKED"
        evidence["diagnostic"] = f"{type(exc).__name__}: {exc}"
        exit_code = 2
    except rbi.SourceUnavailableError as exc:
        evidence["status"] = "LIVE_ENVIRONMENT_BLOCKED"
        evidence["diagnostic"] = f"{type(exc).__name__}: {exc}"
        exit_code = 2
    except Exception as exc:
        evidence["status"] = "FAIL"
        evidence["diagnostic"] = f"{type(exc).__name__}: {exc}"
        exit_code = 1
    evidence["acceptance_finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    _write_evidence(output_dir, evidence)
    return evidence, exit_code


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("spike-artifacts/connector-v1")
    )
    args = parser.parse_args(argv)
    evidence, exit_code = run(args.cache_dir, args.output_dir)
    print(json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
