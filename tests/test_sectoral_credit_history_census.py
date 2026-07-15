from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "census_rbi_sectoral_credit_history.py"
SPEC = importlib.util.spec_from_file_location("historical_census", SCRIPT)
census = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = census
SPEC.loader.exec_module(census)

ARCHIVE_URL = census.ARCHIVE_URL
CandidateAmbiguous = census.CandidateAmbiguous
CensusClient = census.CensusClient
RequestBudgetExceeded = census.RequestBudgetExceeded
SourceBlocked = census.SourceBlocked
SourceValidationError = census.SourceValidationError
_extract_form_payload = census._extract_form_payload
_v1_reference = census._v1_reference
build_sample_schedule = census.build_sample_schedule
boundary_expansion_periods = census.boundary_expansion_periods
classify_v1_result = census.classify_v1_result
discover_table_candidates = census.discover_table_candidates
extract_table_inventory = census.extract_table_inventory
taxonomy_delta = census.taxonomy_delta


FORM_HTML = """
<html><body>
<form method="post" action="./BS_ViewBulletin.aspx">
  <input type="hidden" name="__VIEWSTATE" value="state">
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="generator">
  <input type="hidden" name="__EVENTVALIDATION" value="validation">
  <input type="hidden" name="hdnYear" value="">
  <input type="hidden" name="hdnMonth" value="">
  <input type="submit" name="UsrFontCntr$btn" value="" id="btn">
  <input type="submit" name="btnGo" value="Go" id="btnGo">
</form>
<a onclick='GetYearMonth("2025","6")'>June</a>
</body></html>
"""


ISSUE_HTML = """
<html><body>
<table><tr><td>Date : Jun 23, 2025</td></tr>
<tr><td>Reserve Bank of India Bulletin - June 2025</td></tr></table>
<a href="BS_ViewBulletin.aspx?Id=101">
  15. Deployment of Gross Bank Credit by Major Sectors
</a>
<a href="BS_ViewBulletin.aspx?Id=102">
  16. Industry-wise Deployment of Gross Bank Credit
</a>
</body></html>
"""


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self.body = body
        self.status_code = status
        self.url = ARCHIVE_URL
        self.history: list[FakeResponse] = []
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def iter_content(self, _size: int):
        yield self.body

    def close(self) -> None:
        pass


def test_archive_form_selection_preserves_state() -> None:
    payload = _extract_form_payload(FORM_HTML, year=2025, month=6)
    assert payload["__VIEWSTATE"] == "state"
    assert payload["hdnYear"] == "2025"
    assert payload["hdnMonth"] == "6"
    assert payload["UsrFontCntr$btn"] == ""
    assert "btnGo" not in payload


def test_exact_and_variant_title_detection() -> None:
    found = discover_table_candidates(ISSUE_HTML)
    assert found["major"].number == "15"
    assert found["industry"].number == "16"

    variant = ISSUE_HTML.replace("Gross Bank Credit", "Bank Credit")
    found = discover_table_candidates(variant)
    assert found["major"].title.endswith("Bank Credit by Major Sectors")
    assert found["industry"].title.endswith("Deployment of Bank Credit")


def test_candidate_official_domain_enforcement() -> None:
    evil = ISSUE_HTML.replace(
        'href="BS_ViewBulletin.aspx?Id=101"',
        'href="https://example.com/Scripts/BS_ViewBulletin.aspx?Id=101"',
    )
    with pytest.raises(SourceValidationError, match="outside RBI"):
        discover_table_candidates(evil)


def test_ambiguous_and_missing_candidates() -> None:
    duplicate = ISSUE_HTML.replace(
        "</body>",
        '<a href="BS_ViewBulletin.aspx?Id=999">'
        "15. Deployment of Gross Bank Credit by Major Sectors</a></body>",
    )
    with pytest.raises(CandidateAmbiguous, match="Multiple major"):
        discover_table_candidates(duplicate)

    missing = discover_table_candidates("<html><body>No tables</body></html>")
    assert missing == {"major": None, "industry": None}


def test_sample_schedule_is_bounded_and_deterministic() -> None:
    first = build_sample_schedule("2026-06")
    second = build_sample_schedule("2026-06")
    periods = [item["period"] for item in first]
    assert first == second
    assert len(first) == 37
    assert len(periods) == len(set(periods))
    assert periods[0] == "2010-03"
    assert periods[-1] == "2026-06"
    assert {"2026-01", "2025-06", "2024-06", "2023-06", "2022-06", "2020-06"} <= set(periods)


def test_boundary_expansion_narrows_first_availability() -> None:
    issues = [
        {
            "requested_bulletin_period": "2012-03",
            "v1_result": "MISSING_TABLE_PAIR",
            "major_table": None,
            "industry_table": None,
        },
        {
            "requested_bulletin_period": "2013-03",
            "v1_result": "NEW_LAYOUT",
            "major_table": {},
            "industry_table": {},
        },
    ]
    assert boundary_expansion_periods(issues, {"2012-03", "2013-03"}) == [
        "2012-12",
        "2013-01",
        "2013-02",
    ]


def test_request_budget_is_enforced_without_network(tmp_path: Path) -> None:
    client = CensusClient(tmp_path, max_requests=0)
    try:
        with pytest.raises(RequestBudgetExceeded):
            client.fetch(ARCHIVE_URL, context="offline budget test")
    finally:
        client.close()


def test_semantic_signature_is_deterministic() -> None:
    path = Path("tests/fixtures/rbi_sectoral_credit_major_v1.html")
    html = path.read_text(encoding="utf-8")
    one = extract_table_inventory(
        html, published_title="15. Deployment of Gross Bank Credit by Major Sectors"
    )
    two = extract_table_inventory(
        html, published_title="15. Deployment of Gross Bank Credit by Major Sectors"
    )
    assert one["semantic_layout_signature"] == two["semantic_layout_signature"]
    assert one["structural_signature"] == two["structural_signature"]


def test_classification_distinguishes_v1_pass_and_new_layout() -> None:
    reference = _v1_reference(Path.cwd())
    assert (
        classify_v1_result(
            parser_succeeded=True,
            major_inventory=reference["major"],
            industry_inventory=reference["industry"],
            v1_major_inventory=reference["major"],
            v1_industry_inventory=reference["industry"],
        )
        == "V1_PARSE_PASS"
    )
    assert (
        classify_v1_result(
            parser_succeeded=False,
            major_inventory=reference["major"],
            industry_inventory=reference["industry"],
            v1_major_inventory=reference["major"],
            v1_industry_inventory=reference["industry"],
        )
        == "V1_SIGNATURE_MATCH_PARSE_FAIL"
    )
    changed = deepcopy(reference["major"])
    changed["structural_signature"] = "different"
    assert (
        classify_v1_result(
            parser_succeeded=False,
            major_inventory=changed,
            industry_inventory=reference["industry"],
            v1_major_inventory=reference["major"],
            v1_industry_inventory=reference["industry"],
        )
        == "NEW_LAYOUT"
    )


def test_taxonomy_delta_is_conservative() -> None:
    historical = [
        {"code": "1", "label": "1. Old name", "label_without_code": "Old name"},
        {"code": "2", "label": "2. Gone", "label_without_code": "Gone"},
    ]
    current = [
        {"code": "1", "label": "1. New name", "label_without_code": "New name"},
        {"code": "3", "label": "3. Added", "label_without_code": "Added"},
    ]
    delta = taxonomy_delta(historical, current)
    assert delta["renamed"][0]["continuity"] == "DEFINITION_CHANGED"
    assert delta["removed_from_v1"][0]["continuity"] == "DISCONTINUED_SERIES"
    assert delta["added_in_v1"][0]["continuity"] == "NEW_SERIES"


def test_challenge_page_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = CensusClient(tmp_path)
    monkeypatch.setattr(
        client.session,
        "request",
        lambda *args, **kwargs: FakeResponse(b"<html>CAPTCHA security check</html>"),
    )
    try:
        with pytest.raises(SourceBlocked):
            client.fetch(ARCHIVE_URL, context="offline challenge test")
    finally:
        client.close()


def test_cache_reuse_performs_no_live_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = CensusClient(tmp_path)
    content = b"<html><body>cached</body></html>"
    key = client._cache_key(ARCHIVE_URL, "GET", None)
    metadata = {
        "sha256": hashlib.sha256(content).hexdigest(),
        "http_status": 200,
        "final_url": ARCHIVE_URL,
        "content_type": "text/html",
        "byte_size": len(content),
    }
    (client.raw_dir / f"{key}.html").write_bytes(content)
    (client.raw_dir / f"{key}.json").write_text(json.dumps(metadata), encoding="utf-8")

    def fail_request(*args, **kwargs):
        raise AssertionError("cache hit attempted a live request")

    monkeypatch.setattr(client.session, "request", fail_request)
    try:
        returned, _ = client.fetch(ARCHIVE_URL, context="offline cache test")
        assert returned == content
        assert client.run_request_count == 0
        assert client.run_cache_hits == 1
    finally:
        client.close()
