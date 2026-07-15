#!/usr/bin/env python3
"""Bounded-memory live access spike for RBI sectoral bank-credit workbooks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests


INDEX_URL = "https://rbi.org.in/Scripts/Data_Sectoral_Deployment.aspx"
DATASET_ID = "RBI_SECTORAL_CREDIT"
CHUNK_SIZE = 64 * 1024
MAX_HTML_BYTES = 5 * 1024 * 1024
MAX_WORKBOOK_BYTES = 100 * 1024 * 1024
TITLE_RE = re.compile(
    r"^Sectoral Deployment of Bank Credit\s*[\u2013\u2014-]\s*"
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(20\d{2})$",
    re.IGNORECASE,
)
DATE_RE = re.compile(r"\b([A-Z][a-z]{2})\s+(\d{1,2}),\s+(20\d{2})\b")
BLOCK_MARKERS = (
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
EXPECTED_XLSX_MEMBERS = {"[Content_Types].xml", "xl/workbook.xml"}


class SpikeError(RuntimeError):
    """A classified spike failure."""

    def __init__(
        self,
        stage: str,
        reason: str,
        status: str = "FAIL",
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason
        self.status = status
        self.evidence = evidence


@dataclass(frozen=True)
class Link:
    text: str
    href: str
    position: int


@dataclass(frozen=True)
class Release:
    title: str
    url: str
    month: date
    release_date: date | None


class LinkTextParser(HTMLParser):
    """Collect links plus a small flat text stream using the standard library."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.text_parts: list[str] = []
        self.links: list[Link] = []
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
                Link(" ".join(self._anchor_parts).strip(), self._href, self._anchor_position)
            )
            self._href = None
            self._anchor_parts = []


def _official_rbi_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme == "https" and (host == "rbi.org.in" or host.endswith(".rbi.org.in"))


def _require_official(url: str, stage: str) -> None:
    if not _official_rbi_url(url):
        raise SpikeError(stage, f"Refusing non-official RBI URL: {url}")


def _response_evidence(response: requests.Response) -> dict[str, Any]:
    return {
        "http_status": response.status_code,
        "final_url": response.url,
        "redirect_history": [
            {
                "status": item.status_code,
                "url": item.url,
                "location": item.headers.get("Location"),
            }
            for item in response.history
        ],
        "content_type": response.headers.get("Content-Type"),
    }


def _check_response_access(response: requests.Response, body_prefix: bytes, stage: str) -> None:
    for item in [*response.history, response]:
        _require_official(item.url, stage)
    text = body_prefix.decode("utf-8", errors="ignore").lower()
    if response.status_code in {401, 403, 407, 429, 451} or any(
        marker in text for marker in BLOCK_MARKERS
    ):
        raise SpikeError(
            stage,
            f"RBI access-control response (HTTP {response.status_code})",
            "SOURCE_BLOCKED",
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise SpikeError(stage, f"Unexpected HTTP status {response.status_code}")


def _request(session: requests.Session, url: str, stage: str) -> requests.Response:
    _require_official(url, stage)
    try:
        return session.get(url, timeout=(10, 45), allow_redirects=True, stream=True)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise SpikeError(stage, f"Network environment prevented retrieval: {exc}", "ENVIRONMENT_BLOCKED") from exc
    except requests.RequestException as exc:
        raise SpikeError(stage, f"HTTP request failed: {exc}") from exc


def _read_bounded(response: requests.Response, stage: str, limit: int = MAX_HTML_BYTES) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) > limit:
        response.close()
        raise SpikeError(stage, f"Response exceeds {limit} byte limit")
    body = bytearray()
    try:
        for chunk in response.iter_content(CHUNK_SIZE):
            if not chunk:
                continue
            body.extend(chunk)
            if len(body) > limit:
                raise SpikeError(stage, f"Response exceeds {limit} byte limit")
    except requests.RequestException as exc:
        raise SpikeError(stage, f"Response stream failed: {exc}", "ENVIRONMENT_BLOCKED") from exc
    finally:
        response.close()
    _check_response_access(response, bytes(body[:8192]), stage)
    return bytes(body)


def _decode_html(response: requests.Response, body: bytes) -> str:
    encoding = response.encoding or "utf-8"
    return body.decode(encoding, errors="replace")


def fetch_index(session: requests.Session, index_url: str = INDEX_URL) -> tuple[str, dict[str, Any]]:
    response = _request(session, index_url, "fetch_index")
    evidence = _response_evidence(response)
    try:
        body = _read_bounded(response, "fetch_index")
    except SpikeError as exc:
        exc.evidence = evidence
        raise
    return _decode_html(response, body), evidence


def _parse_date(text: str) -> date | None:
    match = DATE_RE.search(text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%b %d, %Y").date()
    except ValueError:
        return None


def discover_latest_release(html: str, base_url: str = INDEX_URL) -> Release:
    parser = LinkTextParser()
    parser.feed(html)
    releases: list[Release] = []
    for link in parser.links:
        title = " ".join(link.text.split())
        match = TITLE_RE.fullmatch(title)
        if not match:
            continue
        url = urljoin(base_url, link.href)
        _require_official(url, "discover_latest_release")
        month = datetime.strptime(f"{match.group(1)} {match.group(2)}", "%B %Y").date()
        nearby_start = max(0, link.position - 4)
        nearby = parser.text_parts[nearby_start : link.position + 5]
        dated = [
            (abs((nearby_start + i) - link.position), parsed)
            for i, part in enumerate(nearby)
            if (parsed := _parse_date(part))
        ]
        release_date = min(dated, default=(0, None), key=lambda item: item[0])[1]
        releases.append(Release(title, url, month, release_date))
    if not releases:
        raise SpikeError("discover_latest_release", "No explicitly matching sectoral-credit release link found")
    return max(releases, key=lambda item: (item.month, item.release_date or date.min, item.url))


def fetch_release_page(session: requests.Session, url: str) -> tuple[str, dict[str, Any]]:
    response = _request(session, url, "fetch_release_page")
    evidence = _response_evidence(response)
    try:
        body = _read_bounded(response, "fetch_release_page")
    except SpikeError as exc:
        exc.evidence = evidence
        raise
    return _decode_html(response, body), evidence


def discover_workbook_url(html: str, base_url: str) -> str:
    parser = LinkTextParser()
    parser.feed(html)
    matching = [link for link in parser.links if re.fullmatch(r"Statements?\s+I\s+and\s+II", link.text, re.I)]
    if not matching:
        raise SpikeError("discover_workbook_url", "No link labelled 'Statements I and II' found")
    urls = sorted({urljoin(base_url, link.href) for link in matching})
    for url in urls:
        _require_official(url, "discover_workbook_url")
    if len(urls) != 1:
        raise SpikeError("discover_workbook_url", f"Expected one workbook URL, found {len(urls)}")
    return urls[0]


def _looks_like_html(prefix: bytes) -> bool:
    sample = prefix.lstrip().lower()
    return sample.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or b"<html" in sample[:2048]


def validate_xlsx_response(path: Path, prefix: bytes | None = None) -> None:
    """Validate a downloaded file structurally without parsing workbook data."""
    if prefix is None:
        with path.open("rb") as handle:
            prefix = handle.read(8192)
    if _looks_like_html(prefix):
        raise SpikeError("validate_xlsx_response", "Downloaded response is HTML, not XLSX")
    if not prefix.startswith(b"PK\x03\x04"):
        raise SpikeError("validate_xlsx_response", "Downloaded response lacks the ZIP/XLSX signature")
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            missing = EXPECTED_XLSX_MEMBERS - names
            if archive.testzip() is not None:
                raise SpikeError("validate_xlsx_response", "XLSX ZIP contains a corrupt member")
    except (zipfile.BadZipFile, OSError) as exc:
        raise SpikeError("validate_xlsx_response", f"Cannot open response as ZIP: {exc}") from exc
    if missing:
        raise SpikeError("validate_xlsx_response", f"XLSX members missing: {sorted(missing)}")


def download_workbook(
    session: requests.Session, url: str, output_dir: Path
) -> tuple[Path, dict[str, Any]]:
    response = _request(session, url, "download_workbook")
    evidence = _response_evidence(response)
    content_length = response.headers.get("Content-Length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_WORKBOOK_BYTES:
        response.close()
        raise SpikeError("download_workbook", f"Workbook exceeds {MAX_WORKBOOK_BYTES} byte limit")
    fd, temporary_name = tempfile.mkstemp(prefix="rbi-sectoral-", suffix=".part", dir=output_dir)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    byte_size = 0
    prefix = bytearray()
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_content(CHUNK_SIZE):
                if not chunk:
                    continue
                byte_size += len(chunk)
                if byte_size > MAX_WORKBOOK_BYTES:
                    raise SpikeError("download_workbook", f"Workbook exceeds {MAX_WORKBOOK_BYTES} byte limit")
                if len(prefix) < 8192:
                    prefix.extend(chunk[: 8192 - len(prefix)])
                digest.update(chunk)
                handle.write(chunk)
        evidence["byte_size"] = byte_size
        _check_response_access(response, bytes(prefix), "download_workbook")
        validate_xlsx_response(temporary, bytes(prefix))
        final = output_dir / "rbi_sectoral_credit_latest.xlsx"
        temporary.replace(final)
    except SpikeError as exc:
        exc.evidence = evidence
        raise
    except requests.RequestException as exc:
        raise SpikeError("download_workbook", f"Download stream failed: {exc}", "ENVIRONMENT_BLOCKED") from exc
    finally:
        response.close()
        temporary.unlink(missing_ok=True)
    evidence.update(byte_size=byte_size, sha256=digest.hexdigest())
    return final, evidence


def build_manifest(**values: Any) -> dict[str, Any]:
    manifest = {
        "status": values.pop("status", "FAIL"),
        "dataset_id": DATASET_ID,
        "index_url": INDEX_URL,
        "discovered_release_title": None,
        "discovered_release_date": None,
        "release_page_url": None,
        "workbook_url": None,
        "final_download_url": None,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "http_status_codes": {},
        "redirect_history": {},
        "content_type": None,
        "byte_size": None,
        "sha256": None,
        "saved_workbook_path": None,
        "failure_stage": None,
        "failure_reason": None,
    }
    manifest.update(values)
    return manifest


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "IndiaMacro-RBI-access-spike/0.1 (+https://rbi.org.in/)",
            "Accept": "text/html,application/xhtml+xml,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;q=0.9,*/*;q=0.5",
            "Accept-Language": "en-IN,en;q=0.9",
        }
    )
    return session


def run(output_dir: Path) -> tuple[dict[str, Any], int]:
    output_dir.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {}
    evidence: dict[str, dict[str, Any]] = {}
    try:
        with _session() as session:
            index_html, evidence["index"] = fetch_index(session)
            release = discover_latest_release(index_html)
            state.update(
                discovered_release_title=release.title,
                discovered_release_date=release.release_date.isoformat() if release.release_date else None,
                release_page_url=release.url,
            )
            release_html, evidence["release_page"] = fetch_release_page(session, release.url)
            workbook_url = discover_workbook_url(release_html, release.url)
            state["workbook_url"] = workbook_url
            workbook_path, evidence["workbook"] = download_workbook(session, workbook_url, output_dir)
            state.update(
                final_download_url=evidence["workbook"]["final_url"],
                content_type=evidence["workbook"]["content_type"],
                byte_size=evidence["workbook"]["byte_size"],
                sha256=evidence["workbook"]["sha256"],
                saved_workbook_path=str(workbook_path.resolve()),
            )
        status, exit_code = "PASS", 0
        failure: dict[str, Any] = {}
    except SpikeError as exc:
        evidence_key = {
            "fetch_index": "index",
            "fetch_release_page": "release_page",
            "download_workbook": "workbook",
            "validate_xlsx_response": "workbook",
        }.get(exc.stage)
        if evidence_key and exc.evidence:
            evidence[evidence_key] = exc.evidence
        if "workbook" in evidence:
            state.update(
                final_download_url=evidence["workbook"].get("final_url"),
                content_type=evidence["workbook"].get("content_type"),
                byte_size=evidence["workbook"].get("byte_size"),
            )
        status, exit_code = exc.status, 2 if exc.status.endswith("BLOCKED") else 1
        failure = {"failure_stage": exc.stage, "failure_reason": exc.reason}
    manifest = build_manifest(
        status=status,
        **state,
        **failure,
        http_status_codes={key: value["http_status"] for key, value in evidence.items()},
        redirect_history={key: value["redirect_history"] for key, value in evidence.items()},
    )
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest, exit_code


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", nargs="?", type=Path, default=Path("spike-artifacts/rbi-sectoral-credit"))
    args = parser.parse_args(argv)
    manifest, exit_code = run(args.output_dir)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
