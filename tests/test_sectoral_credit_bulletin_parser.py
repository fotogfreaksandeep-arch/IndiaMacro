from __future__ import annotations

import hashlib
import re
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from indiamacro.rbi.sectoral_credit_bulletin import (
    AmbiguousTableError,
    DataValidationError,
    INDUSTRY_TITLE,
    MAJOR_TITLE,
    MEASURE_FY_GROWTH,
    MEASURE_OUTSTANDING,
    MEASURE_YOY_GROWTH,
    OBSERVATION_COLUMNS,
    POPULATION_ALL,
    POPULATION_SELECT,
    UnmappedSeriesError,
    UnsupportedLayoutError,
    _validate_canonical_keys,
    observations_to_csv_bytes,
    parse_sectoral_credit_bulletin,
)


FIXTURES = Path(__file__).parent / "fixtures"
FULL_FIXTURES = Path(__file__).parents[1] / "spike-artifacts" / "source-investigation"
MAJOR_FIXTURE = FIXTURES / "rbi_sectoral_credit_major_v1.html"
INDUSTRY_FIXTURE = FIXTURES / "rbi_sectoral_credit_industries_v1.html"
MAJOR_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24257"
INDUSTRY_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24258"

EXPECTED_FULL_HASHES = {
    "bulletin_major_sectors.html": (
        "a7143fcd39cf1d3538de036893d0798f0cef8c1a7afe298249d0ba5cebb2d17c"
    ),
    "bulletin_industries.html": (
        "115b59651d1bc7f8b3f80480c4e7becc18e946172d4570f848a8881ea9360e8b"
    ),
}
EXPECTED_COMPACT_HASHES = {
    "rbi_sectoral_credit_major_v1.html": (
        "575b8d6f4470edbca5ad3f4b1b5da1d06b16a29ebaac03990b375363b2a5ec20"
    ),
    "rbi_sectoral_credit_industries_v1.html": (
        "c3a46541192e24fa8d7f4c93a2a24c7f331b2a466b237a10c4d70397e66a6518"
    ),
}


def _contents() -> tuple[bytes, bytes]:
    return MAJOR_FIXTURE.read_bytes(), INDUSTRY_FIXTURE.read_bytes()


def _parse(major: bytes | str | None = None, industry: bytes | str | None = None):
    fixture_major, fixture_industry = _contents()
    return parse_sectoral_credit_bulletin(
        fixture_major if major is None else major,
        fixture_industry if industry is None else industry,
        major_sectors_url=MAJOR_URL,
        industries_url=INDUSTRY_URL,
    )


def _value(result, source_label: str, measure: str) -> Decimal | None:
    matches = result.observations.loc[
        (result.observations["source_label"] == source_label)
        & (result.observations["measure"] == measure),
        "value",
    ]
    assert len(matches) == 1
    return matches.iloc[0]


def test_fixture_hashes_are_pinned() -> None:
    for name, expected in EXPECTED_COMPACT_HASHES.items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == expected

    for name, expected in EXPECTED_FULL_HASHES.items():
        path = FULL_FIXTURES / name
        if path.exists():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


@pytest.mark.local_evidence
def test_full_preserved_pages_parse_with_expected_provenance() -> None:
    major_path = FULL_FIXTURES / "bulletin_major_sectors.html"
    industry_path = FULL_FIXTURES / "bulletin_industries.html"
    if not major_path.exists() or not industry_path.exists():
        pytest.skip("ignored full-page regression fixtures are not present")

    result = _parse(major_path.read_bytes(), industry_path.read_bytes())

    assert dict(result.metadata.source_hashes) == {
        MAJOR_TITLE: EXPECTED_FULL_HASHES[major_path.name],
        INDUSTRY_TITLE: EXPECTED_FULL_HASHES[industry_path.name],
    }
    assert len(result.observations) == 425


def test_successful_parse_schema_mapping_counts_and_metadata() -> None:
    result = _parse()

    assert tuple(result.observations.columns) == OBSERVATION_COLUMNS
    assert result.observations.shape == (425, 24)
    assert result.metadata.publication_date == "2026-06-22"
    assert result.metadata.bulletin_period == "June 2026"
    assert result.metadata.current_observation_date == "2026-04-30"
    assert dict(result.metadata.data_row_counts) == {MAJOR_TITLE: 44, INDUSTRY_TITLE: 43}
    assert dict(result.metadata.mapped_row_counts) == {MAJOR_TITLE: 44, INDUSTRY_TITLE: 43}
    assert dict(result.metadata.emitted_row_counts) == {MAJOR_TITLE: 43, INDUSTRY_TITLE: 42}
    assert dict(result.metadata.observation_counts_by_measure) == {
        MEASURE_OUTSTANDING: 255,
        MEASURE_FY_GROWTH: 85,
        MEASURE_YOY_GROWTH: 85,
    }
    assert dict(result.metadata.observation_counts_by_population) == {
        POPULATION_ALL: 15,
        POPULATION_SELECT: 410,
    }
    assert result.metadata.unknown_row_count == 0
    assert result.metadata.canonical_duplicate_key_count == 0
    assert (
        result.metadata.semantic_observations_sha256
        == "e3efd9e834b63c3e9ca1f797de5eee86cc572c448c05fe84b3ab543c2140ce35"
    )
    assert len(result.notes) == 2
    assert all("With effect from December 31, 2025" in note.text for note in result.notes)


def test_output_order_and_hash_are_deterministic_for_bytes_and_text() -> None:
    major, industry = _contents()
    first = _parse(major, industry)
    second = _parse(major.decode("utf-8"), industry.decode("utf-8"))

    first_bytes = observations_to_csv_bytes(first.observations)
    second_bytes = observations_to_csv_bytes(second.observations)
    assert first_bytes == second_bytes
    assert (
        hashlib.sha256(first_bytes).hexdigest()
        == first.metadata.provenance_bound_output_sha256
    )
    assert (
        first.metadata.provenance_bound_output_sha256
        == second.metadata.provenance_bound_output_sha256
    )
    assert (
        first.metadata.semantic_observations_sha256
        == second.metadata.semantic_observations_sha256
    )
    assert list(first.observations.iloc[:5]["measure"]) == [
        MEASURE_OUTSTANDING,
        MEASURE_OUTSTANDING,
        MEASURE_OUTSTANDING,
        MEASURE_FY_GROWTH,
        MEASURE_YOY_GROWTH,
    ]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("III. Non-food Credit", Decimal("15.8")),
        ("1. Agriculture & Allied Activities", Decimal("13.7")),
        ("2. Industry (Micro and Small, Medium and Large)", Decimal("15.1")),
        ("3. Services", Decimal("18.6")),
        ("4. Personal Loans", Decimal("16.0")),
    ],
)
def test_table_15_yoy_golden_values(label: str, expected: Decimal) -> None:
    result = _parse()
    assert _value(result, label, MEASURE_YOY_GROWTH) == expected


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("2.4 Textiles", Decimal("8.3")),
        ("2.2.1 Sugar", Decimal("-0.4")),
        ("2.18.3 Roads", Decimal("0.4")),
    ],
)
def test_table_16_yoy_golden_values(label: str, expected: Decimal) -> None:
    result = _parse()
    row = result.observations.loc[
        (result.observations["source_label"] == label)
        & (result.observations["measure"] == MEASURE_YOY_GROWTH)
    ].iloc[0]
    assert row["observation_date"] == "2026-04-30"
    assert row["comparison_date"] == "2025-04-18"
    assert row["value"] == expected


def test_mixed_populations_and_section42_date_override() -> None:
    result = _parse()
    section42 = result.observations[result.observations["source_row_code"] == "III"]
    agriculture = result.observations[
        result.observations["source_label"] == "1. Agriculture & Allied Activities"
    ]

    assert set(section42["population_id"]) == {POPULATION_ALL}
    assert set(agriculture["population_id"]) == {POPULATION_SELECT}
    assert "2025-05-02" in set(section42["observation_date"])
    assert "2025-04-18" not in set(section42["observation_date"])
    assert "2025-04-18" in set(agriculture["observation_date"])
    section42_yoy = section42[section42["measure"] == MEASURE_YOY_GROWTH].iloc[0]
    assert section42_yoy["comparison_date"] == "2025-05-02"


def test_hierarchy_footnotes_and_memorandum_items_are_stable() -> None:
    result = _parse()
    wholesale = result.observations[
        (result.observations["source_row_code"] == "3.7.1")
        & (result.observations["measure"] == MEASURE_OUTSTANDING)
    ].iloc[0]
    priority = result.observations[
        (result.observations["source_row_code"] == "(i)")
        & (result.observations["measure"] == MEASURE_OUTSTANDING)
    ].iloc[0]

    assert wholesale["parent_row_code"] == "3.7"
    assert wholesale["sector_level_3"] == "Wholesale Trade"
    assert wholesale["footnote_references"] == '["1"]'
    assert bool(priority["is_memorandum"]) is True
    assert priority["parent_row_code"] == "5"
    assert not (result.observations["source_label"] == "5. Priority Sector (Memo)").any()


def test_duplicate_columns_and_cross_table_row_are_collapsed() -> None:
    result = _parse()

    assert result.metadata.duplicate_source_column_count == 86
    assert result.metadata.duplicate_source_row_count == 1
    assert len(result.observations[result.observations["series_id"] == "RBI.SIBC.INDUSTRY.OUTSTANDING"]) == 3


def test_growth_reconciliation_passes_all_available_values() -> None:
    metadata = _parse().metadata
    assert metadata.growth_reconciliation_checks == 172
    assert metadata.growth_reconciliation_skipped == 0
    assert metadata.growth_reconciliation_failures == 0
    assert metadata.growth_reconciliation_tolerance_percentage_points == "0.11"


def test_supported_missing_marker_is_preserved_as_null_and_skips_reconciliation() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace("<td>-0.7</td>", "<td>—</td>", 1)
    result = _parse(major=major)
    bank_fy = result.observations[
        (result.observations["source_row_code"] == "I")
        & (result.observations["measure"] == MEASURE_FY_GROWTH)
    ].iloc[0]

    assert bank_fy["value"] is None
    assert result.metadata.growth_reconciliation_checks == 171
    assert result.metadata.growth_reconciliation_skipped == 1


def test_malformed_numeric_value_fails() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(
        "<td>21361435</td>", "<td>21x</td>", 1
    )
    with pytest.raises(DataValidationError, match="Malformed numeric value"):
        _parse(major=major)


def test_unknown_row_fails() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(
        "3.1 Transport Operators", "3.1 Unknown Activity", 1
    )
    with pytest.raises(UnmappedSeriesError, match="unknown rows"):
        _parse(major=major)


def test_missing_required_row_fails() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8")
    major, count = re.subn(
        r"<tr><td>2\.1 Micro and Small</td>.*?</tr>", "", major, count=1
    )
    assert count == 1
    with pytest.raises(UnmappedSeriesError, match="missing required rows"):
        _parse(major=major)


def test_altered_title_fails() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(MAJOR_TITLE, "15. Altered", 1)
    with pytest.raises(UnsupportedLayoutError, match="exact normalized title"):
        _parse(major=major)


def test_ambiguous_title_fails() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(
        "</body>", f"<table><tr><td>{MAJOR_TITLE}</td></tr></table></body>", 1
    )
    with pytest.raises(AmbiguousTableError, match="Multiple tables"):
        _parse(major=major)


def test_missing_population_note_fails() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(
        "Section-42 return", "aggregate return", 1
    )
    with pytest.raises(UnsupportedLayoutError, match="population/note markers missing"):
        _parse(major=major)


def test_missing_required_header_fails() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(
        "Outstanding as on", "Outstanding", 1
    )
    with pytest.raises(UnsupportedLayoutError, match="matching data table not found"):
        _parse(major=major)


def test_disagreeing_duplicate_source_columns_fail() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(
        "<td>18284957</td><td>21361435</td><td>21211828</td>",
        "<td>18284957</td><td>21361436</td><td>21211828</td>",
        1,
    )
    with pytest.raises(DataValidationError, match=r"columns \(1\) and \(3\) disagree"):
        _parse(major=major)


def test_duplicate_cross_table_industry_row_fails_when_values_disagree() -> None:
    industry = INDUSTRY_FIXTURE.read_text(encoding="utf-8").replace(
        "<td>4584304</td><td>3943927</td><td>4584304</td>",
        "<td>4584305</td><td>3943927</td><td>4584305</td>",
        1,
    )
    with pytest.raises(DataValidationError, match="Cross-table duplicate Industry row disagrees"):
        _parse(industry=industry)


def test_growth_reconciliation_disagreement_fails_clearly() -> None:
    major = MAJOR_FIXTURE.read_text(encoding="utf-8").replace(
        "<td>16.0</td>", "<td>99.0</td>", 1
    )
    with pytest.raises(DataValidationError, match="Growth reconciliation failed"):
        _parse(major=major)


def test_duplicate_canonical_key_fails() -> None:
    observations = _parse().observations
    duplicated = pd.concat([observations, observations.iloc[[0]]], ignore_index=True)
    with pytest.raises(DataValidationError, match="Duplicate canonical observation keys"):
        _validate_canonical_keys(duplicated)
