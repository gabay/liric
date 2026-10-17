"""Unit and integration tests for liric.lrclib API client."""

import hashlib
from unittest.mock import patch
import httpx
import pytest

from liric import __version__
from liric.lrclib import LrclibClient


def test_client_headers() -> None:
    client_default = LrclibClient()
    headers = client_default.default_headers
    assert headers["User-Agent"] == f"liric/{__version__} (https://github.com/roigby/liric)"
    assert headers["Lrclib-Client"] == f"liric v{__version__}"
    assert "Lrclib-Api-Key" not in headers

    client_with_key = LrclibClient(api_key="secret123")
    headers_keyed = client_with_key.default_headers
    assert headers_keyed["Lrclib-Api-Key"] == "secret123"


def test_get_lyrics_success() -> None:
    recorded_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": 101,
                "trackName": "Under Pressure",
                "artistName": "Queen & David Bowie",
                "albumName": "Hot Space",
                "duration": 248,
                "instrumental": False,
                "plainLyrics": "Pressure pushing down on me...",
                "syncedLyrics": "[00:10.00] Pressure pushing down on me...",
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client, api_key="test_api_key")
        result = client.get_lyrics(
            track_name="Under Pressure",
            artist_name="Queen & David Bowie",
            album_name="Hot Space",
            duration=248.4,
        )

    assert result is not None
    assert result["id"] == 101
    assert result["trackName"] == "Under Pressure"
    assert len(recorded_requests) == 1
    req = recorded_requests[0]
    assert req.url.path == "/api/get"
    assert req.url.params["track_name"] == "Under Pressure"
    assert req.url.params["artist_name"] == "Queen & David Bowie"
    assert req.url.params["album_name"] == "Hot Space"
    assert req.url.params["duration"] == "248"
    assert req.headers["Lrclib-Api-Key"] == "test_api_key"


def test_get_lyrics_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"statusCode": 404, "error": "Not Found"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client)
        result = client.get_lyrics(track_name="Unknown Track", artist_name="Unknown Artist")

    assert result is None


def test_search_lyrics() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("q") == "notfound":
            return httpx.Response(404, json=[])
        return httpx.Response(
            200,
            json=[
                {"id": 1, "trackName": "Track 1", "artistName": "Artist A"},
                {"id": 2, "trackName": "Track 2", "artistName": "Artist A"},
            ],
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client)

        results = client.search_lyrics(track_name="Track", artist_name="Artist A")
        assert len(results) == 2
        assert results[0]["id"] == 1
        assert results[1]["id"] == 2

        empty = client.search_lyrics(q="notfound")
        assert empty == []


def test_retry_after_handling_429() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "1"},
                json={"error": "Too Many Requests"},
            )
        return httpx.Response(200, json={"id": 99, "trackName": "Success"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client, max_retries=2)
        with patch("time.sleep") as mock_sleep:
            res = client.get_lyrics(track_name="Song", artist_name="Artist")

    assert res is not None
    assert res["id"] == 99
    assert call_count == 2
    # Verify sleep was called with retry_after (1) + 0.1 jitter
    mock_sleep.assert_called_with(pytest.approx(1.1, rel=1e-2))


def test_retry_after_handling_503() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(
                503,
                headers={"Retry-After": "2"},
                json={"error": "Overloaded"},
            )
        return httpx.Response(200, json={"id": 100, "trackName": "Recovered"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client, max_retries=2)
        with patch("time.sleep") as mock_sleep:
            res = client.get_lyrics(track_name="Song", artist_name="Artist")

    assert res is not None
    assert res["id"] == 100
    assert call_count == 2
    mock_sleep.assert_called_with(pytest.approx(2.1, rel=1e-2))


def test_exponential_backoff_on_5xx_errors() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return httpx.Response(500, text="Internal Server Error")
        return httpx.Response(200, json={"id": 102, "trackName": "After Errors"})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client, max_retries=3)
        with patch("time.sleep") as mock_sleep:
            res = client.get_lyrics(track_name="Song", artist_name="Artist")

    assert res is not None
    assert res["id"] == 102
    assert call_count == 3
    # Check that backoff was called with 1.0 (2^0) then 2.0 (2^1)
    assert mock_sleep.call_count == 2
    mock_sleep.assert_any_call(1.0)
    mock_sleep.assert_any_call(2.0)


def test_network_error_backoff_and_exhaustion() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client, max_retries=2)
        with patch("time.sleep") as mock_sleep:
            with pytest.raises(httpx.ConnectError):
                client.get_lyrics(track_name="Song", artist_name="Artist")

    assert call_count == 3  # initial + 2 retries
    assert mock_sleep.call_count == 2


def test_request_challenge_and_solve() -> None:
    target_hex = "00ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    prefix = "challenge_prefix_test"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/request-challenge":
            return httpx.Response(200, json={"prefix": prefix, "target": target_hex})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client)
        challenge = client.request_challenge()
        assert challenge["prefix"] == prefix
        assert challenge["target"] == target_hex

        nonce = client.solve_challenge(challenge["prefix"], challenge["target"])
        assert isinstance(nonce, str)
        assert nonce.isdigit()

        # Verify the solution
        digest = hashlib.sha256((prefix + nonce).encode("utf-8")).digest()
        assert digest <= bytes.fromhex(target_hex)


def test_publish_lyrics() -> None:
    published_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/publish":
            published_headers.append(dict(request.headers))
            if request.headers.get("X-Publish-Token") == "good_prefix:good_nonce":
                return httpx.Response(201, json={"status": "published"})
            return httpx.Response(400, json={"error": "invalid token"})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http_client:
        client = LrclibClient(client=http_client)

        payload = {
            "trackName": "My Song",
            "artistName": "My Artist",
            "plainLyrics": "Hello world",
        }

        # Success case
        ok = client.publish_lyrics(payload, prefix="good_prefix", nonce="good_nonce")
        assert ok is True
        assert published_headers[-1]["x-publish-token"] == "good_prefix:good_nonce"

        # Failure case
        fail = client.publish_lyrics(payload, prefix="bad_prefix", nonce="bad_nonce")
        assert fail is False
