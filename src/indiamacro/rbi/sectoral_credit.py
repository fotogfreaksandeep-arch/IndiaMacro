"""Live RBI Bulletin connector and verified cache for sectoral bank credit."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests
from platformdirs import user_cache_path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from indiamacro import __version__
from indiamacro.rbi.sectoral_credit_bulletin import (
    DATASET_ID,
    INDUSTRY_TITLE,
    LAYOUT_ID,
    MAJOR_TITLE,
    SectoralCreditParseError,
    parse_sectoral_credit_bulletin,
)


BULLETIN_INDEX_URL: Final = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx"
DEDICATED_INDEX_URL: Final = "https://rbi.org.in/Scripts/Data_Sectoral_Deployment.aspx"
SOURCE_ROUTE: Final = "RBI_BULLETIN_CURRENT_STATISTICS"
MANIFEST_SCHEMA_VERSION: Final = 2
MAX_RESPONSE_BYTES: Final = 5 * 1024 * 1024
CHUNK_SIZE: Final = 64 * 1024
CONNECT_TIMEOUT_SECONDS: Final = 10
READ_TIMEOUT_SECONDS: Final = 45
USER_AGENT: Final = (
    f"IndiaMacro/{__version__} RBI-sectoral-credit (+https://rbi.org.in/)"
)

TABLE_TITLES: Final = {
    "major_sectors": MAJOR_TITLE,
    "industries": INDUSTRY_TITLE,
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
DEDICATED_TITLE_RE: Final = re.compile(
    r"^Sectoral Deployment of Bank Credit\s*[\u2013\u2014-]\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})$",
    re.IGNORECASE,
)
DATE_RE: Final = re.compile(r"\b([A-Z][a-z]{2})\s+(\d{1,2}),\s+(20\d{2})\b")


class SectoralCreditConnectorError(RuntimeError):
    """Base class for connector and cache failures."""


class SourceUnavailableError(SectoralCreditConnectorError):
    """The official source could not be retrieved."""


class SourceAccessBlockedError(SourceUnavailableError):
    """RBI returned an access-control or human-verification response."""


class SourceDiscoveryError(SectoralCreditConnectorError):
    """Required exact source links could not be discovered uniquely."""


class SourceValidationError(SectoralCreditConnectorError):
    """A retrieved response failed URL, HTTP, or semantic validation."""


class CacheNotFoundError(SectoralCreditConnectorError):
    """No committed sectoral-credit cache bundle is available."""


class CacheIncompatibleError(SectoralCreditConnectorError):
    """A pre-release cache uses hash semantics incompatible with this version."""


class CacheIntegrityError(SectoralCreditConnectorError):
    """A committed cache bundle failed manifest, hash, or parser validation."""


@dataclass(frozen=True)
class SectoralCreditMetadata:
    dataset_id: str
    source_route: str
    bulletin_period: str
    bulletin_publication_date: str
    latest_observation_date: str
    retrieved_at_utc: str
    from_cache: bool
    cache_bundle_id: str
    bulletin_index_url: str
    major_sectors_url: str
    industries_url: str
    major_sectors_sha256: str
    industries_sha256: str
    parser_version: str
    layout_id: str
    semantic_observations_sha256: str
    provenance_bound_output_sha256: str
    latest_dedicated_release_title: str | None
    latest_dedicated_release_date: str | None
    latest_dedicated_release_period: str | None
    freshness_gap_months: int | None
    freshness_status: str
    freshness_diagnostic: str | None


@dataclass(frozen=True)
class SectoralCreditResult:
    observations: pd.DataFrame
    metadata: SectoralCreditMetadata
    notes: tuple[str, ...]


@dataclass(frozen=True)
class _Link:
    text: str
    href: str
    position: int


@dataclass(frozen=True)
class _DedicatedRelease:
    title: str
    url: str
    period: str
    release_date: str | None


@dataclass(frozen=True)
class _FetchedPage:
    content: bytes
    requested_url: str
    final_url: str
    redirect_history: tuple[dict[str, Any], ...]
    http_status: int
    content_type: str
    byte_size: int
    sha256: str


class _LinkTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[_Link] = []
        self._href: str | None = None
        self._anchor_parts: list[str] = []
        self._anchor_position = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._anchor_parts = []
            self._anchor_position = len(self.text_parts)

    def handle_data(self, data: str) -> None:
        text = " ".join(unescape(data).split())
        if not text:
            return
        self.text_parts.append(text)
        if self._href is not None:
            self._anchor_parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append(
                _Link(" ".join(self._anchor_parts), self._href, self._anchor_position)
            )
            self._href = None
            self._anchor_parts = []


def _normalize(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _is_approved_rbi_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        host == "rbi.org.in" or host.endswith(".rbi.org.in")
    )


def _require_approved_url(url: str) -> None:
    if not _is_approved_rbi_url(url):
        raise SourceValidationError(f"Refusing URL outside approved RBI HTTPS hosts: {url}")


def _decode_html(content: bytes, *, context: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceValidationError(f"{context} is not valid UTF-8 HTML") from exc


def _discover_bulletin_tables(html: str, base_url: str) -> dict[str, str]:
    parser = _LinkTextParser()
    parser.feed(html)
    discovered: dict[str, str] = {}
    for role, title in TABLE_TITLES.items():
        matches = [
            urljoin(base_url, link.href)
            for link in parser.links
            if _normalize(link.text) == title
        ]
        if len(matches) != 1:
            raise SourceDiscoveryError(
                f"Expected exactly one link with title {title!r}; found {len(matches)}"
            )
        _require_approved_url(matches[0])
        discovered[role] = matches[0]
    if discovered["major_sectors"] == discovered["industries"]:
        raise SourceDiscoveryError("The two Bulletin titles resolve to the same URL")
    return discovered


def _parse_nearby_date(text_parts: list[str], position: int) -> str | None:
    start = max(0, position - 4)
    nearby = text_parts[start : position + 5]
    candidates: list[tuple[int, date]] = []
    for offset, text in enumerate(nearby):
        match = DATE_RE.search(text)
        if not match:
            continue
        try:
            parsed = datetime.strptime(match.group(0), "%b %d, %Y").date()
        except ValueError:
            continue
        candidates.append((abs((start + offset) - position), parsed))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1].isoformat()


def _discover_latest_dedicated_release(html: str, base_url: str) -> _DedicatedRelease:
    parser = _LinkTextParser()
    parser.feed(html)
    releases: list[_DedicatedRelease] = []
    for link in parser.links:
        title = _normalize(link.text)
        match = DEDICATED_TITLE_RE.fullmatch(title)
        if not match:
            continue
        url = urljoin(base_url, link.href)
        _require_approved_url(url)
        period_date = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%B %Y")
        releases.append(
            _DedicatedRelease(
                title=title,
                url=url,
                period=period_date.strftime("%Y-%m"),
                release_date=_parse_nearby_date(parser.text_parts, link.position),
            )
        )
    if not releases:
        raise SourceDiscoveryError("No exact-title Sectoral Deployment release was found")
    return max(
        releases,
        key=lambda item: (item.period, item.release_date or "", item.url),
    )


def _new_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        status=2,
        backoff_factor=0.25,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redirect_history(response: requests.Response) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "status": item.status_code,
            "url": item.url,
            "location": item.headers.get("Location"),
        }
        for item in response.history
    )


def _fetch_html(session: requests.Session, url: str, *, context: str) -> _FetchedPage:
    _require_approved_url(url)
    try:
        response = session.get(
            url,
            stream=True,
            allow_redirects=True,
            timeout=(CONNECT_TIMEOUT_SECONDS, READ_TIMEOUT_SECONDS),
        )
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise SourceUnavailableError(f"{context} network retrieval failed: {exc}") from exc
    except requests.RequestException as exc:
        raise SourceUnavailableError(f"{context} HTTP request failed: {exc}") from exc

    try:
        for hop in [*response.history, response]:
            _require_approved_url(hop.url)
        status = int(response.status_code)
        if status in {401, 403, 407, 451}:
            raise SourceAccessBlockedError(f"{context} returned HTTP {status}")
        if status < 200 or status >= 300:
            raise SourceUnavailableError(f"{context} returned HTTP {status}")

        content_type = response.headers.get("Content-Type", "")
        if not any(
            marker in content_type.casefold()
            for marker in ("text/html", "application/xhtml+xml")
        ):
            raise SourceValidationError(
                f"{context} returned non-HTML Content-Type {content_type!r}"
            )
        content_length = response.headers.get("Content-Length")
        expected_length: int | None = None
        if content_length:
            try:
                expected_length = int(content_length)
            except ValueError:
                expected_length = None
            if expected_length is not None and expected_length > MAX_RESPONSE_BYTES:
                raise SourceValidationError(
                    f"{context} exceeds the {MAX_RESPONSE_BYTES}-byte response bound"
                )

        body = bytearray()
        digest = hashlib.sha256()
        try:
            for chunk in response.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise SourceValidationError(
                        f"{context} exceeds the {MAX_RESPONSE_BYTES}-byte response bound"
                    )
                body.extend(chunk)
                digest.update(chunk)
        except requests.RequestException as exc:
            raise SourceUnavailableError(f"{context} response stream failed: {exc}") from exc
        if expected_length is not None and len(body) != expected_length:
            raise SourceValidationError(
                f"{context} was partially downloaded: expected {expected_length}, got {len(body)}"
            )
        content = bytes(body)
        del body
        lowered = content.decode("utf-8", errors="ignore").casefold()
        if any(marker in lowered for marker in CHALLENGE_MARKERS):
            raise SourceAccessBlockedError(
                f"{context} returned an RBI access-control or human-verification page"
            )
        if "<html" not in lowered and "<!doctype html" not in lowered:
            raise SourceValidationError(f"{context} does not contain an HTML document")
        return _FetchedPage(
            content=content,
            requested_url=url,
            final_url=response.url,
            redirect_history=_redirect_history(response),
            http_status=status,
            content_type=content_type,
            byte_size=len(content),
            sha256=digest.hexdigest(),
        )
    finally:
        response.close()


def _validate_bulletin_index(page: _FetchedPage) -> dict[str, str]:
    html = _decode_html(page.content, context="Bulletin index")
    if "Reserve Bank of India" not in html and "RBI" not in html:
        raise SourceValidationError("Bulletin index lacks an expected RBI page marker")
    return _discover_bulletin_tables(html, page.final_url)


def _validate_table_page(page: _FetchedPage, *, role: str) -> None:
    title = TABLE_TITLES[role]
    html = _decode_html(page.content, context=title)
    normalized = _normalize(re.sub(r"<[^>]+>", " ", html))
    required = (title, "₹ Crore", "Outstanding as on", "Growth (%)", "Y-o-Y")
    missing = [marker for marker in required if marker not in normalized]
    if missing:
        raise SourceValidationError(f"{title}: response is missing semantic markers {missing}")
    numeric_cells = re.findall(r">\s*[+-]?\d+(?:\.\d+)?\s*<", html)
    if len(numeric_cells) < 20:
        raise SourceValidationError(f"{title}: response lacks meaningful numeric data rows")


def _freshness_from_release(
    release: _DedicatedRelease, latest_observation_date: str
) -> dict[str, Any]:
    observed = datetime.strptime(latest_observation_date, "%Y-%m-%d")
    release_period = datetime.strptime(release.period, "%Y-%m")
    gap = (release_period.year - observed.year) * 12 + release_period.month - observed.month
    if gap > 0:
        status = "LAGGING_DEDICATED_RELEASE"
    elif gap < 0:
        status = "AHEAD_OF_DEDICATED_RELEASE"
    else:
        status = "CURRENT_WITH_DEDICATED_RELEASE"
    return {
        "latest_dedicated_release_title": release.title,
        "latest_dedicated_release_date": release.release_date,
        "latest_dedicated_release_period": release.period,
        "freshness_gap_months": gap,
        "freshness_status": status,
        "freshness_diagnostic": None,
    }


def _unknown_freshness(exc: Exception) -> dict[str, Any]:
    return {
        "latest_dedicated_release_title": None,
        "latest_dedicated_release_date": None,
        "latest_dedicated_release_period": None,
        "freshness_gap_months": None,
        "freshness_status": "UNKNOWN",
        "freshness_diagnostic": f"{type(exc).__name__}: {exc}",
    }


def _latest_observation_date(observations: pd.DataFrame) -> str:
    if observations.empty:
        raise SourceValidationError("The parser returned no observations")
    return str(observations["observation_date"].max())


def _bundle_identity(tables: dict[str, dict[str, Any]]) -> str:
    identity = {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "tables": {
            role: {
                "discovered_url": values["discovered_url"],
                "final_url": values["final_url"],
                "sha256": values["sha256"],
            }
            for role, values in sorted(tables.items())
        }
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _table_manifest(page: _FetchedPage, *, role: str, discovered_url: str) -> dict[str, Any]:
    return {
        "role": role,
        "title": TABLE_TITLES[role],
        "file": f"{role}.html",
        "discovered_url": discovered_url,
        "final_url": page.final_url,
        "redirect_history": list(page.redirect_history),
        "http_status": page.http_status,
        "content_type": page.content_type,
        "byte_size": page.byte_size,
        "sha256": page.sha256,
    }


def _cache_root(cache_dir: Path | str | None) -> Path:
    if cache_dir is not None:
        return Path(cache_dir).expanduser()
    configured = os.environ.get("INDIAMACRO_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return user_cache_path("indiamacro", appauthor=False)


def _dataset_cache_root(cache_dir: Path | str | None) -> Path:
    return _cache_root(cache_dir) / "rbi" / "sectoral_credit"


def _manifest_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _commit_bundle(
    cache_root: Path,
    manifest: dict[str, Any],
    major_content: bytes,
    industry_content: bytes,
) -> Path:
    publication_root = cache_root / manifest["bulletin_publication_date"]
    publication_root.mkdir(parents=True, exist_ok=True)
    target = publication_root / manifest["cache_bundle_id"]
    if target.exists():
        existing = _load_manifest(target)
        existing_tables = _require_mapping(existing["tables"], context="Cache tables")
        if set(existing_tables) != set(TABLE_TITLES):
            raise CacheIntegrityError("Existing cache bundle has incomplete table roles")
        for role, expected_content in (
            ("major_sectors", major_content),
            ("industries", industry_content),
        ):
            table = _validate_table_manifest(existing_tables.get(role), role=role)
            cached_content = _read_cached_table(target, table, role=role)
            if cached_content != expected_content:
                raise CacheIntegrityError(
                    f"Existing bundle {target.name} has different immutable {role} content"
                )
        if _bundle_identity(existing_tables) != target.name:
            raise CacheIntegrityError("Existing cache bundle source identity is inconsistent")
        return target
    temporary = Path(tempfile.mkdtemp(prefix=".tmp-", dir=publication_root))
    try:
        (temporary / "major_sectors.html").write_bytes(major_content)
        (temporary / "industries.html").write_bytes(industry_content)
        (temporary / "manifest.json").write_bytes(_manifest_bytes(manifest))
        os.replace(temporary, target)
    except OSError as exc:
        raise CacheIntegrityError(f"Could not commit cache bundle atomically: {exc}") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def _require_mapping(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CacheIntegrityError(f"{context} must be a JSON object")
    return value


def _load_manifest(bundle_dir: Path) -> dict[str, Any]:
    path = bundle_dir / "manifest.json"
    try:
        if path.stat().st_size > 1024 * 1024:
            raise CacheIntegrityError(f"Cache manifest is unexpectedly large: {path}")
        value = json.loads(path.read_text(encoding="utf-8"))
    except CacheIntegrityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CacheIntegrityError(f"Cannot read cache manifest {path}: {exc}") from exc
    manifest = _require_mapping(value, context="Cache manifest")
    if "manifest_schema_version" not in manifest:
        raise CacheIntegrityError("Cache manifest is missing its schema version")
    if manifest["manifest_schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise CacheIncompatibleError(
            "Cache manifest schema is incompatible with v0.1.0 hash semantics; "
            "refresh online or remove the obsolete pre-release bundle"
        )
    required = {
        "manifest_schema_version",
        "dataset_id",
        "source_route",
        "cache_bundle_id",
        "bulletin_period",
        "bulletin_publication_date",
        "latest_observation_date",
        "retrieved_at_utc",
        "bulletin_index_url",
        "tables",
        "layout_id",
        "parser_version",
        "semantic_observations_sha256",
        "provenance_bound_output_sha256",
        "freshness",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise CacheIntegrityError(f"Cache manifest is missing fields: {missing}")
    if manifest["dataset_id"] != DATASET_ID or manifest["source_route"] != SOURCE_ROUTE:
        raise CacheIntegrityError("Cache manifest identifies a different dataset or source route")
    if manifest["cache_bundle_id"] != bundle_dir.name:
        raise CacheIntegrityError("Cache bundle ID does not match its directory name")
    if manifest["layout_id"] != LAYOUT_ID:
        raise CacheIntegrityError("Cache manifest has an unsupported parser layout ID")
    if manifest["bulletin_index_url"] != BULLETIN_INDEX_URL:
        raise CacheIntegrityError("Cache manifest has an unexpected Bulletin index URL")
    try:
        retrieved = datetime.fromisoformat(manifest["retrieved_at_utc"])
    except (TypeError, ValueError) as exc:
        raise CacheIntegrityError("Cache retrieval timestamp is invalid") from exc
    if retrieved.tzinfo is None:
        raise CacheIntegrityError("Cache retrieval timestamp must include a timezone")
    return manifest


def _validate_table_manifest(value: Any, *, role: str) -> dict[str, Any]:
    table = _require_mapping(value, context=f"{role} table metadata")
    required = {
        "role",
        "title",
        "file",
        "discovered_url",
        "final_url",
        "redirect_history",
        "http_status",
        "content_type",
        "byte_size",
        "sha256",
    }
    missing = sorted(required - table.keys())
    if missing:
        raise CacheIntegrityError(f"Cached {role} table metadata is missing fields: {missing}")
    if table["role"] != role or table["title"] != TABLE_TITLES[role]:
        raise CacheIntegrityError(f"Cache table metadata is invalid for {role}")
    for url_field in ("discovered_url", "final_url"):
        if not isinstance(table[url_field], str) or not _is_approved_rbi_url(table[url_field]):
            raise CacheIntegrityError(f"Cached {role} {url_field} is not an approved RBI URL")
    if not isinstance(table["redirect_history"], list):
        raise CacheIntegrityError(f"Cached {role} redirect history must be a list")
    if not isinstance(table["http_status"], int) or not 200 <= table["http_status"] < 300:
        raise CacheIntegrityError(f"Cached {role} HTTP status is not successful")
    if not isinstance(table["content_type"], str) or "html" not in table["content_type"]:
        raise CacheIntegrityError(f"Cached {role} Content-Type is invalid")
    if not isinstance(table["byte_size"], int) or table["byte_size"] < 1:
        raise CacheIntegrityError(f"Cached {role} byte size is invalid")
    if not isinstance(table["sha256"], str) or not re.fullmatch(
        r"[0-9a-f]{64}", table["sha256"]
    ):
        raise CacheIntegrityError(f"Cached {role} SHA-256 is invalid")
    return table


def _read_cached_table(bundle_dir: Path, table: dict[str, Any], *, role: str) -> bytes:
    expected_file = f"{role}.html"
    if table.get("file") != expected_file:
        raise CacheIntegrityError(f"Cache table filename is invalid for {role}")
    path = bundle_dir / expected_file
    try:
        size = path.stat().st_size
        if size > MAX_RESPONSE_BYTES:
            raise CacheIntegrityError(f"Cached {role} page exceeds the response bound")
        content = path.read_bytes()
    except CacheIntegrityError:
        raise
    except OSError as exc:
        raise CacheIntegrityError(f"Cannot read cached {role} page: {exc}") from exc
    if size != table.get("byte_size") or hashlib.sha256(content).hexdigest() != table.get(
        "sha256"
    ):
        raise CacheIntegrityError(f"Cached {role} page size or SHA-256 does not match manifest")
    return content


def _result_from_parsed(parsed, manifest: dict[str, Any], *, from_cache: bool) -> SectoralCreditResult:
    tables = manifest["tables"]
    freshness = manifest["freshness"]
    latest = _latest_observation_date(parsed.observations)
    metadata = SectoralCreditMetadata(
        dataset_id=DATASET_ID,
        source_route=SOURCE_ROUTE,
        bulletin_period=parsed.metadata.bulletin_period,
        bulletin_publication_date=parsed.metadata.publication_date,
        latest_observation_date=latest,
        retrieved_at_utc=manifest["retrieved_at_utc"],
        from_cache=from_cache,
        cache_bundle_id=manifest["cache_bundle_id"],
        bulletin_index_url=manifest["bulletin_index_url"],
        major_sectors_url=tables["major_sectors"]["discovered_url"],
        industries_url=tables["industries"]["discovered_url"],
        major_sectors_sha256=tables["major_sectors"]["sha256"],
        industries_sha256=tables["industries"]["sha256"],
        parser_version=parsed.metadata.parser_version,
        layout_id=parsed.metadata.layout_id,
        semantic_observations_sha256=parsed.metadata.semantic_observations_sha256,
        provenance_bound_output_sha256=parsed.metadata.provenance_bound_output_sha256,
        latest_dedicated_release_title=freshness.get("latest_dedicated_release_title"),
        latest_dedicated_release_date=freshness.get("latest_dedicated_release_date"),
        latest_dedicated_release_period=freshness.get("latest_dedicated_release_period"),
        freshness_gap_months=freshness.get("freshness_gap_months"),
        freshness_status=freshness.get("freshness_status", "UNKNOWN"),
        freshness_diagnostic=freshness.get("freshness_diagnostic"),
    )
    return SectoralCreditResult(
        observations=parsed.observations,
        metadata=metadata,
        notes=tuple(note.text for note in parsed.notes),
    )


def _load_bundle(bundle_dir: Path) -> SectoralCreditResult:
    manifest = _load_manifest(bundle_dir)
    tables = _require_mapping(manifest["tables"], context="Cache tables")
    if set(tables) != set(TABLE_TITLES):
        raise CacheIntegrityError("Cache manifest table roles are incomplete or unknown")
    major_table = _validate_table_manifest(tables["major_sectors"], role="major_sectors")
    industry_table = _validate_table_manifest(tables["industries"], role="industries")
    freshness = _require_mapping(manifest["freshness"], context="Cache freshness")
    if freshness.get("freshness_status") not in {
        "CURRENT_WITH_DEDICATED_RELEASE",
        "LAGGING_DEDICATED_RELEASE",
        "AHEAD_OF_DEDICATED_RELEASE",
        "UNKNOWN",
    }:
        raise CacheIntegrityError("Cache freshness status is invalid")
    try:
        expected_bundle_id = _bundle_identity(tables)
    except (KeyError, TypeError) as exc:
        raise CacheIntegrityError("Cache source identity is incomplete") from exc
    if expected_bundle_id != manifest["cache_bundle_id"]:
        raise CacheIntegrityError("Cache bundle identity does not match source identity")
    major = _read_cached_table(bundle_dir, major_table, role="major_sectors")
    industry = _read_cached_table(bundle_dir, industry_table, role="industries")
    try:
        parsed = parse_sectoral_credit_bulletin(
            major,
            industry,
            major_sectors_url=major_table["final_url"],
            industries_url=industry_table["final_url"],
        )
    except SectoralCreditParseError as exc:
        raise CacheIntegrityError(f"Cached source no longer passes parser validation: {exc}") from exc
    finally:
        del major, industry
    if (
        parsed.metadata.semantic_observations_sha256
        != manifest["semantic_observations_sha256"]
    ):
        raise CacheIntegrityError("Cached semantic observations SHA-256 differs from manifest")
    if (
        parsed.metadata.provenance_bound_output_sha256
        != manifest["provenance_bound_output_sha256"]
    ):
        raise CacheIntegrityError("Cached provenance-bound output SHA-256 differs from manifest")
    if (
        parsed.metadata.publication_date != manifest["bulletin_publication_date"]
        or parsed.metadata.bulletin_period != manifest["bulletin_period"]
        or _latest_observation_date(parsed.observations) != manifest["latest_observation_date"]
    ):
        raise CacheIntegrityError("Cached parsed dates differ from ingestion manifest")
    return _result_from_parsed(parsed, manifest, from_cache=True)


def _bundle_candidates(cache_root: Path) -> list[Path]:
    if not cache_root.exists():
        return []
    candidates = [
        path
        for publication in cache_root.iterdir()
        if publication.is_dir() and re.fullmatch(r"20\d{2}-\d{2}-\d{2}", publication.name)
        for path in publication.iterdir()
        if path.is_dir() and not path.name.startswith(".tmp-")
    ]
    return sorted(candidates, key=lambda path: (path.parent.name, path.name), reverse=True)


def _load_latest_cache(cache_root: Path) -> SectoralCreditResult:
    candidates = _bundle_candidates(cache_root)
    if not candidates:
        raise CacheNotFoundError(f"No verified sectoral-credit cache bundle exists under {cache_root}")
    latest_publication = candidates[0].parent.name
    same_publication = [
        candidate for candidate in candidates if candidate.parent.name == latest_publication
    ]
    ranked = [
        (datetime.fromisoformat(_load_manifest(candidate)["retrieved_at_utc"]), candidate)
        for candidate in same_publication
    ]
    return _load_bundle(max(ranked, key=lambda item: (item[0], item[1].name))[1])


def _build_live_manifest(
    *,
    parsed,
    retrieved_at_utc: str,
    tables: dict[str, dict[str, Any]],
    freshness: dict[str, Any],
) -> dict[str, Any]:
    bundle_id = _bundle_identity(tables)
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "dataset_id": DATASET_ID,
        "source_route": SOURCE_ROUTE,
        "cache_bundle_id": bundle_id,
        "bulletin_period": parsed.metadata.bulletin_period,
        "bulletin_publication_date": parsed.metadata.publication_date,
        "latest_observation_date": _latest_observation_date(parsed.observations),
        "retrieved_at_utc": retrieved_at_utc,
        "bulletin_index_url": BULLETIN_INDEX_URL,
        "tables": tables,
        "layout_id": parsed.metadata.layout_id,
        "parser_version": parsed.metadata.parser_version,
        "semantic_observations_sha256": parsed.metadata.semantic_observations_sha256,
        "provenance_bound_output_sha256": (
            parsed.metadata.provenance_bound_output_sha256
        ),
        "freshness": freshness,
    }


def _retrieve_live(cache_root: Path) -> SectoralCreditResult:
    retrieved_at = _utc_now()
    with _new_session() as session:
        index_page = _fetch_html(session, BULLETIN_INDEX_URL, context="Bulletin index")
        table_urls = _validate_bulletin_index(index_page)
        del index_page

        major_page = _fetch_html(
            session, table_urls["major_sectors"], context="Major-sectors Bulletin table"
        )
        _validate_table_page(major_page, role="major_sectors")
        industry_page = _fetch_html(
            session, table_urls["industries"], context="Industries Bulletin table"
        )
        _validate_table_page(industry_page, role="industries")

        parsed = parse_sectoral_credit_bulletin(
            major_page.content,
            industry_page.content,
            major_sectors_url=major_page.final_url,
            industries_url=industry_page.final_url,
        )
        latest = _latest_observation_date(parsed.observations)

        try:
            dedicated_page = _fetch_html(
                session, DEDICATED_INDEX_URL, context="Dedicated-release index"
            )
            dedicated_html = _decode_html(
                dedicated_page.content, context="Dedicated-release index"
            )
            release = _discover_latest_dedicated_release(
                dedicated_html, dedicated_page.final_url
            )
            freshness = _freshness_from_release(release, latest)
            del dedicated_page, dedicated_html
        except (SectoralCreditConnectorError, UnicodeError, ValueError) as exc:
            freshness = _unknown_freshness(exc)

    tables = {
        "major_sectors": _table_manifest(
            major_page, role="major_sectors", discovered_url=table_urls["major_sectors"]
        ),
        "industries": _table_manifest(
            industry_page, role="industries", discovered_url=table_urls["industries"]
        ),
    }
    manifest = _build_live_manifest(
        parsed=parsed,
        retrieved_at_utc=retrieved_at,
        tables=tables,
        freshness=freshness,
    )
    _commit_bundle(cache_root, manifest, major_page.content, industry_page.content)
    return _result_from_parsed(parsed, manifest, from_cache=False)


def sectoral_credit(
    *,
    refresh: bool = False,
    offline: bool = False,
    cache_dir: Path | str | None = None,
) -> SectoralCreditResult:
    """Return current RBI Bulletin sectoral-credit observations.

    Default calls prefer a verified cache. ``refresh=True`` forces the bounded
    live route, while ``offline=True`` guarantees that no network session is
    created.
    """
    if refresh and offline:
        raise ValueError("refresh=True cannot be combined with offline=True")
    cache_root = _dataset_cache_root(cache_dir)
    if offline:
        return _load_latest_cache(cache_root)
    if not refresh:
        try:
            return _load_latest_cache(cache_root)
        except (CacheNotFoundError, CacheIncompatibleError):
            pass
    return _retrieve_live(cache_root)


__all__ = [
    "CacheIncompatibleError",
    "CacheIntegrityError",
    "CacheNotFoundError",
    "SectoralCreditMetadata",
    "SectoralCreditResult",
    "SourceAccessBlockedError",
    "SourceDiscoveryError",
    "SourceUnavailableError",
    "SourceValidationError",
    "sectoral_credit",
]
