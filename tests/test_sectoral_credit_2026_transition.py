from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from indiamacro.rbi import sectoral_credit_bulletin as v1
from indiamacro.rbi import sectoral_credit_bulletin_v2 as v2
from indiamacro.rbi import sectoral_credit_transition_2026 as transition


ROOT = Path(__file__).resolve().parents[1]
CENSUS_DIR = ROOT / "spike-artifacts/historical-census"
COMPATIBILITY_FIXTURE = ROOT / "tests/fixtures/rbi_sectoral_credit_compatibility_v1.json"
TARGETS = tuple(f"2026-{month:02d}" for month in range(1, 6))
EXPECTED = {
    "2026-01": (transition.LAYOUT_JANUARY, 510, "2025-11-28", None),
    "2026-02": (transition.LAYOUT_FEBRUARY_APRIL, 510, "2025-12-31", None),
    "2026-03": (transition.LAYOUT_FEBRUARY_APRIL, 510, "2026-01-31", None),
    "2026-04": (transition.LAYOUT_FEBRUARY_APRIL, 510, "2026-02-28", "2025-03-07"),
    "2026-05": (transition.LAYOUT_MAY, 425, "2026-03-31", "2025-04-04"),
}


def _census() -> dict[str, object]:
    if not (CENSUS_DIR / "census.json").is_file():
        pytest.skip("ignored census evidence is unavailable")
    return json.loads((CENSUS_DIR / "census.json").read_text(encoding="utf-8"))


def _pair(period: str) -> tuple[bytes, bytes, dict[str, object], dict[str, object]]:
    census = _census()
    issue = next(
        item for item in census["issues"] if item["requested_bulletin_period"] == period
    )
    major = issue["major_table"]
    industry = issue["industry_table"]
    major_raw = (CENSUS_DIR / major["http"]["cache_file"]).read_bytes()
    industry_raw = (CENSUS_DIR / industry["http"]["cache_file"]).read_bytes()
    return major_raw, industry_raw, major, industry


def _parse(period: str) -> v2.ParsedSectoralCreditRelease:
    major_raw, industry_raw, major, industry = _pair(period)
    return transition.parse_transition_release(
        major_raw,
        industry_raw,
        major_url=major["url"],
        industry_url=industry["url"],
    )


@pytest.mark.local_evidence
@pytest.mark.parametrize("period", TARGETS)
def test_cached_hashes_detection_and_complete_release_parse(period: str) -> None:
    major_raw, industry_raw, major, industry = _pair(period)
    assert hashlib.sha256(major_raw).hexdigest() == major["raw_sha256"] == major["http"]["sha256"]
    assert hashlib.sha256(industry_raw).hexdigest() == industry["raw_sha256"] == industry["http"]["sha256"]
    detected = transition.detect_supported_layout(
        major_raw,
        industry_raw,
        major_url=major["url"],
        industry_url=industry["url"],
    )
    parsed = transition.parse_transition_release(
        major_raw,
        industry_raw,
        major_url=major["url"],
        industry_url=industry["url"],
    )
    layout, count, current, override = EXPECTED[period]
    assert detected.layout_id == layout == parsed.metadata.layout_id
    assert len(parsed.observations) == count
    assert parsed.metadata.current_observation_date == current
    assert parsed.metadata.section42_prior_year_override_date == override
    assert parsed.metadata.mapped_row_counts == (("major", 44), ("industry", 43))
    assert parsed.metadata.emitted_row_counts == (("major", 43), ("industry", 42))
    assert parsed.metadata.growth_reconciliation_checks == 170
    assert parsed.metadata.growth_reconciliation_failures == 0


@pytest.mark.local_evidence
def test_all_twelve_releases_have_exact_positive_dispatch() -> None:
    periods = (
        *(f"2025-{month:02d}" for month in range(7, 13)),
        *(f"2026-{month:02d}" for month in range(1, 7)),
    )
    layouts = []
    for period in periods:
        major_raw, industry_raw, major, industry = _pair(period)
        layouts.append(
            transition.detect_supported_layout(
                major_raw,
                industry_raw,
                major_url=major["url"],
                industry_url=industry["url"],
            ).layout_id
        )
    assert layouts[:6] == [v2.LAYOUT_ID] * 6
    assert layouts[6:] == [
        transition.LAYOUT_JANUARY,
        transition.LAYOUT_FEBRUARY_APRIL,
        transition.LAYOUT_FEBRUARY_APRIL,
        transition.LAYOUT_FEBRUARY_APRIL,
        transition.LAYOUT_MAY,
        v1.LAYOUT_ID,
    ]


@pytest.mark.local_evidence
def test_zero_match_and_ambiguous_dispatch_are_hard_failures() -> None:
    major_raw, industry_raw, major, industry = _pair("2026-01")
    broken = major_raw.replace(b"Growth(%)", b"Change", 1)
    with pytest.raises(v2.LayoutDetectionError):
        transition.detect_supported_layout(
            broken,
            industry_raw,
            major_url=major["url"],
            industry_url=industry["url"],
        )
    detected = transition.DetectedLayout(
        transition.LAYOUT_JANUARY,
        v2.EXPECTED_V2_SIGNATURES,
        "January 2026",
    )
    with pytest.raises(v2.AmbiguousLayoutDetectionError):
        transition._select_unique_layout((detected, detected))


@pytest.mark.local_evidence
@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"15. Deployment of Gross Bank Credit by Major Sectors", b"15. Other Table"),
        ("(₹ Crore)".encode(), b"(INR million)"),
    ],
)
def test_exact_titles_and_units(old: bytes, new: bytes) -> None:
    major_raw, industry_raw, major, industry = _pair("2026-01")
    with pytest.raises((v2.LayoutDetectionError, transition.TransitionContractError)):
        transition.parse_transition_release(
            major_raw.replace(old, new),
            industry_raw,
            major_url=major["url"],
            industry_url=industry["url"],
        )


def test_explicit_mappings_hierarchy_memorandum_and_continuity() -> None:
    for period in transition.SUPPORTED_PERIODS:
        mappings = (
            *transition.MAPPINGS_BY_PERIOD[period]["major"],
            *transition.MAPPINGS_BY_PERIOD[period]["industry"],
        )
        assert len(mappings) == 87
        assert all(item.continuity_classification == v2.EXACT_CONTINUITY for item in mappings)
        assert all(item.base_series_id == item.corresponding_v1_base_series_id for item in mappings)
        heading = next(item for item in mappings if item.source_table == v1.MAJOR_TITLE and item.source_row_code == "5")
        assert heading.is_memorandum and not heading.emits_observations
        child = next(item for item in mappings if item.source_table == v1.INDUSTRY_TITLE and item.source_row_code == "2.18.1")
        assert child.parent_row_code == "2.18"


@pytest.mark.local_evidence
@pytest.mark.parametrize("mutation", ["unknown", "missing"])
def test_unknown_and_missing_rows_rejected(mutation: str) -> None:
    major_raw, industry_raw, major, industry = _pair("2026-01")
    label = b"I. Bank Credit (II + III)"
    replacement = b"I. Unknown Credit" if mutation == "unknown" else b""
    with pytest.raises((v2.LayoutDetectionError, v2.V2ContractError, v1.UnmappedSeriesError)):
        transition.parse_transition_release(
            major_raw.replace(label, replacement, 1),
            industry_raw,
            major_url=major["url"],
            industry_url=industry["url"],
        )


@pytest.mark.local_evidence
def test_population_notes_and_month_end_methodology_are_strict() -> None:
    major_raw, industry_raw, major, industry = _pair("2026-02")
    broken_population = major_raw.replace(
        b"covers all scheduled commercial banks (SCBs)", b"covers some banks", 1
    )
    with pytest.raises(transition.TransitionContractError, match="population"):
        transition.parse_transition_release(
            broken_population,
            industry_raw,
            major_url=major["url"],
            industry_url=industry["url"],
        )
    broken_method = major_raw.replace(b"last day of the month", b"reporting date")
    with pytest.raises(transition.TransitionContractError, match="methodology"):
        transition.parse_transition_release(
            broken_method,
            industry_raw.replace(b"last day of the month", b"reporting date"),
            major_url=major["url"],
            industry_url=industry["url"],
        )


@pytest.mark.local_evidence
def test_malformed_missing_and_growth_values() -> None:
    major_raw, industry_raw, major, industry = _pair("2026-01")
    parsed = _parse("2026-01")
    first_value = str(parsed.observations.iloc[0]["value"]).encode()
    with pytest.raises(v1.DataValidationError, match="Malformed numeric"):
        transition.parse_transition_release(
            major_raw.replace(first_value, b"bad", 1),
            industry_raw,
            major_url=major["url"],
            industry_url=industry["url"],
        )
    missing = transition.parse_transition_release(
        major_raw.replace(first_value, b"-", 1),
        industry_raw,
        major_url=major["url"],
        industry_url=industry["url"],
    )
    assert missing.observations["value"].isna().any()
    assert missing.metadata.growth_reconciliation_skipped > 0


@pytest.mark.local_evidence
def test_cross_table_duplicate_and_growth_disagreement_rejected() -> None:
    major_raw, industry_raw, major, industry = _pair("2026-01")
    parsed = _parse("2026-01")
    industry_total = parsed.observations.loc[
        (parsed.observations["source_table"] == v1.MAJOR_TITLE)
        & (parsed.observations["source_row_code"] == "2")
    ].iloc[0]["value"]
    old = str(industry_total).encode()
    with pytest.raises(v1.DataValidationError, match="Cross-table duplicate"):
        transition.parse_transition_release(
            major_raw,
            industry_raw.replace(old, b"99999999", 1),
            major_url=major["url"],
            industry_url=industry["url"],
        )
    current = str(parsed.observations.loc[
        (parsed.observations["source_row_code"] == "I")
        & (parsed.observations["column_role"] == v2.CURRENT_OBSERVATION), "value"
    ].iloc[0]).encode()
    with pytest.raises(v1.DataValidationError, match="Growth reconciliation"):
        transition.parse_transition_release(
            major_raw.replace(current, b"99999999", 1),
            industry_raw,
            major_url=major["url"],
            industry_url=industry["url"],
        )


def test_taxonomy_and_comparability_boundaries_are_separate() -> None:
    assert all(
        item.taxonomy_continuity == v2.EXACT_CONTINUITY
        for item in transition.BOUNDARY_CLASSIFICATIONS
    )
    january_february = transition.BOUNDARY_CLASSIFICATIONS[1]
    assert january_february.comparability == transition.COMPARABLE_WITH_DATE_BASIS_CHANGE
    assert "month-end" in january_february.methodology_change
    assert transition.BOUNDARY_CLASSIFICATIONS[0].comparability == transition.FULLY_COMPARABLE


@pytest.mark.local_evidence
def test_v1_conversion_vintage_keys_and_regression_hash() -> None:
    major_raw, industry_raw, major, industry = _pair("2026-06")
    parsed = v1.parse_sectoral_credit_bulletin(
        major_raw,
        industry_raw,
        major_sectors_url=major["url"],
        industries_url=industry["url"],
    )
    assert len(parsed.observations) == 425
    assert parsed.metadata.semantic_observations_sha256 == "e3efd9e834b63c3e9ca1f797de5eee86cc572c448c05fe84b3ab543c2140ce35"
    frame = transition.v1_release_observations(parsed)
    assert len(frame) == 425
    assert not frame.duplicated(list(v2.VINTAGE_KEY_COLUMNS)).any()


@pytest.mark.local_evidence
def test_transition_evidence_is_offline_deterministic_and_v2_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _census()
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted")

    monkeypatch.setattr("requests.sessions.Session.request", reject_network)
    spec = importlib.util.spec_from_file_location(
        "transition_builder",
        ROOT / "scripts/build_rbi_sectoral_credit_2026_transition_evidence.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = module.build(CENSUS_DIR, tmp_path / "first")
    second = module.build(CENSUS_DIR, tmp_path / "second")
    assert first["release_count"] == 12
    assert first["combined_observation_count"] == 5950
    assert first["semantic_vintage_sha256"] == second["semantic_vintage_sha256"]
    assert first["provenance_bound_vintage_sha256"] == second["provenance_bound_vintage_sha256"]
    first_files = {
        path.relative_to(tmp_path / "first"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (tmp_path / "first").rglob("*") if path.is_file()
    }
    second_files = {
        path.relative_to(tmp_path / "second"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (tmp_path / "second").rglob("*") if path.is_file()
    }
    assert first_files == second_files
    assert first["v2_regression_semantic_sha256"] == "c350493a3ca0e9f36ef7bd99267c9a049662de2411a9e21d35d832d2fb65d1e4"


def test_portable_compatibility_contract_and_positive_dispatch() -> None:
    contract = json.loads(COMPATIBILITY_FIXTURE.read_text(encoding="utf-8"))
    releases = contract["releases"]
    assert [item["issue"] for item in releases] == [
        *(f"2025-{month:02d}" for month in range(7, 13)),
        *(f"2026-{month:02d}" for month in range(1, 7)),
    ]
    assert [item["layout_id"] for item in releases] == [
        *([v2.LAYOUT_ID] * 6),
        transition.LAYOUT_JANUARY,
        *([transition.LAYOUT_FEBRUARY_APRIL] * 3),
        transition.LAYOUT_MAY,
        v1.LAYOUT_ID,
    ]
    assert sum(item["observation_count"] for item in releases) == 5950

    taxonomy = contract["taxonomy_signature_sha256"]
    for item in releases:
        signatures = v2.ContractSignatures(
            structural_signature_sha256=item["structural_signature_sha256"],
            taxonomy_signature_sha256=taxonomy,
            methodology_signature_sha256=item["methodology_signature_sha256"],
        )
        detected = transition.DetectedLayout(item["layout_id"], signatures, item["issue"])
        assert transition._select_unique_layout((detected,)) is detected
        assert len(item["structural_signature_sha256"]) == 64
        assert len(item["methodology_signature_sha256"]) == 64

    with pytest.raises(v2.LayoutDetectionError):
        transition._select_unique_layout(())
    first = releases[0]
    detected = transition.DetectedLayout(
        first["layout_id"],
        v2.ContractSignatures(
            structural_signature_sha256=first["structural_signature_sha256"],
            taxonomy_signature_sha256=taxonomy,
            methodology_signature_sha256=first["methodology_signature_sha256"],
        ),
        first["issue"],
    )
    with pytest.raises(v2.AmbiguousLayoutDetectionError):
        transition._select_unique_layout((detected, detected))


def test_portable_family_boundaries_roles_and_methodology() -> None:
    contract = json.loads(COMPATIBILITY_FIXTURE.read_text(encoding="utf-8"))
    releases = {item["issue"]: item for item in contract["releases"]}
    profiles = contract["role_profiles"]

    assert transition.SUPPORTED_PERIODS == (
        "January 2026",
        "February 2026",
        "March 2026",
        "April 2026",
        "May 2026",
    )
    assert [transition._layout_for_period(period) for period in transition.SUPPORTED_PERIODS] == [
        transition.LAYOUT_JANUARY,
        transition.LAYOUT_FEBRUARY_APRIL,
        transition.LAYOUT_FEBRUARY_APRIL,
        transition.LAYOUT_FEBRUARY_APRIL,
        transition.LAYOUT_MAY,
    ]
    assert [releases[period]["current_date_basis"] for period in releases][:7] == [
        "LAST_REPORTING_FRIDAY"
    ] * 7
    assert [releases[period]["current_date_basis"] for period in releases][7:] == [
        "CALENDAR_MONTH_END"
    ] * 5
    assert releases["2026-01"]["methodology_signature_sha256"] != releases["2026-02"][
        "methodology_signature_sha256"
    ]
    assert transition.BOUNDARY_CLASSIFICATIONS[1].comparability == (
        transition.COMPARABLE_WITH_DATE_BASIS_CHANGE
    )
    assert profiles[releases["2026-05"]["role_profile"]] == [
        v2.CURRENT_OBSERVATION,
        transition.FINANCIAL_YEAR_AND_PRIOR_YEAR_REFERENCE,
        v2.PRIOR_PERIOD_REFERENCE,
        v2.REPORTED_FINANCIAL_YEAR_GROWTH,
        v2.REPORTED_YOY_GROWTH,
    ]
    assert profiles[releases["2026-06"]["role_profile"]] == [
        v2.CURRENT_OBSERVATION,
        v2.FINANCIAL_YEAR_BASE,
        v2.PRIOR_YEAR_REFERENCE,
        v2.REPORTED_FINANCIAL_YEAR_GROWTH,
        v2.REPORTED_YOY_GROWTH,
    ]
