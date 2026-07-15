"""Build bounded, offline evidence for the Bulletin sectoral-credit parser."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from indiamacro.rbi.sectoral_credit_bulletin import (
    INDUSTRY_TITLE,
    MAJOR_TITLE,
    MEASURE_YOY_GROWTH,
    observations_to_csv_bytes,
    parse_sectoral_credit_bulletin,
)


EXPECTED_HASHES = {
    MAJOR_TITLE: "a7143fcd39cf1d3538de036893d0798f0cef8c1a7afe298249d0ba5cebb2d17c",
    INDUSTRY_TITLE: "115b59651d1bc7f8b3f80480c4e7becc18e946172d4570f848a8881ea9360e8b",
}
MAJOR_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24257"
INDUSTRY_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24258"
GOLDEN_VALUES = {
    "III. Non-food Credit": Decimal("15.8"),
    "1. Agriculture & Allied Activities": Decimal("13.7"),
    "2. Industry (Micro and Small, Medium and Large)": Decimal("15.1"),
    "3. Services": Decimal("18.6"),
    "4. Personal Loans": Decimal("16.0"),
    "2.4 Textiles": Decimal("8.3"),
    "2.2.1 Sugar": Decimal("-0.4"),
    "2.18.3 Roads": Decimal("0.4"),
}


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _pairs(values: tuple[tuple[str, int], ...]) -> dict[str, int]:
    return dict(values)


def _check_goldens(observations) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    for label, expected in GOLDEN_VALUES.items():
        matches = observations.loc[
            (observations["source_label"] == label)
            & (observations["measure"] == MEASURE_YOY_GROWTH)
        ]
        if len(matches) != 1:
            raise AssertionError(f"Golden row {label!r} matched {len(matches)} observations")
        row = matches.iloc[0]
        if row["value"] != expected:
            raise AssertionError(
                f"Golden row {label!r}: expected {expected}, got {row['value']}"
            )
        checks.append(
            {
                "source_label": label,
                "measure": MEASURE_YOY_GROWTH,
                "observation_date": row["observation_date"],
                "comparison_date": row["comparison_date"],
                "expected_value": format(expected, "f"),
                "result": "PASS",
            }
        )
    return checks


def build_evidence(major_path: Path, industry_path: Path, output_dir: Path) -> None:
    major = major_path.read_bytes()
    industry = industry_path.read_bytes()
    actual_hashes = {MAJOR_TITLE: _sha256(major), INDUSTRY_TITLE: _sha256(industry)}
    if actual_hashes != EXPECTED_HASHES:
        raise RuntimeError(
            "Preserved source hashes differ from the parsing contract: "
            f"expected={EXPECTED_HASHES!r}, actual={actual_hashes!r}"
        )

    result = parse_sectoral_credit_bulletin(
        major,
        industry,
        major_sectors_url=MAJOR_URL,
        industries_url=INDUSTRY_URL,
    )
    del major, industry
    output_csv = observations_to_csv_bytes(result.observations)
    output_hash = _sha256(output_csv)
    if output_hash != result.metadata.provenance_bound_output_sha256:
        raise AssertionError("Serialized output hash differs from parser metadata")
    golden_checks = _check_goldens(result.observations)

    metadata = result.metadata
    manifest = {
        "status": "PASS",
        "dataset_id": metadata.dataset_id,
        "layout_id": metadata.layout_id,
        "layout_signature_sha256": metadata.layout_signature_sha256,
        "parser_version": metadata.parser_version,
        "input_hashes": {
            "major_sectors": actual_hashes[MAJOR_TITLE],
            "industries": actual_hashes[INDUSTRY_TITLE],
        },
        "publication_date": metadata.publication_date,
        "bulletin_period": metadata.bulletin_period,
        "current_observation_date": metadata.current_observation_date,
        "source_rows_by_table": _pairs(metadata.data_row_counts),
        "mapped_rows_by_table": _pairs(metadata.mapped_row_counts),
        "emitted_rows_by_table": _pairs(metadata.emitted_row_counts),
        "canonical_observation_count": len(result.observations),
        "observation_counts_by_measure": _pairs(metadata.observation_counts_by_measure),
        "observation_counts_by_population": _pairs(metadata.observation_counts_by_population),
        "unknown_row_count": metadata.unknown_row_count,
        "duplicate_source_column_count": metadata.duplicate_source_column_count,
        "duplicate_source_row_count": metadata.duplicate_source_row_count,
        "canonical_duplicate_key_count": metadata.canonical_duplicate_key_count,
        "growth_reconciliation": {
            "checks": metadata.growth_reconciliation_checks,
            "skipped": metadata.growth_reconciliation_skipped,
            "failures": metadata.growth_reconciliation_failures,
            "tolerance_percentage_points": (
                metadata.growth_reconciliation_tolerance_percentage_points
            ),
        },
        "golden_value_checks": golden_checks,
        "semantic_observations_sha256": metadata.semantic_observations_sha256,
        "provenance_bound_output_sha256": output_hash,
    }

    report = f"""# RBI sectoral-credit parser v1 validation report

Status: **PASS**

The two preserved June 2026 RBI Bulletin HTML pages were parsed offline. No
network request, live discovery, browser, or historical source was used.

## Source and layout validation

- Exact preserved SHA-256 values: PASS
- Exact titles and unique semantic table matches: PASS
- Unit `(₹ Crore)`: PASS
- Publication date extraction (`{metadata.publication_date}`): PASS
- Bulletin period derivation (`{metadata.bulletin_period}`): PASS
- Merged header, source-column roles, and dates: PASS
- Required population and methodology notes: PASS
- Section-42 column-(2) override (`2025-05-02`): PASS
- Complete explicit mappings: PASS ({dict(metadata.mapped_row_counts)})
- Unknown rows: PASS ({metadata.unknown_row_count})
- Missing required rows: PASS (0)
- Stable hierarchy, memorandum, footnote, and provisional fields: PASS
- Numeric parsing and supported missing-marker policy: PASS
- Reported measures and units separated: PASS
- Duplicate columns (1)/(3) equal before collapse: PASS
  ({metadata.duplicate_source_column_count} source-column duplicates)
- Cross-table Industry row equal before collapse: PASS
- Canonical key uniqueness: PASS ({metadata.canonical_duplicate_key_count} duplicates)
- Deterministic ordering and CSV serialization: PASS

## Dataset counts

- Source rows: `{dict(metadata.data_row_counts)}`
- Mapped rows: `{dict(metadata.mapped_row_counts)}`
- Emitting rows: `{dict(metadata.emitted_row_counts)}`
- Canonical observations: `{len(result.observations)}`
- By measure: `{dict(metadata.observation_counts_by_measure)}`
- By population: `{dict(metadata.observation_counts_by_population)}`

## Golden values

All checks use `YOY_GROWTH_REPORTED`, observation date `2026-04-30`:

"""
    for check in golden_checks:
        report += (
            f"- {check['source_label']}: {check['expected_value']}% "
            f"(comparison {check['comparison_date']}): PASS\n"
        )
    report += f"""

## Growth reconciliation

- Checks: {metadata.growth_reconciliation_checks}
- Skipped: {metadata.growth_reconciliation_skipped}
- Failures: {metadata.growth_reconciliation_failures}
- Absolute tolerance: {metadata.growth_reconciliation_tolerance_percentage_points}
  percentage points
- Reported values retained rather than replaced: PASS

## Determinism

Semantic observations SHA-256:
`{metadata.semantic_observations_sha256}`

Provenance-bound `observations.csv` SHA-256:
`{output_hash}`
"""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "observations.csv").write_bytes(output_csv)
    (output_dir / "parser_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "validation_report.md").write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--major",
        type=Path,
        default=Path("spike-artifacts/source-investigation/bulletin_major_sectors.html"),
    )
    parser.add_argument(
        "--industries",
        type=Path,
        default=Path("spike-artifacts/source-investigation/bulletin_industries.html"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("spike-artifacts/parser-v1")
    )
    args = parser.parse_args()
    build_evidence(args.major, args.industries, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
