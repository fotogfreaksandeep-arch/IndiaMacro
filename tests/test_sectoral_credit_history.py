from __future__ import annotations

import hashlib
import importlib
import json
import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from indiamacro import rbi
from indiamacro.rbi import sectoral_credit_bulletin_v2 as v2
from indiamacro.rbi import sectoral_credit_transition_2026 as transition

history = importlib.import_module("indiamacro.rbi.sectoral_credit_history")


ROOT = Path(__file__).parents[1]
CENSUS_ROOT = ROOT / "spike-artifacts" / "historical-census"
CENSUS_PATH = CENSUS_ROOT / "census.json"
NON_FOOD_OUTSTANDING = "RBI.SECTION42.NON_FOOD_CREDIT.OUTSTANDING"
NON_FOOD_YOY = "RBI.SECTION42.NON_FOOD_CREDIT.YOY_GROWTH_REPORTED"
FORM_HTML = b"""<!doctype html><html><body><form method="post">
<input type="hidden" name="__VIEWSTATE" value="state">
<input type="hidden" name="__VIEWSTATEGENERATOR" value="generator">
<input type="hidden" name="__EVENTVALIDATION" value="validation">
<input type="hidden" name="hdnYear" value="">
<input type="hidden" name="hdnMonth" value="">
<input type="submit" name="UsrFontCntr$btn" value="">
<input type="submit" name="btnGo" value="Go">
</form></body></html>"""
ISSUE_HTML = f"""<!doctype html><html><body>
<a href="BS_ViewBulletin.aspx?Id=101">{history.TABLE_TITLES['major_sectors']}</a>
<a href="BS_ViewBulletin.aspx?Id=102">{history.TABLE_TITLES['industries']}</a>
</body></html>"""


def _census_issues() -> dict[str, dict]:
    if not CENSUS_PATH.exists():
        pytest.skip("ignored verified historical census evidence is unavailable")
    value = json.loads(CENSUS_PATH.read_text(encoding="utf-8"))
    return {
        item["requested_bulletin_period"]: item
        for item in value["issues"]
        if history.SUPPORTED_START
        <= item["requested_bulletin_period"]
        <= history.SUPPORTED_END
    }


def _raw(item: dict, role: str) -> tuple[bytes, str]:
    table = item[f"{role}_table"]["http"]
    return (CENSUS_ROOT / table["cache_file"]).read_bytes(), table["final_url"]


def _page(raw: bytes, url: str) -> history._FetchedPage:
    return history._FetchedPage(
        content=raw,
        requested_url=url,
        final_url=url,
        redirect_history=(),
        status=200,
        content_type="text/html; charset=utf-8",
        byte_size=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


def _seed_issue(base: Path, issue: str) -> Path:
    item = _census_issues()[issue]
    major, major_url = _raw(item, "major")
    industry, industry_url = _raw(item, "industry")
    parsed = history._parse_release(
        issue,
        major,
        industry,
        major_url=major_url,
        industry_url=industry_url,
    )
    tables = {
        "major_sectors": history._table_manifest(
            _page(major, major_url), role="major_sectors", source_url=major_url
        ),
        "industries": history._table_manifest(
            _page(industry, industry_url), role="industries", source_url=industry_url
        ),
    }
    manifest = history._manifest(
        parsed,
        tables=tables,
        retrieved_at="2026-07-15T00:00:00+00:00",
    )
    return history._commit_release_bundle(
        history._history_cache_root(base), manifest, major, industry
    )


@pytest.fixture(scope="session")
def full_cache(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("sectoral-credit-history-cache")
    for issue in history._issue_range(history.SUPPORTED_START, history.SUPPORTED_END):
        _seed_issue(base, issue)
    return base


@pytest.fixture(scope="session")
def full_result(full_cache: Path) -> history.SectoralCreditHistoryResult:
    return history.sectoral_credit_history(offline=True, cache_dir=full_cache)


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = history.ARCHIVE_URL,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        content_length: int | None = None,
        history_items: list[FakeResponse] | None = None,
    ) -> None:
        self.body = body
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)
        self.history = history_items or []
        self.closed = False

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("2025-07", "2025-07", ("2025-07",)),
        ("2025-12", "2026-02", ("2025-12", "2026-01", "2026-02")),
        ("2025-07", "2026-06", tuple(f"2025-{month:02d}" for month in range(7, 13)) + tuple(f"2026-{month:02d}" for month in range(1, 7))),
    ],
)
def test_issue_ranges_are_inclusive(start: str, end: str, expected: tuple[str, ...]) -> None:
    assert history._issue_range(start, end) == expected


@pytest.mark.parametrize("value", ["2025-7", "July 2025", "2025-00", "25-07", None])
def test_malformed_issue_is_rejected(value) -> None:
    with pytest.raises(history.UnsupportedIssueRangeError):
        history._issue_range(value, "2026-06")


@pytest.mark.parametrize(
    ("start", "end"),
    [("2025-06", "2025-07"), ("2026-06", "2026-07"), ("2026-01", "2025-12")],
)
def test_unsupported_or_reversed_ranges_are_rejected(start: str, end: str) -> None:
    with pytest.raises(history.UnsupportedIssueRangeError):
        history._issue_range(start, end)


def test_public_export_is_the_function() -> None:
    assert rbi.sectoral_credit_history is history.sectoral_credit_history


def test_archive_form_preserves_required_state() -> None:
    payload = history._form_payload(FORM_HTML.decode(), "2026-02")
    assert payload["__VIEWSTATE"] == "state"
    assert payload["__EVENTVALIDATION"] == "validation"
    assert payload["hdnYear"] == "2026"
    assert payload["hdnMonth"] == "2"
    assert payload["UsrFontCntr$btn"] == ""
    assert "btnGo" not in payload


def test_archive_form_rejects_missing_or_ambiguous_state() -> None:
    missing = FORM_HTML.replace(b"__EVENTVALIDATION", b"OTHER")
    with pytest.raises(rbi.SourceDiscoveryError, match="missing"):
        history._form_payload(missing.decode(), "2025-07")
    duplicate = FORM_HTML.replace(b"</body>", FORM_HTML + b"</body>")
    with pytest.raises(rbi.SourceDiscoveryError, match="one archive POST form"):
        history._form_payload(duplicate.decode(), "2025-07")


def test_exact_title_discovery_and_route_validation() -> None:
    tables = history._discover_tables(ISSUE_HTML, history.ARCHIVE_URL)
    assert tables["major_sectors"].endswith("?Id=101")
    assert tables["industries"].endswith("?Id=102")
    assert all(history._approved_url(value) for value in tables.values())


def test_discovery_rejects_missing_ambiguous_and_external_tables() -> None:
    with pytest.raises(rbi.SourceDiscoveryError, match="found 0"):
        history._discover_tables("<html></html>", history.ARCHIVE_URL)
    duplicate = ISSUE_HTML.replace("</body>", f'<a href="BS_ViewBulletin.aspx?Id=9">{history.TABLE_TITLES["major_sectors"]}</a></body>')
    with pytest.raises(rbi.SourceDiscoveryError, match="found 2"):
        history._discover_tables(duplicate, history.ARCHIVE_URL)
    external = ISSUE_HTML.replace(
        "BS_ViewBulletin.aspx?Id=101", "https://example.com/BS_ViewBulletin.aspx?Id=101"
    )
    with pytest.raises(rbi.SourceValidationError, match="outside approved"):
        history._discover_tables(external, history.ARCHIVE_URL)


def test_production_source_contains_no_hardcoded_table_id() -> None:
    source = (ROOT / "src/indiamacro/rbi/sectoral_credit_history.py").read_text()
    assert "BS_ViewBulletin.aspx?Id=" not in source


def test_fetch_streams_bounded_html() -> None:
    response = FakeResponse(b"<!doctype html><html>ok</html>")
    session = FakeSession(response)
    fetched = history._fetch(session, history.ARCHIVE_URL, context="test")
    assert fetched.byte_size == len(response.body)
    assert fetched.sha256 == hashlib.sha256(response.body).hexdigest()
    assert session.calls[0][2]["stream"] is True
    assert response.closed


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse(b"<html></html>", status=403), rbi.SourceAccessBlockedError),
        (FakeResponse(b"<html></html>", status=500), rbi.SourceUnavailableError),
        (FakeResponse(b"not html", content_type="application/json"), rbi.SourceValidationError),
        (FakeResponse(b"<html>CAPTCHA</html>"), rbi.SourceAccessBlockedError),
        (FakeResponse(b"<html></html>", content_length=history.MAX_RESPONSE_BYTES + 1), rbi.SourceValidationError),
        (FakeResponse(b"<html></html>", content_length=999), rbi.SourceValidationError),
    ],
)
def test_fetch_rejects_bad_responses(response: FakeResponse, error: type[Exception]) -> None:
    with pytest.raises(error):
        history._fetch(FakeSession(response), history.ARCHIVE_URL, context="test")
    assert response.closed


def test_fetch_rejects_cross_host_redirect() -> None:
    hop = FakeResponse(b"", url="https://www.rbi.org.in/old", status=302)
    response = FakeResponse(b"<html></html>", history_items=[hop])
    with pytest.raises(rbi.SourceValidationError, match="across hosts"):
        history._fetch(FakeSession(response), history.ARCHIVE_URL, context="test")


@pytest.mark.parametrize(
    ("issue", "layout"),
    [
        ("2025-07", v2.LAYOUT_ID),
        ("2026-01", transition.LAYOUT_JANUARY),
        ("2026-02", transition.LAYOUT_FEBRUARY_APRIL),
        ("2026-05", transition.LAYOUT_MAY),
        ("2026-06", "RBI_BULLETIN_SECTORAL_CREDIT_V1"),
    ],
)
def test_dispatches_all_five_positive_contracts(issue: str, layout: str) -> None:
    item = _census_issues()[issue]
    major, major_url = _raw(item, "major")
    industry, industry_url = _raw(item, "industry")
    parsed = history._parse_release(
        issue, major, industry, major_url=major_url, industry_url=industry_url
    )
    assert parsed.layout_id == layout


def test_issue_month_cannot_override_positive_content_detection() -> None:
    item = _census_issues()["2025-07"]
    major, major_url = _raw(item, "major")
    industry, industry_url = _raw(item, "industry")
    with pytest.raises(rbi.SourceValidationError, match="returned July 2025"):
        history._parse_release(
            "2025-08", major, industry, major_url=major_url, industry_url=industry_url
        )


def test_complete_offline_model_counts_and_hash(full_result) -> None:
    assert isinstance(full_result.vintages, tuple)
    assert isinstance(full_result.current_observations, tuple)
    assert len(full_result.vintages) == 5950
    assert len(full_result.current_observations) == 3060
    assert len(full_result.available_series) == 255
    assert full_result.metadata.release_count == 12
    assert full_result.metadata.semantic_vintage_sha256 == history.EXPECTED_FULL_SEMANTIC_HASH
    assert full_result.metadata.request_count == 0
    assert all(item.from_cache for item in full_result.metadata.source_manifests)
    keys = [tuple(getattr(item, field) for field in history.HISTORY_VINTAGE_KEY) for item in full_result.vintages]
    assert len(keys) == len(set(keys))


def test_current_series_selection_is_chart_ready_and_chronological(full_result) -> None:
    outstanding = full_result.select(series_id=NON_FOOD_OUTSTANDING, view="current")
    yoy = full_result.select(series_id=NON_FOOD_YOY, view="current")
    assert len(outstanding) == len(yoy) == 12
    assert [item.observation_date for item in outstanding] == sorted(
        item.observation_date for item in outstanding
    )
    assert all(isinstance(item.value, Decimal) for item in outstanding)
    assert all(item.measure == "OUTSTANDING" for item in outstanding)
    assert all(item.measure == "YOY_GROWTH_REPORTED" for item in yoy)
    assert outstanding[6].bulletin_period == "January 2026"
    assert outstanding[7].bulletin_period == "February 2026"
    assert outstanding[6].observation_date == "2025-11-28"
    assert outstanding[7].observation_date == "2025-12-31"


def test_selection_bounds_views_and_errors(full_result) -> None:
    selected = full_result.select(
        series_id=NON_FOOD_OUTSTANDING,
        view="current",
        start="2025-09-01",
        end="2026-01-31",
    )
    assert selected
    assert all("2025-09-01" <= item.observation_date <= "2026-01-31" for item in selected)
    assert full_result.select(series_id=NON_FOOD_OUTSTANDING, view="vintages")
    assert full_result.select(series_id=NON_FOOD_OUTSTANDING, view="latest_publication")
    with pytest.raises(history.UnknownSeriesError):
        full_result.select(series_id="NOT.A.SERIES")
    with pytest.raises(ValueError, match="Unsupported history view"):
        full_result.select(series_id=NON_FOOD_OUTSTANDING, view="magic")
    with pytest.raises(ValueError, match="must not be after"):
        full_result.select(series_id=NON_FOOD_OUTSTANDING, start="2026-01-02", end="2026-01-01")


def test_explicit_resolution_and_as_of(full_result) -> None:
    latest = full_result.resolve()
    cutoff = full_result.metadata.source_manifests[5].publication_date
    earlier = full_result.resolve(as_of=cutoff)
    assert latest.policy == earlier.policy == "latest_publication"
    assert latest.as_of == full_result.metadata.source_manifests[-1].publication_date
    assert all(item.publication_date <= cutoff for item in earlier.observations)
    assert latest.semantic_sha256 == full_result.resolve().semantic_sha256
    assert latest.provenance_bound_sha256 == full_result.resolve().provenance_bound_sha256
    with pytest.raises(ValueError, match="Unsupported resolution policy"):
        full_result.resolve(policy="first")
    with pytest.raises(ValueError, match="No retrieved publication"):
        full_result.resolve(as_of="2000-01-01")


def test_same_publication_resolution_conflict_is_explicit(full_result) -> None:
    mutated = list(full_result.vintages)
    target = next(
        item
        for item in mutated
        if item.publication_date == full_result.metadata.source_manifests[-1].publication_date
    )
    mutated.append(
        replace(
            target,
            column_role="SYNTHETIC_SAME_PUBLICATION_ROLE",
            value=(target.value or Decimal(0)) + Decimal(1),
        )
    )
    conflicting = replace(full_result, vintages=tuple(mutated))
    with pytest.raises(history.ResolutionConflictError):
        conflicting.resolve()


def test_methodology_boundary_is_preserved(full_result) -> None:
    assert full_result.metadata.methodology_boundaries == (
        history.MethodologyBoundary(
            "2026-01",
            "2026-02",
            transition.COMPARABLE_WITH_DATE_BASIS_CHANGE,
            "Current observations switch from last reporting Friday to calendar month-end; "
            "the prior-year YoY base retains the old reporting-fortnight definition.",
        ),
    )


def test_offline_reports_all_missing_issues_without_network(tmp_path, monkeypatch) -> None:
    def forbidden():
        raise AssertionError("offline mode created a network session")

    monkeypatch.setattr(history, "_new_session", forbidden)
    with pytest.raises(history.HistoryCacheNotFoundError) as excinfo:
        history.sectoral_credit_history("2025-07", "2025-09", offline=True, cache_dir=tmp_path)
    assert str(excinfo.value).endswith("2025-07, 2025-08, 2025-09")


def test_offline_cache_replay_is_hash_identical(full_cache, full_result, monkeypatch) -> None:
    monkeypatch.setattr(history, "_new_session", lambda: pytest.fail("network session created"))
    replay = history.sectoral_credit_history(offline=True, cache_dir=full_cache)
    assert replay.metadata.semantic_vintage_sha256 == full_result.metadata.semantic_vintage_sha256
    assert replay.metadata.provenance_bound_vintage_sha256 == full_result.metadata.provenance_bound_vintage_sha256
    assert replay.metadata.semantic_current_sha256 == full_result.metadata.semantic_current_sha256
    assert replay.metadata.provenance_bound_current_sha256 == full_result.metadata.provenance_bound_current_sha256
    assert replay.vintages == full_result.vintages


def _copy_one_issue(full_cache: Path, tmp_path: Path, issue: str = "2025-07") -> Path:
    source = history._history_cache_root(full_cache) / issue
    target = history._history_cache_root(tmp_path) / issue
    target.parent.mkdir(parents=True)
    shutil.copytree(source, target)
    return next(target.iterdir())


def test_corrupt_raw_page_is_rejected(full_cache, tmp_path) -> None:
    bundle = _copy_one_issue(full_cache, tmp_path)
    (bundle / "major_sectors.html").write_bytes(b"<html>corrupt</html>")
    with pytest.raises(rbi.CacheIntegrityError, match="size or hash"):
        history.sectoral_credit_history("2025-07", "2025-07", offline=True, cache_dir=tmp_path)


def test_corrupt_and_incompatible_manifest_are_distinct(full_cache, tmp_path) -> None:
    bundle = _copy_one_issue(full_cache, tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest_path.write_text("not json", encoding="utf-8")
    with pytest.raises(rbi.CacheIntegrityError):
        history.sectoral_credit_history("2025-07", "2025-07", offline=True, cache_dir=tmp_path)

    shutil.rmtree(history._history_cache_root(tmp_path))
    bundle = _copy_one_issue(full_cache, tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["manifest_schema_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(rbi.CacheIncompatibleError):
        history.sectoral_credit_history("2025-07", "2025-07", offline=True, cache_dir=tmp_path)


def test_atomic_commit_is_idempotent_and_leaves_no_temporary_directory(tmp_path) -> None:
    first = _seed_issue(tmp_path, "2026-01")
    second = _seed_issue(tmp_path, "2026-01")
    assert first == second
    assert sorted(path.name for path in first.parent.iterdir()) == [first.name]


def test_refresh_failure_keeps_old_bundle_but_never_returns_it(full_cache, tmp_path, monkeypatch) -> None:
    _copy_one_issue(full_cache, tmp_path)

    class SessionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(history, "_new_session", SessionContext)
    monkeypatch.setattr(
        history,
        "_fetch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(rbi.SourceUnavailableError("failed")),
    )
    with pytest.raises(rbi.SourceUnavailableError):
        history.sectoral_credit_history(
            "2025-07", "2025-07", refresh=True, cache_dir=tmp_path
        )
    assert history._bundle_candidates(history._history_cache_root(tmp_path), "2025-07")


def test_semantic_hash_ignores_page_chrome_but_provenance_hash_changes() -> None:
    item = _census_issues()["2025-07"]
    major, major_url = _raw(item, "major")
    industry, industry_url = _raw(item, "industry")
    original = history._parse_release(
        "2025-07", major, industry, major_url=major_url, industry_url=industry_url
    )
    mutated = major.replace(b"<head>", b"<head><!-- synthetic chrome mutation -->", 1)
    if mutated == major:
        mutated = major.replace(b"<body", b"<!-- synthetic chrome mutation --><body", 1)
    changed = history._parse_release(
        "2025-07", mutated, industry, major_url=major_url, industry_url=industry_url
    )
    assert changed.semantic_hash == original.semantic_hash
    assert changed.provenance_hash != original.provenance_hash


def test_cache_root_precedence(tmp_path, monkeypatch) -> None:
    configured = tmp_path / "configured"
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("INDIAMACRO_CACHE_DIR", str(configured))
    assert history._cache_root(explicit) == explicit
    assert history._cache_root(None) == configured


def test_refresh_offline_combination_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        history.sectoral_credit_history(refresh=True, offline=True, cache_dir=tmp_path)
