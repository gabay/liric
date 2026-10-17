"""LRCLIB API client with rate limiting, retry backoff, and challenge solver."""

import hashlib
import threading
import time
from typing import Any, Optional

import httpx

from liric import __version__

DEFAULT_BASE_URL = "https://lrclib.net"
DEFAULT_USER_AGENT = f"liric/{__version__} (https://github.com/roigby/liric)"
DEFAULT_LRCLIB_CLIENT = f"liric v{__version__}"


class LrclibError(Exception):
    """Base exception for LRCLIB client errors."""


class LrclibClient:
    """Thread-safe LRCLIB HTTP API client with rate limit handling and retries."""

    base_url: str
    app_id: Optional[str]
    api_key: Optional[str]
    max_retries: int
    timeout: float
    _client: httpx.Client
    _custom_client: bool
    _lock: threading.Lock
    _pause_until: float

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        app_id: Optional[str] = None,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        timeout: float = 15.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_id = app_id
        self.api_key = api_key
        self.max_retries = max_retries
        self.timeout = timeout
        self._custom_client = client is not None
        self._client = client or httpx.Client()
        self._lock = threading.Lock()
        self._pause_until = 0.0

    @property
    def default_headers(self) -> dict[str, str]:
        """Construct default headers required by LRCLIB."""
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Lrclib-Client": DEFAULT_LRCLIB_CLIENT,
        }
        if self.app_id:
            headers["Lrclib-App-Id"] = self.app_id
        if self.api_key:
            headers["Lrclib-Api-Key"] = self.api_key
        return headers

    @staticmethod
    def _parse_retry_after(header_val: Optional[str], default: float = 2.0) -> float:
        """Parse Retry-After header into seconds as float."""
        if not header_val:
            return default
        try:
            val = float(header_val)
            return val if val > 0 else default
        except (ValueError, TypeError):
            return default

    def _wait_if_paused(self) -> None:
        """Wait if client is currently in a collective rate-limit pause window."""
        with self._lock:
            now = time.monotonic()
            wait_time = self._pause_until - now
        if wait_time > 0:
            time.sleep(wait_time)

    def _apply_rate_limit_pause(self, retry_after: float) -> None:
        """Update collective pause target and sleep."""
        sleep_duration = retry_after + 0.1
        with self._lock:
            now = time.monotonic()
            self._pause_until = max(self._pause_until, now + sleep_duration)
        time.sleep(sleep_duration)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[Any] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> httpx.Response:
        """Execute HTTP request with retries, exponential backoff, and 429/503 handling."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        req_headers = self.default_headers
        if headers:
            req_headers.update(headers)

        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            self._wait_if_paused()

            try:
                response = self._client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json_data,
                    headers=req_headers,
                    timeout=self.timeout,
                )

                # 429 Too Many Requests or 503 Service Unavailable / Overloaded
                if response.status_code in (429, 503):
                    if attempt == self.max_retries:
                        response.raise_for_status()
                    retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                    self._apply_rate_limit_pause(retry_after)
                    continue

                # 5xx Server errors (500, 502, 504, etc.)
                if 500 <= response.status_code < 600:
                    if attempt == self.max_retries:
                        response.raise_for_status()
                    backoff = min(4.0, 1.0 * (2**attempt))
                    time.sleep(backoff)
                    continue

                return response

            except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
                last_exc = exc
                if attempt == self.max_retries:
                    raise exc
                backoff = min(4.0, 1.0 * (2**attempt))
                time.sleep(backoff)

        if last_exc:
            raise last_exc
        raise RuntimeError("Retries exhausted without response")

    def get_lyrics(
        self,
        track_name: str,
        artist_name: str,
        album_name: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Fetch exact lyrics for a track.

        Calls GET /api/get. Returns dict if found (200), or None if not found (404).
        """
        params: dict[str, Any] = {
            "track_name": track_name,
            "artist_name": artist_name,
        }
        if album_name:
            params["album_name"] = album_name
        if duration is not None:
            params["duration"] = round(duration)

        response = self._request("GET", "/api/get", params=params)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else None

    def search_lyrics(
        self,
        track_name: Optional[str] = None,
        artist_name: Optional[str] = None,
        album_name: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Search lyrics candidates.

        Calls GET /api/search. Returns list of candidate dicts.
        """
        params: dict[str, Any] = {}
        if track_name:
            params["track_name"] = track_name
        if artist_name:
            params["artist_name"] = artist_name
        if album_name:
            params["album_name"] = album_name
        if q:
            params["q"] = q

        response = self._request("GET", "/api/search", params=params)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []

    def request_challenge(self) -> dict[str, str]:
        """Request a proof-of-work challenge for publishing lyrics.

        Calls POST /api/request-challenge. Returns dict with prefix and target.
        """
        response = self._request("POST", "/api/request-challenge")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise LrclibError(f"Unexpected response from request-challenge: {data}")
        return data

    @staticmethod
    def solve_challenge(prefix: str, target: str) -> str:
        """Solve a proof-of-work challenge.

        Iterates nonce=0,1,2... until sha256(prefix + str(nonce)) <= target bytes.
        Returns nonce as string.
        """
        target_bytes = bytes.fromhex(target.strip())
        prefix_bytes = prefix.encode("utf-8")
        nonce = 0
        while True:
            digest = hashlib.sha256(prefix_bytes + str(nonce).encode("utf-8")).digest()
            if digest <= target_bytes:
                return str(nonce)
            nonce += 1

    def publish_lyrics(self, payload: dict[str, Any], prefix: str, nonce: str) -> bool:
        """Publish lyrics to LRCLIB with proof-of-work token.

        Calls POST /api/publish with header X-Publish-Token: {prefix}:{nonce}.
        Returns True if successful, False otherwise.
        """
        headers = {"X-Publish-Token": f"{prefix}:{nonce}"}
        try:
            response = self._request("POST", "/api/publish", json_data=payload, headers=headers)
            return response.status_code in (200, 201)
        except Exception:
            return False

    def close(self) -> None:
        """Close HTTP client if opened internally."""
        if not self._custom_client:
            self._client.close()

    def __enter__(self) -> "LrclibClient":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
