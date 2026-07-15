"""Verified RBI Bulletin history connector for July 2025 through June 2026."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final, Iterable, Sequence
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from platformdirs import user_cache_path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from indiamacro import __version__
from indiamacro.rbi import sectoral_credit_bulletin as v1
from indiamacro.rbi import sectoral_credit_bulletin_v2 as v2
from indiamacro.rbi import sectoral_credit_transition_2026 as transition
from indiamacro.rbi.sectoral_credit import (
    CacheIncompatibleError,
    CacheIntegrityError,
    SourceAccessBlockedError,
    SourceDiscoveryError,
    SourceUnavailableError,
    SourceValidationError,
)


ARCHIVE_URL: Final = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx"
SOURCE_ROUTE: Final = "RBI_BULLETIN_ARCHIVE_HISTORY"
MANIFEST_SCHEMA_VERSION: Final = 1
SUPPORTED_START: Final = "2025-07"
SUPPORTED_END: Final = "2026-06"
MAX_RESPONSE_BYTES: Final = 5 * 1024 * 1024
CHUNK_SIZE: Final = 64 * 1024
CONNECT_TIMEOUT_SECONDS: Final = 10
READ_TIMEOUT_SECONDS: Final = 45
USER_AGENT: Final = (
    f"IndiaMacro/{__version__} RBI-sectoral-credit-history (+https://rbi.org.in/)"
)
EXPECTED_FULL_SEMANTIC_HASH: Final = (
    "1c20933973f8652fffdfa4a83e9914fa929bf811c503bd209c430c43d6f6e431"
)
CURRENT_ROLES: Final = frozenset(
    {
        v2.CURRENT_OBSERVATION,
        v2.REPORTED_FINANCIAL_YEAR_GROWTH,
        v2.REPORTED_YOY_GROWTH,
    }
)
TABLE_TITLES: Final = {
    "major_sectors": v1.MAJOR_TITLE,
    "industries": v1.INDUSTRY_TITLE,
}
CHALLENGE_MARKERS: Final = (
    "captcha",
    "access denied",
    "request rejected",
    "human verification",
    "verify you are human",
    "unusual traffic",
    "security check",
    "challenge.support_id",
    'window["bobcmn"]',
    "/tspd/",
)
REQUIRED_FORM_FIELDS: Final = frozenset(
    {"__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"}
)
HISTORY_VINTAGE_KEY: Final = (
    *v2.VINTAGE_KEY_COLUMNS,
    "population_id",
    "source_table",
)
ECONOMIC_RESOLUTION_KEY: Final = (
    "dataset_id",
    "series_id",
    "observation_date",
    "measure",
    "unit",
    "population_id",
)


class SectoralCreditHistoryError(RuntimeError):
    """Base class for historical API failures."""


class UnsupportedIssueRangeError(SectoralCreditHistoryError, ValueError):
    """The requested issue range is malformed or outside verified support."""


class HistoryCacheNotFoundError(SectoralCreditHistoryError):
    """One or more requested issue bundles are absent from the history cache."""


class ResolutionConflictError(SectoralCreditHistoryError):
    """One publication contains conflicting values for one economic key."""


class UnknownSeriesError(SectoralCreditHistoryError, KeyError):
    """A selector requested a series ID outside the retrieved result."""


@dataclass(frozen=True)
class HistoryObservation:
    dataset_id: str
    series_id: str
    source_table: str
    source_row_code: str
    source_label: str
    measure: str
    observation_date: str
    comparison_date: str | None
    publication_date: str
    bulletin_period: str
    value: Decimal | None
    unit: str
    population_id: str
    column_role: str
    layout_id: str
    parser_version: str
    source_url: str
    source_sha256: str
    is_provisional: bool
    footnote_references: str


@dataclass(frozen=True)
class HistoryNote:
    issue_month: str
    source_table: str
    text: str


@dataclass(frozen=True)
class AvailableSeries:
    series_id: str
    measure: str
    unit: str
    population_id: str
    source_row_code: str
    source_label: str


@dataclass(frozen=True)
class ReleaseSourceManifest:
    issue_month: str
    publication_date: str
    bundle_id: str
    layout_id: str
    parser_version: str
    major_sectors_title: str
    major_sectors_raw_filename: str
    major_sectors_url: str
    major_sectors_final_url: str
    major_sectors_byte_size: int
    industries_title: str
    industries_raw_filename: str
    industries_url: str
    industries_final_url: str
    industries_byte_size: int
    major_sectors_sha256: str
    industries_sha256: str
    semantic_release_sha256: str
    provenance_bound_release_sha256: str
    structural_signature_sha256: str
    taxonomy_signature_sha256: str
    methodology_signature_sha256: str
    retrieved_at_utc: str
    from_cache: bool


@dataclass(frozen=True)
class MethodologyBoundary:
    from_issue: str
    to_issue: str
    classification: str
    description: str


@dataclass(frozen=True)
class SectoralCreditHistoryMetadata:
    dataset_id: str
    source_route: str
    selected_start_issue: str
    selected_end_issue: str
    supported_start_issue: str
    supported_end_issue: str
    release_count: int
    vintage_observation_count: int
    current_observation_count: int
    unique_series_count: int
    semantic_vintage_sha256: str
    provenance_bound_vintage_sha256: str
    semantic_current_sha256: str
    provenance_bound_current_sha256: str
    methodology_boundaries: tuple[MethodologyBoundary, ...]
    source_manifests: tuple[ReleaseSourceManifest, ...]
    request_count: int


@dataclass(frozen=True)
class ResolvedHistory:
    observations: tuple[HistoryObservation, ...]
    policy: str
    as_of: str
    semantic_sha256: str
    provenance_bound_sha256: str


@dataclass(frozen=True)
class SectoralCreditHistoryResult:
    vintages: tuple[HistoryObservation, ...]
    current_observations: tuple[HistoryObservation, ...]
    metadata: SectoralCreditHistoryMetadata
    notes: tuple[HistoryNote, ...]
    available_series: tuple[AvailableSeries, ...]

    def resolve(
        self, *, policy: str = "latest_publication", as_of: str | None = None
    ) -> ResolvedHistory:
        if policy != "latest_publication":
            raise ValueError(f"Unsupported resolution policy {policy!r}")
        cutoff = self.metadata.source_manifests[-1].publication_date if as_of is None else _iso_date(as_of, name="as_of")
        eligible = [item for item in self.vintages if item.publication_date <= cutoff]
        if not eligible:
            raise ValueError(f"No retrieved publication is eligible as of {cutoff}")
        by_key_publication: dict[tuple[Any, ...], dict[str, list[HistoryObservation]]] = {}
        for item in eligible:
            key = tuple(getattr(item, field) for field in ECONOMIC_RESOLUTION_KEY)
            by_key_publication.setdefault(key, {}).setdefault(item.publication_date, []).append(item)
        selected: list[HistoryObservation] = []
        for publications in by_key_publication.values():
            publication = max(publications)
            candidates = publications[publication]
            values = {(candidate.value, candidate.comparison_date) for candidate in candidates}
            if len(values) != 1:
                raise ResolutionConflictError(
                    "Conflicting same-publication roles for one economic observation key"
                )
            selected.append(
                min(candidates, key=lambda item: (item.column_role, item.source_table))
            )
        observations = tuple(sorted(selected, key=_resolved_sort_key))
        frame = _records_to_frame(observations)
        return ResolvedHistory(
            observations=observations,
            policy=policy,
            as_of=cutoff,
            semantic_sha256=v2.release_semantic_sha256(frame),
            provenance_bound_sha256=v2.release_provenance_sha256(frame),
        )

    def select(
        self,
        *,
        series_id: str,
        view: str = "current",
        start: str | None = None,
        end: str | None = None,
    ) -> tuple[HistoryObservation, ...]:
        known = {item.series_id for item in self.available_series}
        if series_id not in known:
            raise UnknownSeriesError(series_id)
        if view == "current":
            source: Iterable[HistoryObservation] = self.current_observations
        elif view == "vintages":
            source = self.vintages
        elif view == "latest_publication":
            source = self.resolve().observations
        else:
            raise ValueError(f"Unsupported history view {view!r}")
        lower = _iso_date(start, name="start") if start is not None else None
        upper = _iso_date(end, name="end") if end is not None else None
        if lower and upper and lower > upper:
            raise ValueError("start observation date must not be after end")
        selected = [
            item
            for item in source
            if item.series_id == series_id
            and (lower is None or item.observation_date >= lower)
            and (upper is None or item.observation_date <= upper)
        ]
        return tuple(sorted(selected, key=_selection_sort_key))


@dataclass(frozen=True)
class _Link:
    text: str
    href: str


@dataclass(frozen=True)
class _Form:
    method: str
    inputs: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class _FetchedPage:
    content: bytes
    requested_url: str
    final_url: str
    redirect_history: tuple[dict[str, Any], ...]
    status: int
    content_type: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class _ParsedRelease:
    observations: pd.DataFrame
    notes: tuple[v1.SourceNote, ...]
    issue_month: str
    publication_date: str
    layout_id: str
    parser_version: str
    signatures: v2.ContractSignatures
    semantic_hash: str
    provenance_hash: str
    current_observation_date: str


class _ArchiveParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[_Link] = []
        self.forms: list[_Form] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._form_method: str | None = None
        self._inputs: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        tag = tag.casefold()
        if tag == "a":
            self._href = values.get("href", "")
            self._link_text = []
        elif tag == "form":
            self._form_method = values.get("method", "get").casefold()
            self._inputs = []
        elif tag == "input" and self._form_method is not None:
            self._inputs.append(
                (values.get("name", ""), values.get("value", ""), values.get("type", "text"))
            )

    def handle_data(self, data: str) -> None:
        text = " ".join(unescape(data).split())
        if self._href is not None and text:
            self._link_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "a" and self._href is not None:
            self.links.append(_Link(" ".join(self._link_text), self._href))
            self._href = None
        elif tag == "form" and self._form_method is not None:
            self.forms.append(_Form(self._form_method, tuple(self._inputs)))
            self._form_method = None


def _normalize(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _issue(value: str, *, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", value):
        raise UnsupportedIssueRangeError(f"{name} must use YYYY-MM")
    if value < SUPPORTED_START or value > SUPPORTED_END:
        raise UnsupportedIssueRangeError(
            f"{name} {value} is outside verified support {SUPPORTED_START} through {SUPPORTED_END}"
        )
    return value


def _issue_range(start: str, end: str) -> tuple[str, ...]:
    start = _issue(start, name="start_issue")
    end = _issue(end, name="end_issue")
    if start > end:
        raise UnsupportedIssueRangeError("start_issue must not be after end_issue")
    year, month = map(int, start.split("-"))
    values = []
    while True:
        value = f"{year:04d}-{month:02d}"
        values.append(value)
        if value == end:
            return tuple(values)
        month += 1
        if month == 13:
            year += 1
            month = 1


def _iso_date(value: str, *, name: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must use YYYY-MM-DD") from exc


def _approved_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold().rstrip(".")
    return parsed.scheme.casefold() == "https" and (
        host == "rbi.org.in" or host.endswith(".rbi.org.in")
    )


def _url_host(url: str) -> str:
    return (urlparse(url).hostname or "").casefold().rstrip(".")


def _require_url(url: str) -> None:
    if not _approved_url(url):
        raise SourceValidationError(f"Refusing URL outside approved RBI HTTPS hosts: {url}")


def _decode(content: bytes, *, context: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceValidationError(f"{context} is not valid UTF-8") from exc


def _form_payload(html: str, issue_month: str) -> dict[str, str]:
    parser = _ArchiveParser()
    parser.feed(html)
    forms = [form for form in parser.forms if form.method == "post"]
    if len(forms) != 1:
        raise SourceDiscoveryError(f"Expected one archive POST form; found {len(forms)}")
    payload = {
        name: value
        for name, value, input_type in forms[0].inputs
        if name and input_type.casefold() in {"hidden", "submit"}
    }
    missing = sorted(REQUIRED_FORM_FIELDS - payload.keys())
    if missing:
        raise SourceDiscoveryError(f"Archive form is missing required hidden fields: {missing}")
    year, month = issue_month.split("-")
    payload.update(
        {
            "hdnYear": year,
            "hdnMonth": str(int(month)),
            "ddlSubSection": "0",
            "UsrFontCntr$btn": "",
        }
    )
    payload.pop("btnGo", None)
    return payload


def _discover_tables(html: str, base_url: str) -> dict[str, str]:
    parser = _ArchiveParser()
    parser.feed(html)
    discovered = {}
    for role, title in TABLE_TITLES.items():
        matches = {
            urljoin(base_url, link.href)
            for link in parser.links
            if _normalize(link.text) == title
        }
        if len(matches) != 1:
            raise SourceDiscoveryError(
                f"Expected exactly one archive link titled {title!r}; found {len(matches)}"
            )
        url = next(iter(matches))
        _require_url(url)
        if _url_host(url) != _url_host(base_url):
            raise SourceDiscoveryError(f"Archive table link crosses hosts: {url}")
        parsed = urlparse(url)
        if parsed.path.casefold() != "/scripts/bs_viewbulletin.aspx" or not parsed.query:
            raise SourceDiscoveryError(f"Archive table link has an unexpected route: {url}")
        discovered[role] = url
    if discovered["major_sectors"] == discovered["industries"]:
        raise SourceDiscoveryError("Archive table titles resolve to the same URL")
    return discovered


def _new_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.25,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.2",
            "Accept-Language": "en-IN,en;q=0.9",
        }
    )
    return session


def _fetch(
    session: requests.Session,
    url: str,
    *,
    context: str,
    method: str = "GET",
    data: dict[str, str] | None = None,
) -> _FetchedPage:
    _require_url(url)
    try:
        response = session.request(
            method,
            url,
            data=data,
            stream=True,
            allow_redirects=True,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise SourceUnavailableError(f"{context} network retrieval failed: {exc}") from exc
    except requests.RequestException as exc:
        raise SourceUnavailableError(f"{context} request failed: {exc}") from exc
    try:
        requested_host = _url_host(url)
        for hop in [*response.history, response]:
            _require_url(hop.url)
            if _url_host(hop.url) != requested_host:
                raise SourceValidationError(
                    f"{context} redirected across hosts from {requested_host} to {_url_host(hop.url)}"
                )
        status = int(response.status_code)
        if status in {401, 403, 407, 451}:
            raise SourceAccessBlockedError(f"{context} returned HTTP {status}")
        if not 200 <= status < 300:
            raise SourceUnavailableError(f"{context} returned HTTP {status}")
        content_type = response.headers.get("Content-Type", "")
        if not any(value in content_type.casefold() for value in ("text/html", "application/xhtml+xml")):
            raise SourceValidationError(f"{context} returned non-HTML Content-Type {content_type!r}")
        expected = None
        if response.headers.get("Content-Length"):
            try:
                expected = int(response.headers["Content-Length"])
            except ValueError:
                expected = None
            if expected is not None and expected > MAX_RESPONSE_BYTES:
                raise SourceValidationError(f"{context} exceeds the {MAX_RESPONSE_BYTES}-byte bound")
        body = bytearray()
        digest = hashlib.sha256()
        try:
            for chunk in response.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise SourceValidationError(f"{context} exceeds the response bound")
                body.extend(chunk)
                digest.update(chunk)
        except requests.RequestException as exc:
            raise SourceUnavailableError(f"{context} response stream failed: {exc}") from exc
        if expected is not None and len(body) != expected:
            raise SourceValidationError(
                f"{context} was partially downloaded: expected {expected}, got {len(body)}"
            )
        content = bytes(body)
        del body
        lowered = content.decode("utf-8", errors="ignore").casefold()
        if any(marker in lowered for marker in CHALLENGE_MARKERS):
            raise SourceAccessBlockedError(f"{context} returned an access-control page")
        if "<html" not in lowered and "<!doctype html" not in lowered:
            raise SourceValidationError(f"{context} is not an HTML document")
        return _FetchedPage(
            content=content,
            requested_url=url,
            final_url=response.url,
            redirect_history=tuple(
                {
                    "status": item.status_code,
                    "url": item.url,
                    "location": item.headers.get("Location"),
                }
                for item in response.history
            ),
            status=status,
            content_type=content_type,
            byte_size=len(content),
            sha256=digest.hexdigest(),
        )
    finally:
        response.close()


def _cache_root(cache_dir: Path | str | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir).expanduser()
    configured = os.environ.get("INDIAMACRO_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return user_cache_path("indiamacro", appauthor=False)


def _history_cache_root(cache_dir: Path | str | None) -> Path:
    return _cache_root(cache_dir) / "rbi" / "sectoral_credit_history"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frame_from_v1(parsed: v1.ParsedSectoralCredit) -> pd.DataFrame:
    return transition.v1_release_observations(parsed)


def _parse_release(
    issue_month: str,
    major: bytes,
    industry: bytes,
    *,
    major_url: str,
    industry_url: str,
) -> _ParsedRelease:
    detected = transition.detect_supported_layout(
        major,
        industry,
        major_url=major_url,
        industry_url=industry_url,
    )
    expected_period = datetime.strptime(issue_month, "%Y-%m").strftime("%B %Y")
    if detected.bulletin_period != expected_period:
        raise SourceValidationError(
            f"Requested issue {issue_month} returned {detected.bulletin_period}"
        )
    if issue_month <= "2025-12":
        parsed = v2.parse_sectoral_credit_bulletin_v2_release(
            major,
            industry,
            major_sectors_url=major_url,
            industries_url=industry_url,
        )
        frame = parsed.observations
        metadata = parsed.metadata
    elif issue_month <= "2026-05":
        parsed = transition.parse_transition_release(
            major,
            industry,
            major_url=major_url,
            industry_url=industry_url,
        )
        frame = parsed.observations
        metadata = parsed.metadata
    else:
        parsed_v1 = v1.parse_sectoral_credit_bulletin(
            major,
            industry,
            major_sectors_url=major_url,
            industries_url=industry_url,
        )
        frame = _frame_from_v1(parsed_v1)
        return _ParsedRelease(
            observations=frame,
            notes=parsed_v1.notes,
            issue_month=issue_month,
            publication_date=parsed_v1.metadata.publication_date,
            layout_id=parsed_v1.metadata.layout_id,
            parser_version=parsed_v1.metadata.parser_version,
            signatures=detected.signatures,
            semantic_hash=v2.release_semantic_sha256(frame),
            provenance_hash=v2.release_provenance_sha256(frame),
            current_observation_date=parsed_v1.metadata.current_observation_date,
        )
    return _ParsedRelease(
        observations=frame,
        notes=parsed.notes,
        issue_month=issue_month,
        publication_date=metadata.publication_date,
        layout_id=metadata.layout_id,
        parser_version=metadata.parser_version,
        signatures=detected.signatures,
        semantic_hash=metadata.release_semantic_sha256,
        provenance_hash=metadata.provenance_bound_output_sha256,
        current_observation_date=metadata.current_observation_date,
    )


def _bundle_id(issue: str, tables: dict[str, dict[str, Any]], layout_id: str) -> str:
    payload = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "issue_month": issue,
        "layout_id": layout_id,
        "tables": {
            role: {
                "source_url": table["source_url"],
                "final_url": table["final_url"],
                "sha256": table["sha256"],
            }
            for role, table in sorted(tables.items())
        },
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _table_manifest(page: _FetchedPage, *, role: str, source_url: str) -> dict[str, Any]:
    return {
        "role": role,
        "title": TABLE_TITLES[role],
        "raw_filename": f"{role}.html",
        "source_url": source_url,
        "final_url": page.final_url,
        "redirect_history": list(page.redirect_history),
        "http_status": page.status,
        "content_type": page.content_type,
        "byte_size": page.byte_size,
        "sha256": page.sha256,
    }


def _manifest(
    parsed: _ParsedRelease,
    *,
    tables: dict[str, dict[str, Any]],
    retrieved_at: str,
) -> dict[str, Any]:
    bundle_id = _bundle_id(parsed.issue_month, tables, parsed.layout_id)
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": v1.DATASET_ID,
        "source_route": SOURCE_ROUTE,
        "issue_month": parsed.issue_month,
        "publication_date": parsed.publication_date,
        "table_titles": TABLE_TITLES,
        "tables": tables,
        "parser_layout_id": parsed.layout_id,
        "parser_version": parsed.parser_version,
        "structural_signature_sha256": parsed.signatures.structural_signature_sha256,
        "taxonomy_signature_sha256": parsed.signatures.taxonomy_signature_sha256,
        "methodology_signature_sha256": parsed.signatures.methodology_signature_sha256,
        "ingestion_timestamp_utc": retrieved_at,
        "bundle_id": bundle_id,
        "semantic_release_sha256": parsed.semantic_hash,
        "provenance_bound_release_sha256": parsed.provenance_hash,
        "current_observation_date": parsed.current_observation_date,
    }


def _manifest_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _commit_release_bundle(
    root: Path,
    manifest: dict[str, Any],
    major: bytes,
    industry: bytes,
) -> Path:
    issue_root = root / manifest["issue_month"]
    issue_root.mkdir(parents=True, exist_ok=True)
    target = issue_root / manifest["bundle_id"]
    if target.exists():
        _load_bundle(target)
        return target
    temporary = Path(tempfile.mkdtemp(prefix=".tmp-", dir=issue_root))
    try:
        (temporary / "major_sectors.html").write_bytes(major)
        (temporary / "industries.html").write_bytes(industry)
        (temporary / "manifest.json").write_bytes(_manifest_bytes(manifest))
        os.replace(temporary, target)
    except OSError as exc:
        raise CacheIntegrityError(f"Could not commit history cache atomically: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def _read_manifest(bundle: Path) -> dict[str, Any]:
    path = bundle / "manifest.json"
    try:
        if path.stat().st_size > 1024 * 1024:
            raise CacheIntegrityError("History manifest is unexpectedly large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except CacheIntegrityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CacheIntegrityError(f"Cannot read history manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CacheIntegrityError("History manifest must be a JSON object")
    if "manifest_schema_version" not in value:
        raise CacheIntegrityError("History manifest lacks a schema version")
    if value["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise CacheIncompatibleError(
            f"History cache schema {value['manifest_schema_version']!r} is incompatible"
        )
    required = {
        "dataset_id", "source_route", "issue_month", "publication_date",
        "table_titles", "tables", "parser_layout_id", "parser_version",
        "structural_signature_sha256", "taxonomy_signature_sha256",
        "methodology_signature_sha256", "ingestion_timestamp_utc", "bundle_id",
        "semantic_release_sha256", "provenance_bound_release_sha256",
        "current_observation_date",
    }
    missing = sorted(required - value.keys())
    if missing:
        raise CacheIntegrityError(f"History manifest is missing fields: {missing}")
    if value["dataset_id"] != v1.DATASET_ID or value["source_route"] != SOURCE_ROUTE:
        raise CacheIntegrityError("History manifest identifies another dataset")
    if not re.fullmatch(r"[0-9a-f]{64}", bundle.name):
        raise CacheIntegrityError("History bundle directory is not a SHA-256 identifier")
    if value["bundle_id"] != bundle.name or value["issue_month"] != bundle.parent.name:
        raise CacheIntegrityError("History cache directory identity disagrees with manifest")
    try:
        _issue(value["issue_month"], name="manifest issue_month")
        _iso_date(value["publication_date"], name="manifest publication_date")
        _iso_date(value["current_observation_date"], name="manifest current_observation_date")
    except (TypeError, ValueError) as exc:
        raise CacheIntegrityError("History manifest contains an invalid issue or date") from exc
    if value["table_titles"] != TABLE_TITLES:
        raise CacheIntegrityError("History manifest table titles disagree with the contract")
    for field in (
        "structural_signature_sha256",
        "taxonomy_signature_sha256",
        "methodology_signature_sha256",
        "bundle_id",
        "semantic_release_sha256",
        "provenance_bound_release_sha256",
    ):
        if not isinstance(value[field], str) or not re.fullmatch(r"[0-9a-f]{64}", value[field]):
            raise CacheIntegrityError(f"History manifest {field} is not a SHA-256 digest")
    for field in ("parser_layout_id", "parser_version"):
        if not isinstance(value[field], str) or not value[field]:
            raise CacheIntegrityError(f"History manifest {field} must be a non-empty string")
    try:
        timestamp = datetime.fromisoformat(value["ingestion_timestamp_utc"])
    except (TypeError, ValueError) as exc:
        raise CacheIntegrityError("History ingestion timestamp is invalid") from exc
    if timestamp.tzinfo is None:
        raise CacheIntegrityError("History ingestion timestamp must include a timezone")
    return value


def _read_raw(bundle: Path, table: dict[str, Any], role: str) -> bytes:
    if not isinstance(table, dict):
        raise CacheIntegrityError(f"History {role} table metadata must be an object")
    required = {
        "role", "title", "raw_filename", "source_url", "final_url",
        "redirect_history", "http_status", "content_type", "byte_size", "sha256",
    }
    missing = sorted(required - table.keys())
    if missing:
        raise CacheIntegrityError(f"History {role} metadata is missing fields: {missing}")
    if table["role"] != role or table["title"] != TABLE_TITLES[role]:
        raise CacheIntegrityError(f"History {role} table identity is invalid")
    if table["raw_filename"] != f"{role}.html":
        raise CacheIntegrityError(f"History {role} raw filename is invalid")
    for field in ("source_url", "final_url"):
        if not isinstance(table[field], str) or not _approved_url(table[field]):
            raise CacheIntegrityError(f"History {role} {field} is not approved RBI HTTPS")
    if _url_host(table["source_url"]) != _url_host(table["final_url"]):
        raise CacheIntegrityError(f"History {role} cached redirect crosses hosts")
    if table["http_status"] != 200:
        raise CacheIntegrityError(f"History {role} cached HTTP status is invalid")
    if not isinstance(table["content_type"], str) or not any(
        value in table["content_type"].casefold()
        for value in ("text/html", "application/xhtml+xml")
    ):
        raise CacheIntegrityError(f"History {role} cached Content-Type is invalid")
    if (
        not isinstance(table["byte_size"], int)
        or isinstance(table["byte_size"], bool)
        or not 0 < table["byte_size"] <= MAX_RESPONSE_BYTES
    ):
        raise CacheIntegrityError(f"History {role} cached byte size is invalid")
    if not isinstance(table["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", table["sha256"]):
        raise CacheIntegrityError(f"History {role} cached SHA-256 is invalid")
    if not isinstance(table["redirect_history"], list):
        raise CacheIntegrityError(f"History {role} redirect history must be a list")
    for hop in table["redirect_history"]:
        if not isinstance(hop, dict) or not isinstance(hop.get("url"), str):
            raise CacheIntegrityError(f"History {role} redirect history is malformed")
        if not _approved_url(hop["url"]) or _url_host(hop["url"]) != _url_host(table["source_url"]):
            raise CacheIntegrityError(f"History {role} redirect history crosses hosts")
    path = bundle / table["raw_filename"]
    try:
        size = path.stat().st_size
        if size > MAX_RESPONSE_BYTES:
            raise CacheIntegrityError(f"History {role} raw page exceeds parser bound")
        raw = path.read_bytes()
    except CacheIntegrityError:
        raise
    except OSError as exc:
        raise CacheIntegrityError(f"Cannot read history {role} page: {exc}") from exc
    if size != table["byte_size"] or hashlib.sha256(raw).hexdigest() != table["sha256"]:
        raise CacheIntegrityError(f"History {role} raw size or hash disagrees")
    return raw


def _load_bundle(bundle: Path) -> tuple[_ParsedRelease, dict[str, Any]]:
    manifest = _read_manifest(bundle)
    tables = manifest["tables"]
    if not isinstance(tables, dict) or set(tables) != set(TABLE_TITLES):
        raise CacheIntegrityError("History manifest table roles are incomplete")
    major = _read_raw(bundle, tables["major_sectors"], "major_sectors")
    industry = _read_raw(bundle, tables["industries"], "industries")
    expected_id = _bundle_id(manifest["issue_month"], tables, manifest["parser_layout_id"])
    if expected_id != manifest["bundle_id"]:
        raise CacheIntegrityError("History bundle ID does not match raw provenance")
    try:
        parsed = _parse_release(
            manifest["issue_month"],
            major,
            industry,
            major_url=tables["major_sectors"]["final_url"],
            industry_url=tables["industries"]["final_url"],
        )
    except (v1.SectoralCreditParseError, SourceValidationError) as exc:
        raise CacheIntegrityError(f"History cached pages no longer parse: {exc}") from exc
    finally:
        del major, industry
    comparisons = {
        "publication_date": parsed.publication_date,
        "parser_layout_id": parsed.layout_id,
        "parser_version": parsed.parser_version,
        "structural_signature_sha256": parsed.signatures.structural_signature_sha256,
        "taxonomy_signature_sha256": parsed.signatures.taxonomy_signature_sha256,
        "methodology_signature_sha256": parsed.signatures.methodology_signature_sha256,
        "semantic_release_sha256": parsed.semantic_hash,
        "provenance_bound_release_sha256": parsed.provenance_hash,
        "current_observation_date": parsed.current_observation_date,
    }
    for field, actual in comparisons.items():
        if manifest[field] != actual:
            raise CacheIntegrityError(f"History cached {field} disagrees with reparsed content")
    return parsed, manifest


def _bundle_candidates(root: Path, issue: str) -> list[Path]:
    issue_root = root / issue
    if not issue_root.is_dir():
        return []
    return sorted(
        (
            path
            for path in issue_root.iterdir()
            if path.is_dir() and not path.name.startswith(".tmp-")
        ),
        key=lambda path: path.name,
    )


def _load_issue(root: Path, issue: str) -> tuple[_ParsedRelease, dict[str, Any]]:
    candidates = _bundle_candidates(root, issue)
    if not candidates:
        raise HistoryCacheNotFoundError(issue)
    ranked = []
    for candidate in candidates:
        manifest = _read_manifest(candidate)
        ranked.append((datetime.fromisoformat(manifest["ingestion_timestamp_utc"]), candidate))
    return _load_bundle(max(ranked, key=lambda item: (item[0], item[1].name))[1])


def _release_source_manifest(
    manifest: dict[str, Any], *, from_cache: bool
) -> ReleaseSourceManifest:
    tables = manifest["tables"]
    return ReleaseSourceManifest(
        issue_month=manifest["issue_month"],
        publication_date=manifest["publication_date"],
        bundle_id=manifest["bundle_id"],
        layout_id=manifest["parser_layout_id"],
        parser_version=manifest["parser_version"],
        major_sectors_title=tables["major_sectors"]["title"],
        major_sectors_raw_filename=tables["major_sectors"]["raw_filename"],
        major_sectors_url=tables["major_sectors"]["source_url"],
        major_sectors_final_url=tables["major_sectors"]["final_url"],
        major_sectors_byte_size=tables["major_sectors"]["byte_size"],
        industries_title=tables["industries"]["title"],
        industries_raw_filename=tables["industries"]["raw_filename"],
        industries_url=tables["industries"]["source_url"],
        industries_final_url=tables["industries"]["final_url"],
        industries_byte_size=tables["industries"]["byte_size"],
        major_sectors_sha256=tables["major_sectors"]["sha256"],
        industries_sha256=tables["industries"]["sha256"],
        semantic_release_sha256=manifest["semantic_release_sha256"],
        provenance_bound_release_sha256=manifest["provenance_bound_release_sha256"],
        structural_signature_sha256=manifest["structural_signature_sha256"],
        taxonomy_signature_sha256=manifest["taxonomy_signature_sha256"],
        methodology_signature_sha256=manifest["methodology_signature_sha256"],
        retrieved_at_utc=manifest["ingestion_timestamp_utc"],
        from_cache=from_cache,
    )


def _records(frame: pd.DataFrame) -> tuple[HistoryObservation, ...]:
    return tuple(
        HistoryObservation(**record)
        for record in frame.loc[:, v2.RELEASE_OBSERVATION_COLUMNS].to_dict("records")
    )


def _records_to_frame(records: Sequence[HistoryObservation]) -> pd.DataFrame:
    return pd.DataFrame(
        [tuple(getattr(item, column) for column in v2.RELEASE_OBSERVATION_COLUMNS) for item in records],
        columns=v2.RELEASE_OBSERVATION_COLUMNS,
    )


def _selection_sort_key(item: HistoryObservation) -> tuple[str, str, str, str, str]:
    return (
        item.observation_date,
        item.publication_date,
        item.column_role,
        item.source_table,
        item.source_row_code,
    )


def _resolved_sort_key(item: HistoryObservation) -> tuple[str, str, str, str, str]:
    return (
        item.observation_date,
        item.series_id,
        item.measure,
        item.population_id,
        item.publication_date,
    )


def _methodology_boundaries(issues: Sequence[str]) -> tuple[MethodologyBoundary, ...]:
    boundaries = []
    if any(issue <= "2026-01" for issue in issues) and any(issue >= "2026-02" for issue in issues):
        boundaries.append(
            MethodologyBoundary(
                from_issue="2026-01",
                to_issue="2026-02",
                classification=transition.COMPARABLE_WITH_DATE_BASIS_CHANGE,
                description=(
                    "Current observations switch from last reporting Friday to calendar month-end; "
                    "the prior-year YoY base retains the old reporting-fortnight definition."
                ),
            )
        )
    return tuple(boundaries)


def _available(records: Sequence[HistoryObservation]) -> tuple[AvailableSeries, ...]:
    values: dict[str, AvailableSeries] = {}
    for item in records:
        candidate = AvailableSeries(
            item.series_id,
            item.measure,
            item.unit,
            item.population_id,
            item.source_row_code,
            item.source_label,
        )
        existing = values.get(item.series_id)
        if existing is not None and existing != candidate:
            # Label presentation may vary while canonical identity is unchanged.
            if (
                existing.measure,
                existing.unit,
                existing.population_id,
                existing.source_row_code,
            ) != (
                candidate.measure,
                candidate.unit,
                candidate.population_id,
                candidate.source_row_code,
            ):
                raise SourceValidationError(f"Series identity conflict for {item.series_id}")
            continue
        values[item.series_id] = candidate
    return tuple(values[key] for key in sorted(values))


def _build_result(
    issues: Sequence[str],
    releases: Sequence[tuple[_ParsedRelease, dict[str, Any], bool]],
    *,
    request_count: int,
) -> SectoralCreditHistoryResult:
    frames = [item[0].observations for item in releases]
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(
        ["publication_date", "source_table", "source_row_code", "column_role", "series_id"],
        kind="stable",
    ).reset_index(drop=True)
    duplicates = int(combined.duplicated(list(HISTORY_VINTAGE_KEY), keep=False).sum())
    if duplicates:
        raise SourceValidationError(f"History contains {duplicates} duplicate vintage keys")
    current = combined.loc[combined["column_role"].isin(CURRENT_ROLES)].copy()
    current = current.sort_values(
        ["publication_date", "source_table", "source_row_code", "measure"], kind="stable"
    ).reset_index(drop=True)
    vintage_records = _records(combined)
    current_records = _records(current)
    manifests = tuple(
        _release_source_manifest(manifest, from_cache=from_cache)
        for _parsed, manifest, from_cache in releases
    )
    notes = tuple(
        HistoryNote(parsed.issue_month, note.source_table, note.text)
        for parsed, _manifest_value, _from_cache in releases
        for note in parsed.notes
    )
    metadata = SectoralCreditHistoryMetadata(
        dataset_id=v1.DATASET_ID,
        source_route=SOURCE_ROUTE,
        selected_start_issue=issues[0],
        selected_end_issue=issues[-1],
        supported_start_issue=SUPPORTED_START,
        supported_end_issue=SUPPORTED_END,
        release_count=len(releases),
        vintage_observation_count=len(combined),
        current_observation_count=len(current),
        unique_series_count=int(combined["series_id"].nunique()),
        semantic_vintage_sha256=v2.release_semantic_sha256(combined),
        provenance_bound_vintage_sha256=v2.release_provenance_sha256(combined),
        semantic_current_sha256=v2.release_semantic_sha256(current),
        provenance_bound_current_sha256=v2.release_provenance_sha256(current),
        methodology_boundaries=_methodology_boundaries(issues),
        source_manifests=manifests,
        request_count=request_count,
    )
    if issues[0] == SUPPORTED_START and issues[-1] == SUPPORTED_END:
        if metadata.semantic_vintage_sha256 != EXPECTED_FULL_SEMANTIC_HASH:
            raise SourceValidationError("Complete history semantic vintage hash changed")
        if len(combined) != 5950 or len(current) != 3060:
            raise SourceValidationError("Complete history observation counts changed")
    return SectoralCreditHistoryResult(
        vintages=vintage_records,
        current_observations=current_records,
        metadata=metadata,
        notes=notes,
        available_series=_available(vintage_records),
    )


def _retrieve_issues(
    root: Path,
    issues: Sequence[str],
    *,
    refresh: bool,
) -> tuple[list[tuple[_ParsedRelease, dict[str, Any], bool]], int]:
    releases: dict[str, tuple[_ParsedRelease, dict[str, Any], bool]] = {}
    missing = []
    if not refresh:
        for issue in issues:
            try:
                parsed, manifest = _load_issue(root, issue)
                releases[issue] = (parsed, manifest, True)
            except HistoryCacheNotFoundError:
                missing.append(issue)
    else:
        missing = list(issues)
    if not missing:
        return [releases[issue] for issue in issues], 0
    request_count = 0
    with _new_session() as session:
        archive = _fetch(session, ARCHIVE_URL, context="RBI Bulletin archive entry")
        request_count += 1
        archive_html = _decode(archive.content, context="RBI Bulletin archive entry")
        for issue in missing:
            payload = _form_payload(archive_html, issue)
            issue_page = _fetch(
                session,
                ARCHIVE_URL,
                method="POST",
                data=payload,
                context=f"RBI Bulletin issue {issue}",
            )
            request_count += 1
            issue_html = _decode(issue_page.content, context=f"RBI Bulletin issue {issue}")
            urls = _discover_tables(issue_html, issue_page.final_url)
            major_page = _fetch(
                session, urls["major_sectors"], context=f"{issue} Table 15"
            )
            request_count += 1
            industry_page = _fetch(
                session, urls["industries"], context=f"{issue} Table 16"
            )
            request_count += 1
            parsed = _parse_release(
                issue,
                major_page.content,
                industry_page.content,
                major_url=major_page.final_url,
                industry_url=industry_page.final_url,
            )
            tables = {
                "major_sectors": _table_manifest(
                    major_page, role="major_sectors", source_url=urls["major_sectors"]
                ),
                "industries": _table_manifest(
                    industry_page, role="industries", source_url=urls["industries"]
                ),
            }
            manifest = _manifest(parsed, tables=tables, retrieved_at=_utc_now())
            _commit_release_bundle(
                root,
                manifest,
                major_page.content,
                industry_page.content,
            )
            releases[issue] = (parsed, manifest, False)
            del issue_page, issue_html, major_page, industry_page
    return [releases[issue] for issue in issues], request_count


def _load_offline(
    root: Path, issues: Sequence[str]
) -> list[tuple[_ParsedRelease, dict[str, Any], bool]]:
    missing = [issue for issue in issues if not _bundle_candidates(root, issue)]
    if missing:
        raise HistoryCacheNotFoundError(
            f"Missing history cache issues: {', '.join(missing)}"
        )
    return [(*_load_issue(root, issue), True) for issue in issues]


def sectoral_credit_history(
    start_issue: str = SUPPORTED_START,
    end_issue: str = SUPPORTED_END,
    *,
    refresh: bool = False,
    offline: bool = False,
    cache_dir: Path | str | None = None,
) -> SectoralCreditHistoryResult:
    """Return verified RBI sectoral-credit publication vintages for an issue range."""
    if refresh and offline:
        raise ValueError("refresh=True cannot be combined with offline=True")
    issues = _issue_range(start_issue, end_issue)
    root = _history_cache_root(cache_dir)
    if offline:
        releases = _load_offline(root, issues)
        request_count = 0
    else:
        releases, request_count = _retrieve_issues(root, issues, refresh=refresh)
    return _build_result(issues, releases, request_count=request_count)


__all__ = [
    "AvailableSeries",
    "HistoryCacheNotFoundError",
    "HistoryNote",
    "HistoryObservation",
    "MethodologyBoundary",
    "ReleaseSourceManifest",
    "ResolutionConflictError",
    "ResolvedHistory",
    "SectoralCreditHistoryError",
    "SectoralCreditHistoryMetadata",
    "SectoralCreditHistoryResult",
    "UnknownSeriesError",
    "UnsupportedIssueRangeError",
    "sectoral_credit_history",
]
