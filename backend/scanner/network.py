"""Verified TLS context and HTTP helpers with bounded timeouts and sizes."""
from __future__ import annotations

from functools import lru_cache
import gzip
import http.client
import json
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zlib

from backend.config import USER_AGENT

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class HttpStatusError(RuntimeError):
    """RuntimeError carrying the integer HTTP status code so callers can detect 404/410 closures."""

    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = int(code)


@lru_cache(maxsize=1)
def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except ImportError:
        pass
    if hasattr(ssl, "enum_certificates"):
        for store in ("ROOT", "CA"):
            try:
                certificates = ssl.enum_certificates(store)
            except OSError:
                continue
            for certificate, encoding, trust in certificates:
                if encoding == "x509_asn" and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
                    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
    return context


def _read_decoded_bytes(response, max_bytes: int) -> bytes:
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"Antwort überschreitet Größenlimit ({max_bytes // 1_000_000} MB)")
    encoding = (response.headers.get("Content-Encoding") or "").lower()
    if "gzip" in encoding:
        return gzip.decompress(raw)
    if "deflate" in encoding:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def _urlopen_with_retry(req: Request, timeout: int, max_bytes: int):
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(req, timeout=timeout, context=tls_context()) as response:
                if response.status != 200:
                    raise HttpStatusError(response.status)
                charset = response.headers.get_content_charset() or "utf-8"
                return _read_decoded_bytes(response, max_bytes), charset
        except HTTPError as exc:
            if exc.code in (429, 502, 503, 504) and attempt < 2:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait_s = max(0.5, min(8.0, float(retry_after))) if retry_after else 0.8 * (attempt + 1)
                except ValueError:
                    wait_s = 0.8 * (attempt + 1)
                time.sleep(wait_s)
                continue
            raise HttpStatusError(exc.code) from exc
        except (URLError, TimeoutError, ConnectionResetError, http.client.IncompleteRead) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise
    raise last_exc or RuntimeError("HTTP-Anfrage fehlgeschlagen")


def fetch_json(url: str, timeout: int = 18) -> dict | list:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    raw, charset = _urlopen_with_retry(req, timeout=timeout, max_bytes=6_000_000)
    return json.loads(raw.decode(charset, errors="replace"))


def fetch_html(url: str, timeout: int = 18) -> str:
    accept = (
        "application/xml,text/xml,text/html,*/*;q=0.8"
        if "/xml" in url
        else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    )
    req = Request(
        url,
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": accept,
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    raw, charset = _urlopen_with_retry(req, timeout=timeout, max_bytes=6_000_000)
    return raw.decode(charset, errors="replace")


def portal_request(url: str, data: dict | None = None, timeout: int = 18) -> str:
    is_json = data is not None or "/wday/cxs/" in url or "/api/" in url
    headers = {
        "User-Agent": BROWSER_UA,
        "Accept": "application/json" if is_json else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    raw, charset = _urlopen_with_retry(req, timeout=timeout, max_bytes=5_000_000)
    return raw.decode(charset, errors="replace")
