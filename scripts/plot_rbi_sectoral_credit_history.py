#!/usr/bin/env python3
"""Generate the reproducible RBI Non-food Credit history demonstration."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any, Sequence

from indiamacro import __version__, rbi


OUTSTANDING_SERIES_ID = "RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING"
GROWTH_SERIES_ID = "RBI.SECTION42.NON_FOOD_CREDIT.YOY_GROWTH_REPORTED"
OUTPUT_STEM = "rbi_non_food_credit_history"
CSV_COLUMNS = (
    "observation_date",
    "publication_date",
    "outstanding_inr_crore",
    "outstanding_inr_lakh_crore",
    "reported_yoy_growth_percent",
    "population_id",
    "outstanding_unit",
    "growth_unit",
    "methodology_regime",
    "comparability_classification",
    "outstanding_source_url",
    "growth_source_url",
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _normalize_svg(path: Path) -> None:
    content = b"\n".join(line.rstrip() for line in path.read_bytes().splitlines()) + b"\n"
    path.write_bytes(content)


def _csv_bytes(points: Sequence[rbi.SectoralCreditChartPoint]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for point in points:
        writer.writerow(
            {
                "observation_date": point.observation_date,
                "publication_date": point.publication_date,
                "outstanding_inr_crore": str(point.outstanding_inr_crore),
                "outstanding_inr_lakh_crore": str(point.outstanding_inr_lakh_crore),
                "reported_yoy_growth_percent": str(point.reported_yoy_growth_percent),
                "population_id": point.population_id,
                "outstanding_unit": point.outstanding_unit,
                "growth_unit": point.growth_unit,
                "methodology_regime": point.methodology_regime,
                "comparability_classification": point.comparability_classification,
                "outstanding_source_url": point.outstanding_source_url,
                "growth_source_url": point.growth_source_url,
            }
        )
    return output.getvalue().encode("utf-8")


def _semantic_chart_rows(
    points: Sequence[rbi.SectoralCreditChartPoint],
) -> list[dict[str, Any]]:
    return [
        {
            "observation_date": point.observation_date,
            "publication_date": point.publication_date,
            "outstanding_inr_crore": str(point.outstanding_inr_crore),
            "outstanding_inr_lakh_crore": str(point.outstanding_inr_lakh_crore),
            "reported_yoy_growth_percent": str(point.reported_yoy_growth_percent),
            "population_id": point.population_id,
            "outstanding_unit": point.outstanding_unit,
            "growth_unit": point.growth_unit,
            "methodology_regime": point.methodology_regime,
            "comparability_classification": point.comparability_classification,
        }
        for point in points
    ]


def _source_entries(history: rbi.SectoralCreditHistoryResult) -> list[dict[str, Any]]:
    return [
        {
            "issue_month": item.issue_month,
            "publication_date": item.publication_date,
            "major_sectors_url": item.major_sectors_final_url,
            "industries_url": item.industries_final_url,
            "major_sectors_sha256": item.major_sectors_sha256,
            "industries_sha256": item.industries_sha256,
        }
        for item in history.metadata.source_manifests
    ]


def _boundary_entries(history: rbi.SectoralCreditHistoryResult) -> list[dict[str, str]]:
    return [asdict(item) for item in history.metadata.methodology_boundaries]


def _manifest(
    history: rbi.SectoralCreditHistoryResult,
    points: Sequence[rbi.SectoralCreditChartPoint],
    *,
    csv_content: bytes,
    output_hashes: dict[str, str],
) -> dict[str, Any]:
    chart_rows = _semantic_chart_rows(points)
    chart_data_sha256 = _sha256(
        json.dumps(chart_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    output_filenames = sorted([*output_hashes, f"{OUTPUT_STEM}_manifest.json"])
    deterministic = {
        "indiamacro_version": __version__,
        "selected_start_issue": history.metadata.selected_start_issue,
        "selected_end_issue": history.metadata.selected_end_issue,
        "supported_start_issue": history.metadata.supported_start_issue,
        "supported_end_issue": history.metadata.supported_end_issue,
        "semantic_current_observations_sha256": history.metadata.semantic_current_sha256,
        "outstanding_series_id": OUTSTANDING_SERIES_ID,
        "growth_series_id": GROWTH_SERIES_ID,
        "chart_data_sha256": chart_data_sha256,
        "csv_sha256": _sha256(csv_content),
        "point_count": len(points),
        "methodology_boundaries": _boundary_entries(history),
        "output_filenames": output_filenames,
    }
    semantic_manifest_sha256 = _sha256(
        json.dumps(deterministic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    source_manifests = history.metadata.source_manifests
    generation_timestamp = max(item.retrieved_at_utc for item in source_manifests)
    return {
        **deterministic,
        "generation_timestamp_utc": generation_timestamp,
        "generation_timestamp_basis": "LATEST_SOURCE_INGESTION",
        "semantic_manifest_sha256": semantic_manifest_sha256,
        "source_publications": _source_entries(history),
        "output_sha256": output_hashes,
    }


def _commit_output_set(staging: Path, output_dir: Path, filenames: Sequence[str]) -> None:
    backups = staging / ".backups"
    backups.mkdir()
    committed: list[str] = []
    try:
        for filename in filenames:
            target = output_dir / filename
            if target.exists():
                shutil.copy2(target, backups / filename)
            os.replace(staging / filename, target)
            committed.append(filename)
    except OSError:
        for filename in reversed(committed):
            target = output_dir / filename
            backup = backups / filename
            if backup.exists():
                os.replace(backup, target)
            else:
                target.unlink(missing_ok=True)
        raise


def generate_demo(
    *,
    start_issue: str = "2025-07",
    end_issue: str = "2026-06",
    cache_dir: Path | str | None = None,
    offline: bool = False,
    refresh: bool = False,
    output_dir: Path | str = Path("spike-artifacts/sectoral-credit-demo-v1"),
    output_format: str = "both",
) -> dict[str, Any]:
    """Generate chart, CSV, and provenance manifest through public IndiaMacro APIs."""
    if output_format not in {"both", "svg", "png"}:
        raise ValueError("output_format must be one of: both, svg, png")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".tmp-sectoral-credit-demo-", dir=output_dir))
    previous_backend = os.environ.get("MPLBACKEND")
    previous_config = os.environ.get("MPLCONFIGDIR")
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("MPLCONFIGDIR", str(staging / ".matplotlib"))
    try:
        history = rbi.sectoral_credit_history(
            start_issue=start_issue,
            end_issue=end_issue,
            cache_dir=cache_dir,
            offline=offline,
            refresh=refresh,
        )
        points = rbi.sectoral_credit_history_chart_data(
            history,
            outstanding_series_id=OUTSTANDING_SERIES_ID,
            growth_series_id=GROWTH_SERIES_ID,
        )
        figure, _axes = rbi.plot_sectoral_credit_history(
            history,
            outstanding_series_id=OUTSTANDING_SERIES_ID,
            growth_series_id=GROWTH_SERIES_ID,
        )
        filenames = [f"{OUTPUT_STEM}.csv", f"{OUTPUT_STEM}_manifest.json"]
        if output_format in {"both", "svg"}:
            filenames.append(f"{OUTPUT_STEM}.svg")
            figure.savefig(
                staging / f"{OUTPUT_STEM}.svg",
                format="svg",
                metadata={"Creator": f"IndiaMacro {__version__}", "Date": None},
            )
            _normalize_svg(staging / f"{OUTPUT_STEM}.svg")
        if output_format in {"both", "png"}:
            filenames.append(f"{OUTPUT_STEM}.png")
            figure.savefig(
                staging / f"{OUTPUT_STEM}.png",
                format="png",
                dpi=160,
                metadata={"Software": f"IndiaMacro {__version__}"},
            )
        csv_content = _csv_bytes(points)
        (staging / f"{OUTPUT_STEM}.csv").write_bytes(csv_content)
        output_hashes = {
            filename: _sha256((staging / filename).read_bytes())
            for filename in sorted(filenames)
            if not filename.endswith("_manifest.json")
        }
        manifest = _manifest(
            history,
            points,
            csv_content=csv_content,
            output_hashes=output_hashes,
        )
        manifest_name = f"{OUTPUT_STEM}_manifest.json"
        (staging / manifest_name).write_bytes(_canonical_json(manifest))
        _commit_output_set(staging, output_dir, sorted(filenames))
        return manifest
    finally:
        if previous_backend is None:
            os.environ.pop("MPLBACKEND", None)
        else:
            os.environ["MPLBACKEND"] = previous_backend
        if previous_config is None:
            os.environ.pop("MPLCONFIGDIR", None)
        else:
            os.environ["MPLCONFIGDIR"] = previous_config
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-issue", default="2025-07")
    parser.add_argument("--end-issue", default="2026-06")
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("spike-artifacts/sectoral-credit-demo-v1"),
    )
    parser.add_argument("--format", choices=("both", "svg", "png"), default="both")
    args = parser.parse_args()
    manifest = generate_demo(
        start_issue=args.start_issue,
        end_issue=args.end_issue,
        cache_dir=args.cache_dir,
        offline=args.offline,
        refresh=args.refresh,
        output_dir=args.output_dir,
        output_format=args.format,
    )
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
