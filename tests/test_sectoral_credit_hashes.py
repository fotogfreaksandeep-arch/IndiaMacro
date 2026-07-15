from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from indiamacro.rbi.sectoral_credit_bulletin import (
    SEMANTIC_OBSERVATION_COLUMNS,
    parse_sectoral_credit_bulletin,
    provenance_bound_output_sha256,
    semantic_observations_sha256,
)


FIXTURES = Path(__file__).parent / "fixtures"
MAJOR = (FIXTURES / "rbi_sectoral_credit_major_v1.html").read_bytes()
INDUSTRIES = (FIXTURES / "rbi_sectoral_credit_industries_v1.html").read_bytes()


def _parse(major: bytes = MAJOR, industries: bytes = INDUSTRIES):
    return parse_sectoral_credit_bulletin(
        major,
        industries,
        major_sectors_url="https://rbi.org.in/table-15",
        industries_url="https://rbi.org.in/table-16",
    )


def test_semantic_hash_uses_the_documented_economic_columns() -> None:
    assert SEMANTIC_OBSERVATION_COLUMNS == (
        "dataset_id",
        "series_id",
        "source_table",
        "source_row_code",
        "source_label",
        "parent_row_code",
        "sector_level_1",
        "sector_level_2",
        "sector_level_3",
        "measure",
        "observation_date",
        "comparison_date",
        "value",
        "unit",
        "population_id",
        "publication_date",
        "bulletin_period",
        "is_provisional",
        "is_memorandum",
        "footnote_references",
    )


def test_identical_semantic_observations_have_identical_hashes() -> None:
    observations = _parse().observations
    assert semantic_observations_sha256(observations) == semantic_observations_sha256(
        observations.copy(deep=True)
    )


def test_row_order_does_not_change_semantic_hash() -> None:
    observations = _parse().observations
    reordered = observations.sample(frac=1, random_state=17)
    assert semantic_observations_sha256(reordered) == semantic_observations_sha256(
        observations
    )


def test_dataframe_index_does_not_change_semantic_hash() -> None:
    observations = _parse().observations
    changed_index = observations.copy()
    changed_index.index = range(10_000, 10_000 + len(changed_index))
    assert semantic_observations_sha256(changed_index) == semantic_observations_sha256(
        observations
    )


@pytest.mark.parametrize("column", ["source_url", "source_sha256", "parser_version"])
def test_transport_or_implementation_provenance_does_not_change_semantic_hash(
    column: str,
) -> None:
    observations = _parse().observations
    changed = observations.copy()
    changed.loc[changed.index[0], column] = f"changed-{column}"
    assert semantic_observations_sha256(changed) == semantic_observations_sha256(
        observations
    )


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("observation_date", "2099-01-01"),
        ("unit", "OTHER_UNIT"),
        ("measure", "OTHER_MEASURE"),
        ("population_id", "OTHER_POPULATION"),
        ("series_id", "RBI.OTHER.SERIES"),
        ("is_memorandum", True),
    ],
)
def test_economic_classification_changes_semantic_hash(column: str, replacement) -> None:
    observations = _parse().observations
    changed = observations.copy()
    changed.loc[changed.index[0], column] = replacement
    assert semantic_observations_sha256(changed) != semantic_observations_sha256(
        observations
    )


def test_economic_value_change_changes_semantic_hash() -> None:
    observations = _parse().observations
    changed = observations.copy()
    changed.loc[changed.index[0], "value"] += Decimal("1")
    assert semantic_observations_sha256(changed) != semantic_observations_sha256(
        observations
    )


def test_page_chrome_mutation_separates_raw_semantic_and_provenance_identity() -> None:
    chrome_mutation = MAJOR.replace(b"<body>", b"<body><!-- changed page chrome -->", 1)
    assert hashlib.sha256(chrome_mutation).hexdigest() != hashlib.sha256(MAJOR).hexdigest()

    original = _parse()
    changed = _parse(major=chrome_mutation)

    assert (
        original.metadata.semantic_observations_sha256
        == changed.metadata.semantic_observations_sha256
    )
    assert (
        original.metadata.provenance_bound_output_sha256
        != changed.metadata.provenance_bound_output_sha256
    )


def test_identical_complete_observations_have_identical_provenance_hashes() -> None:
    observations = _parse().observations
    assert provenance_bound_output_sha256(observations) == provenance_bound_output_sha256(
        observations.copy(deep=True)
    )


@pytest.mark.parametrize("column", ["source_url", "source_sha256"])
def test_source_provenance_change_changes_provenance_bound_hash(column: str) -> None:
    observations = _parse().observations
    changed = observations.copy()
    changed.loc[changed.index[0], column] = f"changed-{column}"
    assert provenance_bound_output_sha256(changed) != provenance_bound_output_sha256(
        observations
    )
