#!/usr/bin/env python3
"""Bounded historical layout census for RBI Bulletin sectoral-credit tables."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from indiamacro.rbi import sectoral_credit_bulletin as v1_parser


ARCHIVE_URL = "https://rbi.org.in/Scripts/BS_ViewBulletin.aspx"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
CHUNK_SIZE = 64 * 1024
MAX_REQUESTS = 120
MIN_REQUEST_INTERVAL_SECONDS = 0.75
USER_AGENT = "IndiaMacro/0.1.0 historical-layout-census (+https://rbi.org.in/)"
CHALLENGE_MARKERS = (
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


class CensusError(RuntimeError):
    """Base class for deterministic census failures."""


class RequestBudgetExceeded(CensusError):
    """The configured request budget would be exceeded."""


class SourceBlocked(CensusError):
    """RBI returned an access-control page."""


class SourceValidationError(CensusError):
    """An official response failed bounded validation."""


@dataclass(frozen=True)
class Link:
    text: str
    href: str
    attrs: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Form:
    action: str
    method: str
    inputs: tuple[tuple[str, str, str], ...]
    selects: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]


class ArchiveStructureParser(HTMLParser):
    """Collect public link and form structure without retaining a DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[Link] = []
        self.forms: list[Form] = []
        self.scripts: list[str] = []
        self._link_attrs: dict[str, str] | None = None
        self._link_text: list[str] = []
        self._form_action: str | None = None
        self._form_method = "get"
        self._form_inputs: list[tuple[str, str, str]] = []
        self._form_selects: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        self._select_name: str | None = None
        self._select_options: list[tuple[str, str]] = []
        self._option_value: str | None = None
        self._option_text: list[str] = []
        self._in_script = False
        self._script_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "a":
            self._link_attrs = values
            self._link_text = []
        elif tag == "form":
            self._form_action = values.get("action", "")
            self._form_method = values.get("method", "get").lower()
            self._form_inputs = []
            self._form_selects = []
        elif tag == "input" and self._form_action is not None:
            self._form_inputs.append(
                (values.get("name", ""), values.get("value", ""), values.get("type", "text"))
            )
        elif tag == "select" and self._form_action is not None:
            self._select_name = values.get("name", "")
            self._select_options = []
        elif tag == "option" and self._select_name is not None:
            self._option_value = values.get("value", "")
            self._option_text = []
        elif tag == "script":
            self._in_script = True
            self._script_text = []

    def handle_data(self, data: str) -> None:
        text = " ".join(unescape(data).split())
        if self._link_attrs is not None and text:
            self._link_text.append(text)
        if self._option_value is not None and text:
            self._option_text.append(text)
        if self._in_script:
            self._script_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._link_attrs is not None:
            attrs = tuple(sorted(self._link_attrs.items()))
            self.links.append(
                Link(" ".join(self._link_text), self._link_attrs.get("href", ""), attrs)
            )
            self._link_attrs = None
            self._link_text = []
        elif tag == "option" and self._option_value is not None:
            self._select_options.append((self._option_value, " ".join(self._option_text)))
            self._option_value = None
            self._option_text = []
        elif tag == "select" and self._select_name is not None:
            self._form_selects.append((self._select_name, tuple(self._select_options)))
            self._select_name = None
            self._select_options = []
        elif tag == "form" and self._form_action is not None:
            self.forms.append(
                Form(
                    self._form_action,
                    self._form_method,
                    tuple(self._form_inputs),
                    tuple(self._form_selects),
                )
            )
            self._form_action = None
        elif tag == "script" and self._in_script:
            script = "".join(self._script_text).strip()
            if script:
                self.scripts.append(script)
            self._in_script = False
            self._script_text = []


def _official_rbi_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        host == "rbi.org.in" or host.endswith(".rbi.org.in")
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CensusClient:
    """Sequential bounded HTTP client with an immutable on-disk response cache."""

    def __init__(self, artifact_dir: Path, *, max_requests: int = MAX_REQUESTS) -> None:
        self.artifact_dir = artifact_dir
        self.raw_dir = artifact_dir / "raw"
        self.log_path = artifact_dir / "request_log.json"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.max_requests = max_requests
        self.request_count = 0
        self.run_request_count = 0
        self.cache_hits = 0
        self.run_cache_hits = 0
        self._last_request_at: float | None = None
        self.entries: list[dict[str, Any]] = []
        if self.log_path.exists():
            existing = json.loads(self.log_path.read_text(encoding="utf-8"))
            self.entries = list(existing.get("entries", []))
            self.request_count = int(
                existing.get("live_request_attempts_total", len(self.entries))
            )
            self.cache_hits = int(existing.get("cache_hits_total", 0))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.2",
                "Accept-Language": "en-IN,en;q=0.9",
            }
        )

    def close(self) -> None:
        self.session.close()

    def _cache_key(self, url: str, method: str, data: dict[str, str] | None) -> str:
        payload = json.dumps(
            {"url": url, "method": method.upper(), "data": data or {}},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_cached(
        self, url: str, *, method: str = "GET", data: dict[str, str] | None = None
    ) -> bool:
        key = self._cache_key(url, method, data)
        return (self.raw_dir / f"{key}.html").is_file() and (
            self.raw_dir / f"{key}.json"
        ).is_file()

    def _write_log(self) -> None:
        payload = {
            "maximum_live_requests": self.max_requests,
            "live_request_attempts_total": self.request_count,
            "live_request_attempts_this_run": self.run_request_count,
            "cache_hits_total": self.cache_hits,
            "cache_hits_this_run": self.run_cache_hits,
            "entries": self.entries,
        }
        self.log_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        data: dict[str, str] | None = None,
        context: str,
    ) -> tuple[bytes, dict[str, Any]]:
        if not _official_rbi_url(url):
            raise SourceValidationError(f"Refusing non-RBI URL: {url}")
        key = self._cache_key(url, method, data)
        content_path = self.raw_dir / f"{key}.html"
        metadata_path = self.raw_dir / f"{key}.json"
        if content_path.is_file() and metadata_path.is_file():
            content = content_path.read_bytes()
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if hashlib.sha256(content).hexdigest() != metadata["sha256"]:
                raise SourceValidationError(f"Cached response hash mismatch for {context}")
            self.cache_hits += 1
            self.run_cache_hits += 1
            metadata.setdefault("cache_file", f"raw/{key}.html")
            self._write_log()
            return content, metadata
        if self.request_count >= self.max_requests:
            raise RequestBudgetExceeded(
                f"Live request budget {self.max_requests} exhausted before {context}"
            )
        if self._last_request_at is not None:
            wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)

        response: requests.Response | None = None
        error: Exception | None = None
        for attempt in range(1, 3):
            if self.request_count >= self.max_requests:
                raise RequestBudgetExceeded(
                    f"Live request budget {self.max_requests} exhausted during {context}"
                )
            self.request_count += 1
            self.run_request_count += 1
            self._write_log()
            self._last_request_at = time.monotonic()
            try:
                response = self.session.request(
                    method,
                    url,
                    data=data,
                    stream=True,
                    allow_redirects=True,
                    timeout=(10, 45),
                )
                if response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                    break
                response.close()
                response = None
                time.sleep(MIN_REQUEST_INTERVAL_SECONDS)
            except (requests.ConnectionError, requests.Timeout) as exc:
                error = exc
                if attempt == 2:
                    raise CensusError(f"{context} network failure: {exc}") from exc
                time.sleep(MIN_REQUEST_INTERVAL_SECONDS)
        if response is None:
            raise CensusError(f"{context} retrieval failed: {error}")

        try:
            for hop in [*response.history, response]:
                if not _official_rbi_url(hop.url):
                    raise SourceValidationError(
                        f"{context} redirected outside official RBI hosts: {hop.url}"
                    )
            status = int(response.status_code)
            body = bytearray()
            digest = hashlib.sha256()
            for chunk in response.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise SourceValidationError(
                        f"{context} exceeds {MAX_RESPONSE_BYTES} bytes"
                    )
                body.extend(chunk)
                digest.update(chunk)
            content = bytes(body)
            del body
            lowered = content.decode("utf-8", errors="ignore").casefold()
            blocked = status in {401, 403, 407, 451} or any(
                marker in lowered for marker in CHALLENGE_MARKERS
            )
            metadata = {
                "context": context,
                "requested_url": url,
                "method": method.upper(),
                "archive_form_parameters": {
                    key: value
                    for key, value in (data or {}).items()
                    if key in {"hdnYear", "hdnMonth"}
                },
                "http_status": status,
                "final_url": response.url,
                "redirect_history": [
                    {
                        "status": item.status_code,
                        "url": item.url,
                        "location": item.headers.get("Location"),
                    }
                    for item in response.history
                ],
                "content_type": response.headers.get("Content-Type", ""),
                "byte_size": len(content),
                "sha256": digest.hexdigest(),
                "access_control_markers_present": blocked,
                "retrieved_at_utc": _utc_now(),
                "cache_file": f"raw/{key}.html",
            }
            self.entries.append(metadata)
            self._write_log()
            if blocked:
                raise SourceBlocked(f"{context} returned an access-control page")
            if not 200 <= status < 300:
                raise CensusError(f"{context} returned HTTP {status}")
            if "html" not in metadata["content_type"].casefold():
                raise SourceValidationError(
                    f"{context} returned non-HTML content type {metadata['content_type']!r}"
                )
            content_path.write_bytes(content)
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return content, metadata
        finally:
            response.close()


def inspect_archive_structure(html: str, base_url: str = ARCHIVE_URL) -> dict[str, Any]:
    parser = ArchiveStructureParser()
    parser.feed(html)
    archive_links = []
    for link in parser.links:
        attrs = dict(link.attrs)
        combined = " ".join((link.text, link.href, attrs.get("onclick", "")))
        if re.search(r"archive|bulletin|month|year|record|202[0-6]", combined, re.I):
            archive_links.append(
                {
                    "text": link.text,
                    "href": urljoin(base_url, link.href) if link.href else "",
                    "onclick": attrs.get("onclick", ""),
                }
            )
    script_fragments = [
        line.strip()
        for script in parser.scripts
        for line in script.splitlines()
        if re.search(r"archive|bulletin|month|year|record|__doPostBack", line, re.I)
    ]
    return {
        "forms": [
            {
                "action": urljoin(base_url, form.action),
                "method": form.method,
                "inputs": list(form.inputs),
                "selects": [
                    {"name": name, "options": list(options)}
                    for name, options in form.selects
                ],
            }
            for form in parser.forms
        ],
        "archive_links": archive_links,
        "script_fragments": script_fragments,
    }


MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
ANCHOR_PERIODS = (
    "2026-01",
    "2025-06",
    "2024-06",
    "2023-06",
    "2022-06",
    "2020-06",
)
MAJOR_TITLE_RE = re.compile(
    r"^(?:(?P<number>\d+[A-Za-z]?)\.\s*)?"
    r"Deployment of (?:Gross )?Bank Credit by Major Sectors$",
    re.IGNORECASE,
)
INDUSTRY_TITLE_RE = re.compile(
    r"^(?:(?P<number>\d+[A-Za-z]?)\.\s*)?"
    r"Industry[-\s]wise Deployment of (?:Gross )?Bank Credit$",
    re.IGNORECASE,
)
PUBLICATION_DATE_RE = re.compile(
    r"\b(?:Date\s*:\s*)?([A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+20\d{2})\b"
)
PERIOD_RE = re.compile(
    r"Reserve Bank of India Bulletin\s*[-\u2013\u2014]\s*"
    r"(" + "|".join(MONTH_NAMES) + r")\s+(20\d{2})",
    re.IGNORECASE,
)


class CandidateError(CensusError):
    """Candidate issue or table links cannot be resolved safely."""


class CandidateAmbiguous(CandidateError):
    """More than one credible candidate was found."""


@dataclass(frozen=True)
class TableCandidate:
    role: str
    title: str
    number: str | None
    url: str


def _normalize(value: str) -> str:
    return " ".join(unescape(value).replace("\xa0", " ").split())


def _period(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _period_parts(period: str) -> tuple[int, int]:
    match = re.fullmatch(r"(20\d{2})-(0[1-9]|1[0-2])", period)
    if not match:
        raise ValueError(f"Invalid period {period!r}")
    return int(match.group(1)), int(match.group(2))


def _shift_period(period: str, months: int) -> str:
    year, month = _period_parts(period)
    offset = year * 12 + month - 1 + months
    return _period(offset // 12, offset % 12 + 1)


def _extract_form_payload(html: str, *, year: int, month: int) -> dict[str, str]:
    parser = ArchiveStructureParser()
    parser.feed(html)
    post_forms = [form for form in parser.forms if form.method == "post"]
    if len(post_forms) != 1:
        raise CandidateError(f"Expected one archive POST form; found {len(post_forms)}")
    form = post_forms[0]
    payload = {
        name: value
        for name, value, input_type in form.inputs
        if name and input_type.casefold() in {"hidden", "submit"}
    }
    required = {"__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"}
    missing = sorted(required - payload.keys())
    if missing:
        raise CandidateError(f"Archive form is missing required state fields: {missing}")
    payload.update(
        {
            "hdnYear": str(year),
            "hdnMonth": str(month),
            "ddlSubSection": "0",
            "UsrFontCntr$btn": "",
        }
    )
    payload.pop("btnGo", None)
    return payload


def _archive_periods(html: str) -> set[str]:
    return {
        _period(int(year), int(month))
        for year, month in re.findall(
            r"GetYearMonth\([\"'](20\d{2})[\"'],[\"']([1-9]|1[0-2])[\"']\)",
            html,
        )
    }


def _published_period(html: str) -> str | None:
    match = PERIOD_RE.search(_normalize(html))
    if not match:
        return None
    month = next(
        index for index, name in enumerate(MONTH_NAMES, start=1) if name.casefold() == match[1].casefold()
    )
    return _period(int(match[2]), month)


def _publication_date(html: str) -> str | None:
    for raw in PUBLICATION_DATE_RE.findall(_normalize(html)):
        cleaned = raw.replace(".", "")
        for pattern in ("%b %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(cleaned, pattern).date().isoformat()
            except ValueError:
                pass
    return None


def _parse_header_date(value: str) -> str | None:
    cleaned = value.replace(".", "")
    cleaned = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", cleaned)
    cleaned = re.sub(r",\s*", ", ", cleaned)
    for pattern in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(cleaned, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def discover_table_candidates(
    html: str, base_url: str = ARCHIVE_URL
) -> dict[str, TableCandidate | None]:
    parser = ArchiveStructureParser()
    parser.feed(html)
    matches: dict[str, list[TableCandidate]] = {"major": [], "industry": []}
    for link in parser.links:
        title = _normalize(link.text)
        for role, pattern in (("major", MAJOR_TITLE_RE), ("industry", INDUSTRY_TITLE_RE)):
            match = pattern.fullmatch(title)
            if not match:
                continue
            url = urljoin(base_url, link.href)
            parsed = urlparse(url)
            if not _official_rbi_url(url):
                raise SourceValidationError(f"Candidate {title!r} is outside RBI: {url}")
            if parsed.path.casefold() != "/scripts/bs_viewbulletin.aspx" or not parsed.query:
                continue
            matches[role].append(
                TableCandidate(role, title, match.group("number"), url)
            )
    result: dict[str, TableCandidate | None] = {}
    for role, candidates in matches.items():
        unique = {(item.title, item.url): item for item in candidates}
        if len(unique) > 1:
            raise CandidateAmbiguous(
                f"Multiple {role} table candidates: {sorted(unique)}"
            )
        result[role] = next(iter(unique.values()), None)
    return result


def build_sample_schedule(latest_period: str) -> list[dict[str, str]]:
    """Build the bounded plan before fetching any sampled table pages."""
    latest_year, latest_month = _period_parts(latest_period)
    recent = [_shift_period(latest_period, -offset) for offset in range(11, -1, -1)]
    recent_start = recent[0]

    intermediate_start = _shift_period(recent_start, -24)
    intermediate_end = _shift_period(recent_start, -1)
    quarterly: list[str] = []
    cursor = intermediate_start
    while cursor <= intermediate_end:
        _, month = _period_parts(cursor)
        if month in {3, 6, 9, 12}:
            quarterly.append(cursor)
        cursor = _shift_period(cursor, 1)

    intermediate_start_year, _ = _period_parts(intermediate_start)
    annual_end_year = intermediate_start_year if intermediate_start > _period(intermediate_start_year, 3) else intermediate_start_year - 1
    annual = [_period(year, 3) for year in range(annual_end_year, 2009, -1)]

    categories: dict[str, str] = {}
    for period in ANCHOR_PERIODS:
        categories[period] = "anchor"
    for period in recent:
        categories[period] = "recent_monthly"
    for period in quarterly:
        categories.setdefault(period, "intermediate_quarterly")
    for period in annual:
        categories.setdefault(period, "older_annual")
    if len(categories) > 40:
        raise ValueError(f"Schedule exceeds 40 issues: {len(categories)}")
    if latest_year < 2026 or latest_month < 1:
        raise ValueError(f"Unexpectedly old latest Bulletin period: {latest_period}")
    return [
        {"period": period, "category": categories[period]}
        for period in sorted(categories)
    ]


def boundary_expansion_periods(
    issues: list[dict[str, Any]], scheduled_periods: set[str], *, maximum: int = 3
) -> list[str]:
    present = sorted(
        item["requested_bulletin_period"]
        for item in issues
        if item.get("major_table") is not None and item.get("industry_table") is not None
    )
    missing = {
        item["requested_bulletin_period"]
        for item in issues
        if item.get("v1_result") == "MISSING_TABLE_PAIR"
    }
    if not present or not any(period < present[0] for period in missing):
        return []
    candidates = [_shift_period(present[0], offset) for offset in range(-maximum, 0)]
    return [period for period in candidates if period not in scheduled_periods]


def _semantic_text(value: str) -> str:
    value = _normalize(value).casefold().replace("\u2013", "-").replace("\u2014", "-")
    value = re.sub(
        r"(?:₹|`|rs\.?)\s*(?=(?:crore|billion|million))", "inr ", value
    )
    value = re.sub(r"growth\s*\(\s*%\s*\)", "growth (%)", value)
    value = re.sub(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
        r"\d{1,2},?\s+20\d{2}\b",
        "<date>",
        value,
    )
    value = re.sub(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
        r"\d{1,2}\b",
        "<month-day>",
        value,
    )
    value = re.sub(r"\b20\d{2}-\d{2}\b", "<fiscal-year>", value)
    value = re.sub(r"\b20\d{2}\b", "<year>", value)
    return value


def _normalized_title(value: str) -> str:
    return re.sub(r"^\d+[a-z]?\.\s*", "", _semantic_text(value))


def _split_row_label(value: str) -> tuple[str, str]:
    text = _normalize(value)
    match = re.match(
        r"^(?P<code>(?:[IVX]+|\d+(?:\.\d+)*|\([ivx]+\)))\.?(?:\s+|$)(?P<label>.*)$",
        text,
        re.IGNORECASE,
    )
    if not match:
        return "", text
    return match.group("code"), match.group("label")


def _parent_code(code: str) -> str | None:
    if "." in code and code[0].isdigit():
        return code.rsplit(".", 1)[0]
    return None


def _cells(table: Any) -> list[str]:
    return [cell.text for row in table.rows for cell in row.cells]


def extract_table_inventory(html: str, *, published_title: str) -> dict[str, Any]:
    parser = v1_parser._SemanticTableParser()  # noqa: SLF001
    parser.feed(html)
    title_tables = [
        table
        for table in parser.tables
        if published_title in {_normalize(text) for text in _cells(table)}
    ]
    if len(title_tables) != 1:
        raise CandidateError(
            f"Expected one table containing title {published_title!r}; found {len(title_tables)}"
        )
    title_table = title_tables[0]
    descendants = list(v1_parser._descendants(title_table))  # noqa: SLF001
    candidates = [
        table
        for table in descendants
        if len(table.rows) >= 4
        and any(
            marker in _semantic_text(text)
            for text in _cells(table)
            for marker in ("outstanding as on", "amount outstanding", "growth (%)")
        )
    ]
    if not candidates:
        candidates = [table for table in descendants if len(table.rows) >= 6]
    if not candidates:
        raise CandidateError(f"No data table found below {published_title!r}")
    data_table = max(candidates, key=lambda item: (len(item.rows), len(_cells(item))))

    publication_date = None
    for text in _cells(title_table):
        if _normalize(text).casefold().startswith("date"):
            publication_date = _publication_date(text)
            if publication_date:
                break

    unit = next(
        (
            _normalize(text)
            for text in _cells(data_table)
            if re.search(
                r"(?:₹|`|rs\.?|rupees?).*(?:crore|billion|million)", text, re.I
            )
        ),
        "",
    )
    economic_start: int | None = None
    for index, row in enumerate(data_table.rows):
        if (
            row.cells
            and all(_split_row_label(row.cells[0].text))
            and len(row.cells) >= 2
        ):
            economic_start = index
            break
    if economic_start is None:
        raise CandidateError(f"No coded economic rows found in {published_title!r}")

    raw_header_grid = [
        [
            {
                "text": _normalize(cell.text),
                "rowspan": cell.rowspan,
                "colspan": cell.colspan,
            }
            for cell in row.cells
        ]
        for row in data_table.rows[:economic_start]
    ]
    header_grid = [
        [
            {
                "text": _semantic_text(cell.text),
                "rowspan": cell.rowspan,
                "colspan": cell.colspan,
            }
            for cell in row.cells
        ]
        for row in data_table.rows[:economic_start]
    ]
    full_header_dates = [
        parsed
        for row in raw_header_grid
        for cell in row
        if (parsed := _parse_header_date(cell["text"])) is not None
    ]
    observation_date = full_header_dates[0] if full_header_dates else None
    prior_year_comparison_date = None
    if len(raw_header_grid) >= 4:
        year_values = [
            cell["text"]
            for cell in raw_header_grid[2]
            if re.fullmatch(r"20\d{2}", cell["text"])
        ]
        month_days = [
            cell["text"]
            for cell in raw_header_grid[3]
            if re.fullmatch(r"[A-Z][a-z]{2,8}\.?\s*\d{1,2}", cell["text"])
        ]
        if year_values and month_days:
            prior_year_comparison_date = _parse_header_date(
                f"{month_days[0]}, {year_values[0]}"
            )
            observation_date = _parse_header_date(
                f"{month_days[-1]}, {year_values[-1]}"
            )
    rows: list[dict[str, Any]] = []
    note_texts: list[str] = []
    in_notes = False
    for row in data_table.rows[economic_start:]:
        texts = [_normalize(cell.text) for cell in row.cells]
        if not texts:
            continue
        if texts[0].casefold().startswith(("note:", "notes:", "source:")):
            in_notes = True
        if in_notes or len(texts) == 1:
            note_texts.extend(text for text in texts if text)
            continue
        code, label = _split_row_label(texts[0])
        if not code or not label:
            note_texts.extend(text for text in texts if text)
            continue
        rows.append(
            {
                "code": code,
                "label": texts[0],
                "label_without_code": label,
                "parent_code": _parent_code(code),
                "is_memorandum": "memo" in texts[0].casefold(),
            }
        )
    notes = " ".join(note_texts)
    population_markers = [
        marker
        for marker in (
            "section-42 return",
            "all scheduled commercial banks",
            "select banks",
            "95 per cent",
            "sector-wise and industry-wise bank credit",
        )
        if marker in notes.casefold()
    ]
    measure_columns = [
        marker
        for marker in ("outstanding as on", "amount outstanding", "growth (%)", "variation")
        if any(marker in cell["text"] for row in header_grid for cell in row)
    ]
    reporting_structure = {
        "contains_explicit_date": bool(PUBLICATION_DATE_RE.search(notes)),
        "contains_financial_year": "financial year" in notes.casefold()
        or any("financial year" in cell["text"] for row in header_grid for cell in row),
        "contains_year_on_year": any(
            "y-o-y" in cell["text"] or "year-on-year" in cell["text"]
            for row in header_grid
            for cell in row
        ),
        "last_friday": "last friday" in notes.casefold(),
        "last_day_of_month": "last day of the month" in notes.casefold(),
        "reporting_fortnight": "fortnight" in notes.casefold(),
        "section42_reference_date": "reference date for section-42" in notes.casefold(),
    }
    bank_counts = sorted(
        {int(value) for value in re.findall(r"\b(\d{1,3})\s+(?:select\s+)?banks\b", notes, re.I)}
    )
    coverage_percentages = sorted(
        {
            value
            for value in re.findall(
                r"\b(\d+(?:\.\d+)?)\s+per cent\b", notes, re.IGNORECASE
            )
        }
    )
    semantic = {
        "title": _normalized_title(published_title),
        "unit": _semantic_text(unit),
        "header_depth": len(header_grid),
        "header_grid": header_grid,
        "rows": [
            {
                "code": item["code"],
                "label": _semantic_text(item["label"]),
                "parent_code": item["parent_code"],
                "is_memorandum": item["is_memorandum"],
            }
            for item in rows
        ],
        "population_markers": population_markers,
        "bank_counts": bank_counts,
        "coverage_percentages": coverage_percentages,
        "reporting_structure": reporting_structure,
        "measure_columns": measure_columns,
        "memorandum_rows": [item["code"] for item in rows if item["is_memorandum"]],
    }
    signature_payload = json.dumps(
        semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    structural = {
        "unit": semantic["unit"],
        "header_grid": semantic["header_grid"],
        "rows": semantic["rows"],
        "measure_columns": semantic["measure_columns"],
    }
    structural_payload = json.dumps(
        structural, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "title": published_title,
        "publication_date": publication_date,
        "unit": unit,
        "raw_header_grid": raw_header_grid,
        "normalized_header_grid": header_grid,
        "header_depth": len(header_grid),
        "rows": rows,
        "row_count": len(rows),
        "row_codes": [item["code"] for item in rows],
        "source_labels": [item["label"] for item in rows],
        "note_text": notes,
        "population_markers": population_markers,
        "bank_counts": bank_counts,
        "coverage_percentages": coverage_percentages,
        "reporting_date_structure": reporting_structure,
        "observation_date": observation_date,
        "prior_year_comparison_date": prior_year_comparison_date,
        "measure_columns": measure_columns,
        "memorandum_items": semantic["memorandum_rows"],
        "semantic_layout_signature": hashlib.sha256(signature_payload).hexdigest(),
        "structural_signature": hashlib.sha256(structural_payload).hexdigest(),
    }


def taxonomy_delta(
    historical_rows: list[dict[str, Any]], current_rows: list[dict[str, Any]]
) -> dict[str, list[dict[str, str]]]:
    historical_by_code = {item["code"]: item for item in historical_rows}
    current_by_code = {item["code"]: item for item in current_rows}
    current_label_groups: dict[str, list[dict[str, Any]]] = {}
    for item in current_rows:
        current_label_groups.setdefault(
            _semantic_text(item["label_without_code"]), []
        ).append(item)
    output: dict[str, list[dict[str, str]]] = {
        "exact": [],
        "renamed": [],
        "code_changes": [],
        "added_in_v1": [],
        "removed_from_v1": [],
        "hierarchy_changes": [],
    }
    matched_current: set[str] = set()
    for code, item in historical_by_code.items():
        current = current_by_code.get(code)
        if current:
            matched_current.add(code)
            same = _semantic_text(item["label_without_code"]) == _semantic_text(
                current["label_without_code"]
            )
            similarity = SequenceMatcher(
                None,
                _semantic_text(item["label_without_code"]),
                _semantic_text(current["label_without_code"]),
            ).ratio()
            continuity = (
                "EXACT_CONTINUITY"
                if same
                else (
                    "LIKELY_CONTINUITY_REQUIRES_REVIEW"
                    if similarity >= 0.65
                    else "DEFINITION_CHANGED"
                )
            )
            output["exact" if same else "renamed"].append(
                {
                    "historical_code": code,
                    "historical_label": item["label"],
                    "v1_code": code,
                    "v1_label": current["label"],
                    "continuity": continuity,
                }
            )
            if item.get("parent_code") != current.get("parent_code"):
                output["hierarchy_changes"].append(
                    {
                        "historical_code": code,
                        "historical_parent": str(item.get("parent_code")),
                        "v1_parent": str(current.get("parent_code")),
                        "continuity": "DEFINITION_CHANGED",
                    }
                )
            continue
        label_matches = current_label_groups.get(
            _semantic_text(item["label_without_code"]), []
        )
        if len(label_matches) == 1:
            label_match = label_matches[0]
            matched_current.add(label_match["code"])
            output["code_changes"].append(
                {
                    "historical_code": code,
                    "historical_label": item["label"],
                    "v1_code": label_match["code"],
                    "v1_label": label_match["label"],
                    "continuity": "LIKELY_CONTINUITY_REQUIRES_REVIEW",
                }
            )
        else:
            output["removed_from_v1"].append(
                {
                    "historical_code": code,
                    "historical_label": item["label"],
                    "continuity": (
                        "UNRESOLVED" if len(label_matches) > 1 else "DISCONTINUED_SERIES"
                    ),
                }
            )
    for code, item in current_by_code.items():
        if code not in matched_current:
            output["added_in_v1"].append(
                {
                    "v1_code": code,
                    "v1_label": item["label"],
                    "continuity": "NEW_SERIES",
                }
            )
    return output


def classify_v1_result(
    *,
    parser_succeeded: bool,
    major_inventory: dict[str, Any],
    industry_inventory: dict[str, Any],
    v1_major_inventory: dict[str, Any],
    v1_industry_inventory: dict[str, Any],
) -> str:
    if parser_succeeded:
        return "V1_PARSE_PASS"
    if (
        major_inventory["structural_signature"]
        == v1_major_inventory["structural_signature"]
        and industry_inventory["structural_signature"]
        == v1_industry_inventory["structural_signature"]
    ):
        return "V1_SIGNATURE_MATCH_PARSE_FAIL"
    return "NEW_LAYOUT"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _v1_reference(repo_root: Path) -> dict[str, dict[str, Any]]:
    fixture_dir = repo_root / "tests" / "fixtures"
    major_path = fixture_dir / "rbi_sectoral_credit_major_v1.html"
    industry_path = fixture_dir / "rbi_sectoral_credit_industries_v1.html"
    if not major_path.is_file() or not industry_path.is_file():
        raise CensusError("The compact v1 reference fixtures are unavailable")
    return {
        "major": extract_table_inventory(
            major_path.read_text(encoding="utf-8"), published_title=v1_parser.MAJOR_TITLE
        ),
        "industry": extract_table_inventory(
            industry_path.read_text(encoding="utf-8"),
            published_title=v1_parser.INDUSTRY_TITLE,
        ),
    }


def _select_archive_issue(
    client: CensusClient, archive_html: str, period: str
) -> tuple[bytes, dict[str, Any]]:
    year, month = _period_parts(period)
    payload = _extract_form_payload(archive_html, year=year, month=month)
    return client.fetch(
        ARCHIVE_URL,
        method="POST",
        data=payload,
        context=f"Bulletin archive selection {period}",
    )


def _estimated_new_requests_for_issue(
    client: CensusClient, archive_html: str, period: str
) -> int:
    year, month = _period_parts(period)
    payload = _extract_form_payload(archive_html, year=year, month=month)
    if not client.is_cached(ARCHIVE_URL, method="POST", data=payload):
        return 3
    key = client._cache_key(ARCHIVE_URL, "POST", payload)
    issue_path = client.raw_dir / f"{key}.html"
    metadata_path = client.raw_dir / f"{key}.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    candidates = discover_table_candidates(
        issue_path.read_text(encoding="utf-8", errors="replace"), metadata["final_url"]
    )
    return sum(
        candidate is not None and not client.is_cached(candidate.url)
        for candidate in candidates.values()
    )


def _base_issue_result(
    period: str,
    category: str,
    metadata: dict[str, Any],
    html: str,
) -> dict[str, Any]:
    return {
        "requested_bulletin_period": period,
        "sample_category": category,
        "discovered_bulletin_period": _published_period(html),
        "publication_date": _publication_date(html),
        "discovery": {
            "url": ARCHIVE_URL,
            "method": "POST",
            "form_parameters": {"hdnYear": period[:4], "hdnMonth": str(int(period[5:]))},
        },
        "issue_page": metadata,
        "major_table": None,
        "industry_table": None,
        "layout_signature": None,
        "v1_result": None,
        "parser_exception": None,
        "observation_date": None,
        "notes": [],
    }


def _table_evidence(
    candidate: TableCandidate,
    metadata: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "published_number": candidate.number,
        "url": candidate.url,
        "http": metadata,
        "raw_sha256": metadata["sha256"],
        "byte_size": metadata["byte_size"],
        "publication_date": inventory["publication_date"],
        "unit": inventory["unit"],
        "raw_header_grid": inventory["raw_header_grid"],
        "normalized_header_grid": inventory["normalized_header_grid"],
        "header_depth": inventory["header_depth"],
        "row_codes": inventory["row_codes"],
        "source_labels": inventory["source_labels"],
        "note_text": inventory["note_text"],
        "population_markers": inventory["population_markers"],
        "bank_counts": inventory["bank_counts"],
        "coverage_percentages": inventory["coverage_percentages"],
        "reporting_date_structure": inventory["reporting_date_structure"],
        "observation_date": inventory["observation_date"],
        "prior_year_comparison_date": inventory["prior_year_comparison_date"],
        "measure_columns": inventory["measure_columns"],
        "memorandum_items": inventory["memorandum_items"],
        "semantic_layout_signature": inventory["semantic_layout_signature"],
        "structural_signature": inventory["structural_signature"],
        "rows": inventory["rows"],
    }


def process_issue(
    client: CensusClient,
    *,
    archive_html: str,
    period: str,
    category: str,
    v1_reference: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    issue_content, issue_metadata = _select_archive_issue(client, archive_html, period)
    issue_html = issue_content.decode("utf-8", errors="replace")
    result = _base_issue_result(period, category, issue_metadata, issue_html)
    if result["discovered_bulletin_period"] != period:
        result["v1_result"] = "RETRIEVAL_FAILED"
        result["notes"].append(
            "Archive selection returned "
            f"{result['discovered_bulletin_period']!r} instead of {period!r}."
        )
        return result

    try:
        candidates = discover_table_candidates(issue_html, issue_metadata["final_url"])
    except CandidateAmbiguous as exc:
        result["v1_result"] = "DISCOVERY_AMBIGUOUS"
        result["notes"].append(str(exc))
        return result
    major_candidate = candidates["major"]
    industry_candidate = candidates["industry"]
    if major_candidate is None and industry_candidate is None:
        result["v1_result"] = "MISSING_TABLE_PAIR"
        return result
    if major_candidate is None:
        result["v1_result"] = "MISSING_MAJOR_TABLE"
        return result
    if industry_candidate is None:
        result["v1_result"] = "MISSING_INDUSTRY_TABLE"
        return result

    major_content: bytes | None = None
    industry_content: bytes | None = None
    try:
        major_content, major_metadata = client.fetch(
            major_candidate.url, context=f"{period} major-sector table"
        )
        major_html = major_content.decode("utf-8", errors="replace")
        major_inventory = extract_table_inventory(
            major_html, published_title=major_candidate.title
        )
        result["major_table"] = _table_evidence(
            major_candidate, major_metadata, major_inventory
        )
        del major_html

        industry_content, industry_metadata = client.fetch(
            industry_candidate.url, context=f"{period} industry table"
        )
        industry_html = industry_content.decode("utf-8", errors="replace")
        industry_inventory = extract_table_inventory(
            industry_html, published_title=industry_candidate.title
        )
        result["industry_table"] = _table_evidence(
            industry_candidate, industry_metadata, industry_inventory
        )
        del industry_html

        parser_succeeded = False
        try:
            parsed = v1_parser.parse_sectoral_credit_bulletin(
                major_content,
                industry_content,
                major_sectors_url=major_candidate.url,
                industries_url=industry_candidate.url,
            )
            parser_succeeded = True
            result["observation_date"] = parsed.metadata.current_observation_date
            del parsed
        except Exception as exc:  # exact parser exception is census evidence
            result["parser_exception"] = f"{type(exc).__name__}: {exc}"
        result["v1_result"] = classify_v1_result(
            parser_succeeded=parser_succeeded,
            major_inventory=major_inventory,
            industry_inventory=industry_inventory,
            v1_major_inventory=v1_reference["major"],
            v1_industry_inventory=v1_reference["industry"],
        )
        if result["observation_date"] is None:
            result["observation_date"] = major_inventory["observation_date"]
        pair_payload = (
            major_inventory["semantic_layout_signature"]
            + ":"
            + industry_inventory["semantic_layout_signature"]
        ).encode("ascii")
        result["layout_signature"] = hashlib.sha256(pair_payload).hexdigest()
        if not result["publication_date"]:
            result["publication_date"] = major_inventory["publication_date"]
        return result
    except SourceBlocked:
        result["v1_result"] = "SOURCE_BLOCKED"
        raise
    except (CensusError, UnicodeError, ValueError) as exc:
        result["v1_result"] = "RETRIEVAL_FAILED"
        result["notes"].append(f"{type(exc).__name__}: {exc}")
        return result
    finally:
        del major_content, industry_content


def _layout_groups(
    issues: list[dict[str, Any]], v1_reference: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        signature = issue.get("layout_signature")
        if signature:
            grouped.setdefault(signature, []).append(issue)
    layouts: list[dict[str, Any]] = []
    for signature, samples in sorted(grouped.items()):
        samples.sort(key=lambda item: item["requested_bulletin_period"])
        first = samples[0]
        major = first["major_table"]
        industry = first["industry_table"]
        layouts.append(
            {
                "layout_signature": signature,
                "first_sampled_occurrence": first["requested_bulletin_period"],
                "last_sampled_occurrence": samples[-1]["requested_bulletin_period"],
                "sample_count": len(samples),
                "sampled_periods": [
                    item["requested_bulletin_period"] for item in samples
                ],
                "titles": sorted(
                    {
                        item[role]["title"]
                        for item in samples
                        for role in ("major_table", "industry_table")
                    }
                ),
                "header_structure": {
                    "major": major["normalized_header_grid"],
                    "industry": industry["normalized_header_grid"],
                },
                "row_counts": {
                    "major": len(major["rows"]),
                    "industry": len(industry["rows"]),
                },
                "units": {
                    "major": major["unit"],
                    "industry": industry["unit"],
                },
                "row_label_inventory": {
                    "major": major["source_labels"],
                    "industry": industry["source_labels"],
                },
                "population_wording": {
                    "major": major["population_markers"],
                    "industry": industry["population_markers"],
                },
                "bank_counts": {
                    "major": major["bank_counts"],
                    "industry": industry["bank_counts"],
                },
                "coverage_percentages": {
                    "major": major["coverage_percentages"],
                    "industry": industry["coverage_percentages"],
                },
                "reporting_date_convention": {
                    "major": major["reporting_date_structure"],
                    "industry": industry["reporting_date_structure"],
                },
                "v1_results": sorted({item["v1_result"] for item in samples}),
                "likely_new_parser_version": any(
                    item["v1_result"] == "NEW_LAYOUT" for item in samples
                ),
                "taxonomy_delta": {
                    "major": taxonomy_delta(major["rows"], v1_reference["major"]["rows"]),
                    "industry": taxonomy_delta(
                        industry["rows"], v1_reference["industry"]["rows"]
                    ),
                },
            }
        )
    return layouts


def _write_summary_csv(path: Path, issues: list[dict[str, Any]]) -> None:
    output = io.StringIO(newline="")
    fields = (
        "bulletin_period",
        "publication_date",
        "major_table_title",
        "industry_table_title",
        "layout_signature",
        "v1_result",
        "observation_date",
        "notes",
    )
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for item in sorted(issues, key=lambda value: value["requested_bulletin_period"]):
        writer.writerow(
            {
                "bulletin_period": item["requested_bulletin_period"],
                "publication_date": item["publication_date"] or "",
                "major_table_title": (item["major_table"] or {}).get("title", ""),
                "industry_table_title": (item["industry_table"] or {}).get("title", ""),
                "layout_signature": item["layout_signature"] or "",
                "v1_result": item["v1_result"],
                "observation_date": item["observation_date"] or "",
                "notes": " | ".join(item["notes"]),
            }
        )
    path.write_text(output.getvalue(), encoding="utf-8")


def _persist_census(
    artifact_dir: Path,
    *,
    schedule: list[dict[str, str]],
    issues: list[dict[str, Any]],
    v1_reference: dict[str, dict[str, Any]],
    client: CensusClient,
) -> None:
    layouts = _layout_groups(issues, v1_reference)
    _write_json(
        artifact_dir / "census.json",
        {
            "generated_at_utc": _utc_now(),
            "issues_planned": len(schedule),
            "issues_inspected": len(issues),
            "live_request_attempts_total": client.request_count,
            "cache_hits_total": client.cache_hits,
            "issues": issues,
        },
    )
    _write_json(artifact_dir / "layout_inventory.json", {"layouts": layouts})
    _write_summary_csv(artifact_dir / "census_summary.csv", issues)


def run_census(artifact_dir: Path, *, repo_root: Path) -> dict[str, Any]:
    client = CensusClient(artifact_dir)
    try:
        archive_content, archive_metadata = client.fetch(
            ARCHIVE_URL, context="Bulletin archive index"
        )
        archive_html = archive_content.decode("utf-8", errors="replace")
        exposed_periods = _archive_periods(archive_html)
        missing_anchors = sorted(set(ANCHOR_PERIODS) - exposed_periods)
        if missing_anchors:
            raise CandidateError(f"Archive selectors omit anchors: {missing_anchors}")
        latest_period = _published_period(archive_html)
        if latest_period is None:
            raise CandidateError("Current Bulletin period is not discoverable")
        v1_reference = _v1_reference(repo_root)

        anchor_results: dict[str, dict[str, Any]] = {}
        for period in ANCHOR_PERIODS:
            content, metadata = _select_archive_issue(client, archive_html, period)
            html = content.decode("utf-8", errors="replace")
            candidates = discover_table_candidates(html, metadata["final_url"])
            anchor_results[period] = {
                "discovered_bulletin_period": _published_period(html),
                "publication_date": _publication_date(html),
                "major": (
                    None
                    if candidates["major"] is None
                    else candidates["major"].__dict__
                ),
                "industry": (
                    None
                    if candidates["industry"] is None
                    else candidates["industry"].__dict__
                ),
            }
            if anchor_results[period]["discovered_bulletin_period"] != period:
                raise CandidateError(f"Anchor {period} did not resolve to its requested issue")
            if candidates["major"] is None or candidates["industry"] is None:
                raise CandidateError(f"Anchor {period} did not expose both target tables")
        _write_json(artifact_dir / "anchor_discovery.json", anchor_results)

        schedule = build_sample_schedule(latest_period)
        plan = {
            "recorded_before_full_sample_at_utc": _utc_now(),
            "latest_archive_period": latest_period,
            "initial_issues_planned": len(schedule),
            "maximum_issues": 40,
            "maximum_live_requests": MAX_REQUESTS,
            "initial_schedule": list(schedule),
            "boundary_expansion": [],
            "schedule": list(schedule),
        }
        _write_json(artifact_dir / "sample_schedule.json", plan)
        issues: list[dict[str, Any]] = []
        for item in schedule:
            issue = process_issue(
                client,
                archive_html=archive_html,
                period=item["period"],
                category=item["category"],
                v1_reference=v1_reference,
            )
            issues.append(issue)
            _persist_census(
                artifact_dir,
                schedule=schedule,
                issues=issues,
                v1_reference=v1_reference,
                client=client,
            )
        expansion_periods = boundary_expansion_periods(
            issues, {item["period"] for item in schedule}
        )
        expansion_periods = [
            period for period in expansion_periods if period in exposed_periods
        ][: max(0, 40 - len(schedule))]
        bounded_expansion: list[str] = []
        estimated_total = client.request_count
        for period in expansion_periods:
            estimated_total += _estimated_new_requests_for_issue(
                client, archive_html, period
            )
            if estimated_total > MAX_REQUESTS:
                break
            bounded_expansion.append(period)
        expansion_periods = bounded_expansion
        expansion = [
            {"period": period, "category": "boundary_expansion"}
            for period in expansion_periods
        ]
        schedule.extend(expansion)
        plan["boundary_expansion"] = expansion
        plan["schedule"] = schedule
        plan["issues_planned"] = len(schedule)
        _write_json(artifact_dir / "sample_schedule.json", plan)
        for item in expansion:
            issues.append(
                process_issue(
                    client,
                    archive_html=archive_html,
                    period=item["period"],
                    category=item["category"],
                    v1_reference=v1_reference,
                )
            )
            _persist_census(
                artifact_dir,
                schedule=schedule,
                issues=issues,
                v1_reference=v1_reference,
                client=client,
            )
        return {
            "archive_metadata": archive_metadata,
            "latest_period": latest_period,
            "issues_planned": len(schedule),
            "issues_inspected": len(issues),
            "live_request_attempts_total": client.request_count,
            "cache_hits_total": client.cache_hits,
            "results": {
                name: sum(item["v1_result"] == name for item in issues)
                for name in sorted({item["v1_result"] for item in issues})
            },
        }
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("spike-artifacts/historical-census"),
    )
    parser.add_argument("--inspect-archive", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    client = CensusClient(args.artifact_dir)
    try:
        if args.inspect_archive:
            content, metadata = client.fetch(ARCHIVE_URL, context="Bulletin archive index")
            result = {
                "metadata": metadata,
                "structure": inspect_archive_structure(
                    content.decode("utf-8", errors="replace"), metadata["final_url"]
                ),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.run:
            result = run_census(args.artifact_dir, repo_root=Path.cwd())
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        parser.error("choose an operation")
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
