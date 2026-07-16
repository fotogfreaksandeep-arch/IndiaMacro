from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from html import escape
from pathlib import Path

import pandas as pd
import pytest

from indiamacro.rbi import sectoral_credit_bulletin as v1
from indiamacro.rbi import sectoral_credit_bulletin_v2 as v2


ROOT = Path(__file__).resolve().parents[1]
MAJOR_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=23534"
INDUSTRY_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=23535"


def _cell(value: str, *, rowspan: int = 1, colspan: int = 1) -> str:
    attrs = ""
    if rowspan != 1:
        attrs += f' rowspan="{rowspan}"'
    if colspan != 1:
        attrs += f' colspan="{colspan}"'
    return f"<td{attrs}>{escape(value)}</td>"


def _row(cells: list[str]) -> str:
    return f"<tr>{''.join(cells)}</tr>"


def _synthetic_table(
    role: str,
    *,
    publication: str = "Jul 23, 2025",
    ordinal: str | None = None,
    chrome: str = "compact fixture",
    growth_header: str = "Growth(%)",
    current_day: str = "May 30",
    prior_day: str = "May 31",
    prior_period_day: str = "Apr. 18",
    reporting_phrase: str = "last reporting Friday of the month",
    mutate_label: tuple[int, str] | None = None,
    mutate_values: tuple[int, list[str]] | None = None,
) -> str:
    mappings = (
        v2.V2_MAJOR_ROW_MAPPINGS if role == "major" else v2.V2_INDUSTRY_ROW_MAPPINGS
    )
    ordinal = ordinal or ("15" if role == "major" else "16")
    semantic = v2.SEMANTIC_TITLE_BY_ROLE[role]
    rows = [_row([_cell("(₹ Crore)", colspan=7)])]
    rows.append(
        _row(
            [
                _cell("Sector" if role == "major" else "Industry", rowspan=4),
                _cell("Outstanding as on", colspan=4),
                _cell(growth_header, colspan=2),
            ]
        )
    )
    rows.append(
        _row(
            [
                _cell("Mar. 21, 2025", rowspan=2),
                _cell("2024"),
                _cell("2025", colspan=2),
                _cell("Financial year so far"),
                _cell("Y-o-Y"),
            ]
        )
    )
    rows.append(
        _row(
            [
                _cell(prior_day),
                _cell(prior_period_day),
                _cell(current_day),
                _cell("2025-26"),
                _cell("2025"),
            ]
        )
    )
    rows.append(_row([_cell(value) for value in ("1", "2", "3", "4", "%", "%")]))
    for index, mapping in enumerate(mappings):
        label = mutate_label[1] if mutate_label and mutate_label[0] == index else mapping.source_label
        values = ["100", "100", "100", "110", "10.0", "10.0"]
        if not mapping.emits_observations:
            values = [""] * 6
        if mutate_values and mutate_values[0] == index:
            values = mutate_values[1]
        rows.append(_row([_cell(label), *[_cell(value) for value in values]]))
    if role == "major":
        note = (
            "Notes: (1) Data are provisional. Bank credit, Food credit and Non-food credit "
            "data are based on Section-42 return, which covers all scheduled commercial banks "
            "(SCBs), while sectoral non-food credit data are based on sector-wise and "
            "industry-wise bank credit (SIBC) return, which covers select banks accounting for "
            f"about 95 per cent of total non-food credit, pertaining to the {reporting_phrase}. "
            "(2) Data since July 28, 2023 include the impact of the merger of a non-bank with a bank."
        )
    else:
        note = "Note: Data since July 28, 2023 include the impact of the merger of a non-bank with a bank."
    rows.append(_row([_cell(note, colspan=7)]))
    return (
        "<html><body><table>"
        + _row([_cell(chrome)])
        + _row([_cell(f"{ordinal}. {semantic}")])
        + _row([_cell(f"Date : {publication}")])
        + _row([f"<td><table>{''.join(rows)}</table></td>"])
        + "</table></body></html>"
    )


def _pair(**kwargs: object) -> tuple[str, str]:
    return _synthetic_table("major", **kwargs), _synthetic_table("industry", **kwargs)


def _parse(major: str, industry: str) -> v2.ParsedSectoralCreditRelease:
    return v2.parse_sectoral_credit_bulletin_v2_release(
        major,
        industry,
        major_sectors_url=MAJOR_URL,
        industries_url=INDUSTRY_URL,
    )


def _contract_pair(major: str, industry: str) -> tuple[object, object]:
    return (
        v2._extract_contract_table(major, expected_role="major", source_url=MAJOR_URL, source_sha256="a"),
        v2._extract_contract_table(industry, expected_role="industry", source_url=INDUSTRY_URL, source_sha256="b"),
    )


def test_positive_v2_layout_detection_and_release_parse() -> None:
    major, industry = _pair()
    layout, signatures = v2.detect_sectoral_credit_layout(major, industry)
    assert layout == v2.LAYOUT_ID
    assert signatures == v2.EXPECTED_V2_SIGNATURES
    parsed = _parse(major, industry)
    assert len(parsed.observations) == 510
    assert parsed.metadata.mapped_row_counts == (("major", 44), ("industry", 43))


def test_positive_v1_layout_detection_and_unchanged_output() -> None:
    major = (ROOT / "tests/fixtures/rbi_sectoral_credit_major_v1.html").read_bytes()
    industry = (ROOT / "tests/fixtures/rbi_sectoral_credit_industries_v1.html").read_bytes()
    assert v2.detect_sectoral_credit_layout(major, industry)[0] == v1.LAYOUT_ID
    direct = v1.parse_sectoral_credit_bulletin(
        major,
        industry,
        major_sectors_url=MAJOR_URL,
        industries_url=INDUSTRY_URL,
    )
    dispatched = v2.parse_sectoral_credit_bulletin_detected(
        major,
        industry,
        major_sectors_url=MAJOR_URL,
        industries_url=INDUSTRY_URL,
    )
    assert len(dispatched.observations) == 425
    assert dispatched.metadata.semantic_observations_sha256 == direct.metadata.semantic_observations_sha256


def test_zero_and_ambiguous_layout_rejection() -> None:
    major, industry = _pair(growth_header="Change")
    with pytest.raises(v2.LayoutDetectionError):
        v2.detect_sectoral_credit_layout(major, industry)
    observed = v2.EXPECTED_V2_SIGNATURES
    with pytest.raises(v2.AmbiguousLayoutDetectionError):
        v2._detect_from_signatures(observed, (("a", observed), ("b", observed)))


def test_release_dates_ordinals_and_chrome_are_not_structural() -> None:
    first = _contract_pair(*_pair())
    second = _contract_pair(
        _synthetic_table(
            "major",
            publication="Aug 28, 2025",
            ordinal="99",
            chrome="new",
            current_day="Jun. 27",
            prior_day="Jun. 28",
            prior_period_day="May 23",
        ),
        _synthetic_table(
            "industry",
            publication="Aug 28, 2025",
            ordinal="98",
            chrome="new",
            current_day="Jun. 27",
            prior_day="Jun. 28",
            prior_period_day="May 23",
        ),
    )
    assert v2.contract_signatures(*first).structural_signature_sha256 == v2.contract_signatures(*second).structural_signature_sha256


def test_header_taxonomy_and_methodology_mutations_change_their_signatures() -> None:
    baseline = v2.contract_signatures(*_contract_pair(*_pair()))
    changed_major = _synthetic_table("major").replace(
        "Outstanding as on</td>",
        "Outstanding as on</td>",
    ).replace('colspan="4"', 'colspan="3"', 1)
    changed_industry = _synthetic_table("industry").replace(
        'colspan="4"', 'colspan="3"', 1
    )
    header = v2.contract_signatures(
        *_contract_pair(
            changed_major,
            changed_industry,
        )
    )
    taxonomy = v2.contract_signatures(
        *_contract_pair(
            _synthetic_table("major", mutate_label=(1, "II. Food Lending")),
            _synthetic_table("industry"),
        )
    )
    method = v2.contract_signatures(
        *_contract_pair(
            _synthetic_table("major", reporting_phrase="last day of the month").replace(
                "covers select banks accounting for about 95 per cent",
                "covers participating banks accounting for about 95 per cent",
            ),
            _synthetic_table("industry"),
        )
    )
    assert header.structural_signature_sha256 != baseline.structural_signature_sha256
    assert taxonomy.taxonomy_signature_sha256 != baseline.taxonomy_signature_sha256
    assert method.methodology_signature_sha256 != baseline.methodology_signature_sha256


def test_complete_mapping_and_continuity_identity_rules() -> None:
    assert len(v2.V2_ROW_MAPPINGS) == 87
    assert all(item.continuity_classification == v2.EXACT_CONTINUITY for item in v2.V2_ROW_MAPPINGS)
    assert all(item.base_series_id == item.corresponding_v1_base_series_id for item in v2.V2_ROW_MAPPINGS)
    uncertain = replace(
        v2.V2_ROW_MAPPINGS[0],
        continuity_classification=v2.LIKELY_CONTINUITY_REQUIRES_REVIEW,
    )
    with pytest.raises(v2.V2ContractError, match="must not reuse"):
        v2.validate_continuity_mappings((uncertain,))


@pytest.mark.parametrize("kind", ["unknown", "missing"])
def test_unknown_and_missing_rows_are_hard_failures(kind: str) -> None:
    if kind == "unknown":
        major = _synthetic_table("major", mutate_label=(0, "I. Unknown credit"))
    else:
        major = _synthetic_table("major").replace(
            _row(
                [
                    _cell(v2.V2_MAJOR_ROW_MAPPINGS[0].source_label),
                    *[
                        _cell(value)
                        for value in ["100", "100", "100", "110", "10.0", "10.0"]
                    ],
                ]
            ),
            "",
            1,
        )
    with pytest.raises((v2.LayoutDetectionError, v1.UnmappedSeriesError)):
        _parse(major, _synthetic_table("industry"))


def test_release_vintage_keys_roles_and_growth() -> None:
    parsed = _parse(*_pair())
    observations = parsed.observations
    assert not observations.duplicated(list(v2.VINTAGE_KEY_COLUMNS)).any()
    assert observations["column_role"].value_counts().to_dict() == {
        role: 85 for role in v2.COLUMN_ROLES
    }
    assert parsed.metadata.growth_reconciliation_checks == 170
    assert parsed.metadata.growth_reconciliation_skipped == 0
    assert parsed.metadata.growth_reconciliation_failures == 0


def test_identical_dates_in_different_publications_remain_vintages() -> None:
    july = _parse(*_pair()).observations
    august = _parse(*_pair(publication="Aug 28, 2025")).observations
    combined = pd.concat([july, august], ignore_index=True)
    assert len(combined) == 1020
    assert not combined.duplicated(list(v2.VINTAGE_KEY_COLUMNS)).any()
    assert combined.duplicated(["dataset_id", "series_id", "observation_date", "measure", "column_role"], keep=False).any()


def test_cross_table_duplicate_disagreement_and_growth_failure() -> None:
    with pytest.raises(v1.DataValidationError, match="Cross-table duplicate"):
        _parse(
            _synthetic_table("major"),
            _synthetic_table("industry", mutate_values=(0, ["101", "100", "100", "110", "8.9", "10.0"])),
        )
    with pytest.raises(v1.DataValidationError, match="Growth reconciliation"):
        _parse(
            _synthetic_table("major", mutate_values=(0, ["100", "100", "100", "110", "9.0", "10.0"])),
            _synthetic_table("industry"),
        )


def test_repeated_date_column_equality_and_collapse() -> None:
    parsed = _parse(*_pair(prior_period_day="Mar. 21"))
    assert len(parsed.observations) == 425
    assert parsed.metadata.duplicate_source_column_count == 85
    assert v2.PRIOR_PERIOD_REFERENCE not in set(parsed.observations["column_role"])
    with pytest.raises(v1.DataValidationError, match="repeated-date source columns"):
        _parse(
            _synthetic_table(
                "major",
                prior_period_day="Mar. 21",
                mutate_values=(0, ["100", "100", "101", "110", "10.0", "10.0"]),
            ),
            _synthetic_table("industry", prior_period_day="Mar. 21"),
        )


def test_deterministic_per_release_hashes() -> None:
    first = _parse(*_pair()).metadata
    second = _parse(*_pair()).metadata
    assert first.release_semantic_sha256 == second.release_semantic_sha256
    assert first.provenance_bound_output_sha256 == second.provenance_bound_output_sha256


@pytest.mark.local_evidence
def test_offline_six_release_evidence_runner_and_deterministic_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    census_dir = ROOT / "spike-artifacts/historical-census"
    if not (census_dir / "census.json").is_file():
        pytest.skip("local ignored census evidence is not present")
    spec = importlib.util.spec_from_file_location(
        "build_v2_evidence", ROOT / "scripts/build_rbi_sectoral_credit_v2_evidence.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("pytest attempted network access")

    monkeypatch.setattr("requests.sessions.Session.request", reject_network)
    first = module.build(census_dir, tmp_path / "first")
    second = module.build(census_dir, tmp_path / "second")
    assert first["release_count"] == 6
    assert first["combined_observation_count"] == 3060
    assert first["combined_semantic_sha256"] == second["combined_semantic_sha256"]
    assert first["combined_provenance_sha256"] == second["combined_provenance_sha256"]
    written = json.loads((tmp_path / "first/parser_v2_manifest.json").read_text())
    assert written["combined_semantic_sha256"] == first["combined_semantic_sha256"]
    assert written["target_periods"] == list(module.TARGETS)
