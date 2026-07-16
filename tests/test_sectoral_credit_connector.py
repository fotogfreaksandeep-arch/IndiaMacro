from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
import requests

from indiamacro import rbi
from indiamacro.rbi import sectoral_credit as public_sectoral_credit
from indiamacro.rbi.sectoral_credit_bulletin import UnsupportedLayoutError


connector = importlib.import_module("indiamacro.rbi.sectoral_credit")
parser_module = importlib.import_module("indiamacro.rbi.sectoral_credit_bulletin")


FIXTURES = Path(__file__).parent / "fixtures"
FULL_FIXTURES = Path(__file__).parents[1] / "spike-artifacts" / "source-investigation"
MAJOR = (FIXTURES / "rbi_sectoral_credit_major_v1.html").read_bytes()
INDUSTRIES = (FIXTURES / "rbi_sectoral_credit_industries_v1.html").read_bytes()
MAJOR_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=alpha-15"
INDUSTRY_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=beta-16"


def _index_html(*, duplicate_major: bool = False, missing_industries: bool = False) -> bytes:
    major = (
        f'<a href="{MAJOR_URL}">{connector.MAJOR_TITLE}</a>'
        * (2 if duplicate_major else 1)
    )
    industries = (
        ""
        if missing_industries
        else f'<a href="{INDUSTRY_URL}">{connector.INDUSTRY_TITLE}</a>'
    )
    return f"<html><title>Reserve Bank of India</title>{major}{industries}</html>".encode()


def _dedicated_html(period: str = "May 2026", released: str = "Jun 30, 2026") -> bytes:
    return (
        "<html><title>Reserve Bank of India</title>"
        f"<a href='/Scripts/BS_PressReleaseDisplay.aspx?prid=dynamic'>"
        f"Sectoral Deployment of Bank Credit – {period}</a><span>{released}</span>"
        "</html>"
    ).encode()


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        url: str,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        history: list[FakeResponse] | None = None,
        content_length: int | None = None,
        stream_error: Exception | None = None,
    ) -> None:
        self._content = content
        self.url = url
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        if content_length is None:
            content_length = len(content)
        self.headers["Content-Length"] = str(content_length)
        self.history = history or []
        self._stream_error = stream_error
        self.closed = False

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start : start + chunk_size]
        if self._stream_error is not None:
            raise self._stream_error

    def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self, routes: dict[str, list[FakeResponse | Exception]]) -> None:
        self.routes = {url: list(values) for url, values in routes.items()}
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def get(self, url: str, **kwargs):
        assert kwargs["stream"] is True
        assert kwargs["allow_redirects"] is True
        assert kwargs["timeout"] == (10, 45)
        self.calls.append(url)
        if url not in self.routes or not self.routes[url]:
            raise AssertionError(f"Unexpected network request: {url}")
        response = self.routes[url].pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _session(
    *,
    index: bytes | None = None,
    major: bytes = MAJOR,
    industries: bytes = INDUSTRIES,
    dedicated: bytes | Exception | None = None,
    major_response: FakeResponse | None = None,
    industry_response: FakeResponse | None = None,
) -> FakeSession:
    dedicated_value = _dedicated_html() if dedicated is None else dedicated
    return FakeSession(
        {
            connector.BULLETIN_INDEX_URL: [
                FakeResponse(index or _index_html(), url=connector.BULLETIN_INDEX_URL)
            ],
            MAJOR_URL: [major_response or FakeResponse(major, url=MAJOR_URL)],
            INDUSTRY_URL: [
                industry_response or FakeResponse(industries, url=INDUSTRY_URL)
            ],
            connector.DEDICATED_INDEX_URL: [
                dedicated_value
                if isinstance(dedicated_value, Exception)
                else FakeResponse(dedicated_value, url=connector.DEDICATED_INDEX_URL)
            ],
        }
    )


def _patch_session(monkeypatch, session: FakeSession) -> None:
    monkeypatch.setattr(connector, "_new_session", lambda: session)


def _bundle_dir(cache_dir: Path) -> Path:
    return next((cache_dir / "rbi" / "sectoral_credit").glob("*/*"))


def _convert_only_bundle_to_legacy(cache_dir: Path) -> Path:
    bundle = _bundle_dir(cache_dir)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_id = "a" * 64
    legacy = bundle.with_name(old_id)
    bundle.rename(legacy)
    manifest["manifest_schema_version"] = 1
    manifest["cache_bundle_id"] = old_id
    manifest["normalized_output_sha256"] = manifest.pop(
        "provenance_bound_output_sha256"
    )
    manifest.pop("semantic_observations_sha256")
    (legacy / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return legacy


def test_public_api_symbol_is_exposed() -> None:
    assert rbi.sectoral_credit is public_sectoral_credit
    assert callable(rbi.sectoral_credit)


def test_empty_cache_mocked_live_retrieval_parses_and_commits(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)

    result = rbi.sectoral_credit(cache_dir=tmp_path)

    assert len(result.observations) == 425
    assert result.metadata.from_cache is False
    assert result.metadata.source_route == "RBI_BULLETIN_CURRENT_STATISTICS"
    assert result.metadata.bulletin_publication_date == "2026-06-22"
    assert result.metadata.latest_observation_date == "2026-04-30"
    assert len(result.notes) == 2
    bundle = _bundle_dir(tmp_path)
    assert (bundle / "major_sectors.html").read_bytes() == MAJOR
    assert (bundle / "industries.html").read_bytes() == INDUSTRIES
    assert (bundle / "manifest.json").is_file()


def test_discovery_uses_exact_titles_and_dynamic_urls() -> None:
    assert connector._discover_bulletin_tables(
        _index_html().decode(), connector.BULLETIN_INDEX_URL
    ) == {"major_sectors": MAJOR_URL, "industries": INDUSTRY_URL}
    assert "24257" not in MAJOR_URL and "24258" not in INDUSTRY_URL


def test_supported_compact_fixture_has_pinned_output_hash(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    result = rbi.sectoral_credit(cache_dir=tmp_path)
    assert (
        result.metadata.provenance_bound_output_sha256
        == "b0f5ff678a8eaa3687ba3d9031634af7a861241f474a02618390fcf5318a168c"
    )


def test_second_default_call_uses_cache_without_network(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    first = rbi.sectoral_credit(cache_dir=tmp_path)
    monkeypatch.setattr(
        connector,
        "_new_session",
        lambda: (_ for _ in ()).throw(AssertionError("network session created")),
    )

    second = rbi.sectoral_credit(cache_dir=tmp_path)

    assert second.metadata.from_cache is True
    assert (
        second.metadata.provenance_bound_output_sha256
        == first.metadata.provenance_bound_output_sha256
    )
    assert (
        second.metadata.semantic_observations_sha256
        == first.metadata.semantic_observations_sha256
    )


def test_offline_uses_verified_cache_without_network(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    live = rbi.sectoral_credit(cache_dir=tmp_path)
    monkeypatch.setattr(
        connector,
        "_new_session",
        lambda: (_ for _ in ()).throw(AssertionError("network session created")),
    )

    offline = rbi.sectoral_credit(offline=True, cache_dir=tmp_path)

    assert offline.metadata.from_cache is True
    assert (
        offline.metadata.provenance_bound_output_sha256
        == live.metadata.provenance_bound_output_sha256
    )
    assert (
        offline.metadata.semantic_observations_sha256
        == live.metadata.semantic_observations_sha256
    )


def test_offline_empty_cache_raises_without_creating_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        connector,
        "_new_session",
        lambda: (_ for _ in ()).throw(AssertionError("network session created")),
    )
    with pytest.raises(rbi.CacheNotFoundError):
        rbi.sectoral_credit(offline=True, cache_dir=tmp_path)


def test_refresh_discovers_live_despite_valid_cache(monkeypatch, tmp_path) -> None:
    first_session = _session()
    _patch_session(monkeypatch, first_session)
    rbi.sectoral_credit(cache_dir=tmp_path)
    refresh_session = _session()
    _patch_session(monkeypatch, refresh_session)

    refreshed = rbi.sectoral_credit(refresh=True, cache_dir=tmp_path)

    assert refreshed.metadata.from_cache is False
    assert refresh_session.calls[0] == connector.BULLETIN_INDEX_URL
    assert len(refresh_session.calls) == 4


def test_refresh_failure_does_not_fall_back_to_valid_cache(monkeypatch, tmp_path) -> None:
    first_session = _session()
    _patch_session(monkeypatch, first_session)
    rbi.sectoral_credit(cache_dir=tmp_path)
    failed_refresh = FakeSession(
        {connector.BULLETIN_INDEX_URL: [requests.ConnectionError("live unavailable")]}
    )
    _patch_session(monkeypatch, failed_refresh)

    with pytest.raises(rbi.SourceUnavailableError, match="live unavailable"):
        rbi.sectoral_credit(refresh=True, cache_dir=tmp_path)


def test_refresh_and_offline_are_mutually_exclusive(tmp_path) -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        rbi.sectoral_credit(refresh=True, offline=True, cache_dir=tmp_path)


def test_cached_raw_file_corruption_is_detected(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    rbi.sectoral_credit(cache_dir=tmp_path)
    bundle = _bundle_dir(tmp_path)
    (bundle / "major_sectors.html").write_bytes(b"corrupt")

    with pytest.raises(rbi.CacheIntegrityError, match="size or SHA-256"):
        rbi.sectoral_credit(offline=True, cache_dir=tmp_path)


def test_invalid_json_manifest_is_detected(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    rbi.sectoral_credit(cache_dir=tmp_path)
    (_bundle_dir(tmp_path) / "manifest.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(rbi.CacheIntegrityError, match="Cannot read cache manifest"):
        rbi.sectoral_credit(offline=True, cache_dir=tmp_path)


def test_legacy_manifest_is_incompatible_not_corrupt(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    rbi.sectoral_credit(cache_dir=tmp_path)
    _convert_only_bundle_to_legacy(tmp_path)

    with pytest.raises(rbi.CacheIncompatibleError, match="refresh online or remove"):
        rbi.sectoral_credit(offline=True, cache_dir=tmp_path)


def test_online_default_refreshes_legacy_cache_without_rewriting_it(
    monkeypatch, tmp_path
) -> None:
    first_session = _session()
    _patch_session(monkeypatch, first_session)
    rbi.sectoral_credit(cache_dir=tmp_path)
    legacy = _convert_only_bundle_to_legacy(tmp_path)
    legacy_manifest_before = (legacy / "manifest.json").read_bytes()
    second_session = _session()
    _patch_session(monkeypatch, second_session)

    result = rbi.sectoral_credit(cache_dir=tmp_path)

    assert result.metadata.from_cache is False
    assert (legacy / "manifest.json").read_bytes() == legacy_manifest_before
    bundles = list((tmp_path / "rbi" / "sectoral_credit").glob("*/*"))
    assert len(bundles) == 2
    compatible = next(path for path in bundles if path != legacy)
    compatible_manifest = json.loads(
        (compatible / "manifest.json").read_text(encoding="utf-8")
    )
    assert compatible_manifest["manifest_schema_version"] == 2
    assert "semantic_observations_sha256" in compatible_manifest
    assert "provenance_bound_output_sha256" in compatible_manifest


def test_manifest_hash_mismatch_is_detected(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    rbi.sectoral_credit(cache_dir=tmp_path)
    manifest_path = _bundle_dir(tmp_path) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tables"]["major_sectors"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(rbi.CacheIntegrityError):
        rbi.sectoral_credit(offline=True, cache_dir=tmp_path)


def test_http_200_challenge_page_is_rejected(monkeypatch, tmp_path) -> None:
    challenge = b'<html><script>window["bobcmn"]="challenge";</script></html>'
    session = _session(major_response=FakeResponse(challenge, url=MAJOR_URL))
    _patch_session(monkeypatch, session)

    with pytest.raises(rbi.SourceAccessBlockedError, match="human-verification"):
        rbi.sectoral_credit(cache_dir=tmp_path)
    assert not list((tmp_path / "rbi" / "sectoral_credit").glob("*/*"))


def test_redirect_outside_rbi_is_rejected(monkeypatch, tmp_path) -> None:
    redirected = FakeResponse(MAJOR, url="https://rbi.org.in.example.com/evil")
    session = _session(major_response=redirected)
    _patch_session(monkeypatch, session)

    with pytest.raises(rbi.SourceValidationError, match="outside approved RBI"):
        rbi.sectoral_credit(cache_dir=tmp_path)


def test_oversized_response_is_rejected_before_streaming(monkeypatch, tmp_path) -> None:
    oversized = FakeResponse(
        MAJOR,
        url=MAJOR_URL,
        content_length=connector.MAX_RESPONSE_BYTES + 1,
    )
    session = _session(major_response=oversized)
    _patch_session(monkeypatch, session)

    with pytest.raises(rbi.SourceValidationError, match="response bound"):
        rbi.sectoral_credit(cache_dir=tmp_path)


@pytest.mark.parametrize(
    "index",
    [
        _index_html(missing_industries=True),
        _index_html(duplicate_major=True),
    ],
)
def test_missing_or_duplicate_discovery_links_are_rejected(
    monkeypatch, tmp_path, index: bytes
) -> None:
    session = _session(index=index)
    _patch_session(monkeypatch, session)
    with pytest.raises(rbi.SourceDiscoveryError, match="exactly one link"):
        rbi.sectoral_credit(cache_dir=tmp_path)


def test_mismatched_table_publication_dates_are_rejected(monkeypatch, tmp_path) -> None:
    industry = INDUSTRIES.replace(b"Date : Jun 22, 2026", b"Date : Jun 23, 2026")
    session = _session(industries=industry)
    _patch_session(monkeypatch, session)
    with pytest.raises(parser_module.DataValidationError, match="different publication dates"):
        rbi.sectoral_credit(cache_dir=tmp_path)


def test_mismatched_table_bulletin_periods_are_rejected(monkeypatch, tmp_path) -> None:
    industry = INDUSTRIES.replace(b"Date : Jun 22, 2026", b"Date : Jul 22, 2026")
    session = _session(industries=industry)
    _patch_session(monkeypatch, session)
    with pytest.raises(parser_module.DataValidationError):
        rbi.sectoral_credit(cache_dir=tmp_path)


def test_parser_unsupported_layout_error_is_propagated(monkeypatch, tmp_path) -> None:
    major = MAJOR.replace(b"Financial year so far", b"Financial year")
    session = _session(major=major)
    _patch_session(monkeypatch, session)
    with pytest.raises(UnsupportedLayoutError):
        rbi.sectoral_credit(cache_dir=tmp_path)


def test_dedicated_release_freshness_gap_is_calculated(monkeypatch, tmp_path) -> None:
    session = _session(dedicated=_dedicated_html("May 2026", "Jun 30, 2026"))
    _patch_session(monkeypatch, session)
    result = rbi.sectoral_credit(cache_dir=tmp_path)

    assert result.metadata.latest_dedicated_release_title.endswith("May 2026")
    assert result.metadata.latest_dedicated_release_date == "2026-06-30"
    assert result.metadata.latest_dedicated_release_period == "2026-05"
    assert result.metadata.freshness_gap_months == 1
    assert result.metadata.freshness_status == "LAGGING_DEDICATED_RELEASE"


def test_freshness_failure_returns_unknown_without_discarding_data(monkeypatch, tmp_path) -> None:
    session = _session(dedicated=requests.ConnectionError("temporary DNS failure"))
    _patch_session(monkeypatch, session)
    result = rbi.sectoral_credit(cache_dir=tmp_path)

    assert len(result.observations) == 425
    assert result.metadata.freshness_status == "UNKNOWN"
    assert result.metadata.freshness_gap_months is None
    assert "SourceUnavailableError" in result.metadata.freshness_diagnostic


def test_connector_never_requests_release_page_or_xlsx(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)
    rbi.sectoral_credit(cache_dir=tmp_path)

    assert session.calls == [
        connector.BULLETIN_INDEX_URL,
        MAJOR_URL,
        INDUSTRY_URL,
        connector.DEDICATED_INDEX_URL,
    ]
    assert not any("rbidocs" in url or url.endswith(".xlsx") for url in session.calls)


def test_bundle_id_and_manifest_are_deterministic_for_identical_inputs(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(connector, "_utc_now", lambda: "2026-07-15T00:00:00+00:00")
    first_cache = tmp_path / "first"
    second_cache = tmp_path / "second"
    first_session = _session()
    _patch_session(monkeypatch, first_session)
    first = rbi.sectoral_credit(cache_dir=first_cache)
    second_session = _session()
    _patch_session(monkeypatch, second_session)
    second = rbi.sectoral_credit(cache_dir=second_cache)

    assert first.metadata.cache_bundle_id == second.metadata.cache_bundle_id
    first_manifest = (_bundle_dir(first_cache) / "manifest.json").read_bytes()
    second_manifest = (_bundle_dir(second_cache) / "manifest.json").read_bytes()
    assert first_manifest == second_manifest


def test_temporary_bundle_is_removed_if_atomic_commit_fails(monkeypatch, tmp_path) -> None:
    session = _session()
    _patch_session(monkeypatch, session)

    def fail_replace(source, target):
        raise OSError("simulated atomic-rename failure")

    monkeypatch.setattr(connector.os, "replace", fail_replace)
    with pytest.raises(rbi.CacheIntegrityError, match="commit cache bundle"):
        rbi.sectoral_credit(cache_dir=tmp_path)
    cache_root = tmp_path / "rbi" / "sectoral_credit"
    assert not list(cache_root.rglob(".tmp-*"))


def test_partial_download_is_rejected(monkeypatch, tmp_path) -> None:
    partial = FakeResponse(MAJOR, url=MAJOR_URL, content_length=len(MAJOR) + 10)
    session = _session(major_response=partial)
    _patch_session(monkeypatch, session)
    with pytest.raises(rbi.SourceValidationError, match="partially downloaded"):
        rbi.sectoral_credit(cache_dir=tmp_path)


def test_approved_host_check_rejects_suffix_trick() -> None:
    assert connector._is_approved_rbi_url("https://rbi.org.in/path")
    assert connector._is_approved_rbi_url("https://sub.rbi.org.in/path")
    assert not connector._is_approved_rbi_url("https://rbi.org.in.example.com/path")
    assert not connector._is_approved_rbi_url("http://rbi.org.in/path")


def test_environment_cache_directory_is_supported(monkeypatch, tmp_path) -> None:
    configured = tmp_path / "configured"
    monkeypatch.setenv("INDIAMACRO_CACHE_DIR", str(configured))
    session = _session()
    _patch_session(monkeypatch, session)
    result = rbi.sectoral_credit()
    assert result.metadata.from_cache is False
    assert _bundle_dir(configured).is_dir()


@pytest.mark.local_evidence
def test_full_preserved_fixture_connector_hash(monkeypatch, tmp_path) -> None:
    major_path = FULL_FIXTURES / "bulletin_major_sectors.html"
    industry_path = FULL_FIXTURES / "bulletin_industries.html"
    if not major_path.exists() or not industry_path.exists():
        pytest.skip("ignored full-page regression fixtures are not present")
    full_major_url = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24257"
    full_industry_url = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx?Id=24258"
    index = (
        "<html><title>Reserve Bank of India</title>"
        f'<a href="{full_major_url}">{connector.MAJOR_TITLE}</a>'
        f'<a href="{full_industry_url}">{connector.INDUSTRY_TITLE}</a></html>'
    ).encode()
    session = FakeSession(
        {
            connector.BULLETIN_INDEX_URL: [
                FakeResponse(index, url=connector.BULLETIN_INDEX_URL)
            ],
            full_major_url: [FakeResponse(major_path.read_bytes(), url=full_major_url)],
            full_industry_url: [
                FakeResponse(industry_path.read_bytes(), url=full_industry_url)
            ],
            connector.DEDICATED_INDEX_URL: [
                FakeResponse(_dedicated_html(), url=connector.DEDICATED_INDEX_URL)
            ],
        }
    )
    _patch_session(monkeypatch, session)

    result = rbi.sectoral_credit(cache_dir=tmp_path)

    assert len(result.observations) == 425
    assert (
        result.metadata.provenance_bound_output_sha256
        == "ea336649ccc0eb9e6513fd61d5b0d3f779483c9f0b3aec51d2fa4fb5868524c0"
    )
