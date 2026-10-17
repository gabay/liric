"""Unit and integration tests for liric.fetcher."""

from pathlib import Path
from typing import Any, Optional
import pytest

from liric.config import Config
from liric.db import Database, TrackRecord
from liric.fetcher import fetch_lyrics_for_library, track_has_required_lyrics
from liric.lrclib import LrclibClient


class DummyLrclibClient(LrclibClient):
    """Mock LRCLIB client for testing fetcher."""

    def __init__(
        self,
        exact_return: Optional[dict[str, Any]] = None,
        search_return: Optional[list[dict[str, Any]]] = None,
        raise_on_get: bool = False,
        raise_on_search: bool = False,
    ) -> None:
        super().__init__()
        self.exact_return = exact_return
        self.search_return = search_return if search_return is not None else []
        self.raise_on_get = raise_on_get
        self.raise_on_search = raise_on_search
        self.get_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []

    def get_lyrics(
        self,
        track_name: str,
        artist_name: str,
        album_name: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        self.get_calls.append({
            "track_name": track_name,
            "artist_name": artist_name,
            "album_name": album_name,
            "duration": duration,
        })
        if self.raise_on_get:
            raise RuntimeError("Simulated API failure on get_lyrics")
        return self.exact_return

    def search_lyrics(
        self,
        track_name: Optional[str] = None,
        artist_name: Optional[str] = None,
        album_name: Optional[str] = None,
        q: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        self.search_calls.append({
            "track_name": track_name,
            "artist_name": artist_name,
            "album_name": album_name,
            "q": q,
        })
        if self.raise_on_search:
            raise RuntimeError("Simulated API failure on search_lyrics")
        return self.search_return


def test_track_has_required_lyrics() -> None:
    track_synced = TrackRecord(file_path="/1.mp3", track_name="T1", artist_name="A1", has_synced=True)
    track_plain = TrackRecord(file_path="/2.mp3", track_name="T2", artist_name="A2", has_plain=True)
    track_none = TrackRecord(file_path="/3.mp3", track_name="T3", artist_name="A3")

    assert track_has_required_lyrics(track_synced, "synced_only") is True
    assert track_has_required_lyrics(track_synced, "prefer_synced") is True
    assert track_has_required_lyrics(track_plain, "synced_only") is False
    assert track_has_required_lyrics(track_plain, "prefer_synced") is False

    assert track_has_required_lyrics(track_plain, "plain_only") is True
    assert track_has_required_lyrics(track_plain, "prefer_plain") is True
    assert track_has_required_lyrics(track_synced, "plain_only") is False

    assert track_has_required_lyrics(track_none, "prefer_synced") is False


def test_fetch_exact_match_success(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path="/music/queen.mp3",
                track_name="Bohemian Rhapsody",
                artist_name="Queen",
                album_name="A Night at the Opera",
                duration=354.0,
                fetch_status="pending",
            )
        )

        mock_candidate = {
            "id": 1234,
            "trackName": "Bohemian Rhapsody",
            "artistName": "Queen",
            "albumName": "A Night at the Opera",
            "duration": 354,
            "plainLyrics": "Is this the real life?",
            "syncedLyrics": "[00:10.00] Is this the real life?",
            "instrumental": False,
        }
        client = DummyLrclibClient(exact_return=mock_candidate)
        config = Config(db_path=str(db_file), fetch_parallelism=1)

        summary = fetch_lyrics_for_library(
            db=db,
            config=config,
            client=client,
            show_progress=False,
        )

        assert summary.total_processed == 1
        assert summary.found == 1
        assert summary.not_found == 0
        assert summary.errors == 0
        assert summary.skipped == 0

        updated = db.get_track("/music/queen.mp3")
        assert updated is not None
        assert updated.fetch_status == "fetched"
        assert updated.lrclib_id == 1234
        assert updated.plain_lyrics == "Is this the real life?"
        assert updated.synced_lyrics == "[00:10.00] Is this the real life?"
        assert updated.confidence == 1.0
        assert updated.candidates_json is not None


def test_fetch_search_fallback(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path="/music/song.mp3",
                track_name="My Song",
                artist_name="Great Artist",
                duration=200.0,
                fetch_status="pending",
            )
        )

        # exact lookup returns None, search returns matching candidate
        search_results = [
            {
                "id": 5678,
                "trackName": "My Song",
                "artistName": "Great Artist",
                "duration": 200,
                "syncedLyrics": "[00:05.00] Lyrics here",
            }
        ]
        client = DummyLrclibClient(exact_return=None, search_return=search_results)
        config = Config(db_path=str(db_file))

        summary = fetch_lyrics_for_library(
            db=db,
            config=config,
            client=client,
            show_progress=False,
        )

        assert summary.total_processed == 1
        assert summary.found == 1
        assert len(client.get_calls) == 1
        assert len(client.search_calls) == 1

        updated = db.get_track("/music/song.mp3")
        assert updated is not None
        assert updated.fetch_status == "fetched"
        assert updated.lrclib_id == 5678
        assert updated.synced_lyrics == "[00:05.00] Lyrics here"


def test_fetch_not_found(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path="/music/ghost.mp3",
                track_name="Ghost Track",
                artist_name="Ghost Artist",
                fetch_status="pending",
            )
        )

        client = DummyLrclibClient(exact_return=None, search_return=[])
        config = Config(db_path=str(db_file))

        summary = fetch_lyrics_for_library(
            db=db,
            config=config,
            client=client,
            show_progress=False,
        )

        assert summary.total_processed == 1
        assert summary.found == 0
        assert summary.not_found == 1

        updated = db.get_track("/music/ghost.mp3")
        assert updated is not None
        assert updated.fetch_status == "not_found"
        assert updated.lrclib_id is None


def test_fetch_dry_run_does_not_mutate_db(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path="/music/dry.mp3",
                track_name="Dry Run Song",
                artist_name="Artist",
                fetch_status="pending",
            )
        )

        mock_candidate = {
            "id": 9999,
            "trackName": "Dry Run Song",
            "artistName": "Artist",
            "syncedLyrics": "[00:01] Hello",
        }
        client = DummyLrclibClient(exact_return=mock_candidate)
        config = Config(db_path=str(db_file))

        summary = fetch_lyrics_for_library(
            db=db,
            config=config,
            dry_run=True,
            client=client,
            show_progress=False,
        )

        assert summary.total_processed == 1
        assert summary.found == 1

        # Database record must NOT have changed
        unchanged = db.get_track("/music/dry.mp3")
        assert unchanged is not None
        assert unchanged.fetch_status == "pending"
        assert unchanged.lrclib_id is None
        assert unchanged.synced_lyrics is None


def test_fetch_error_handling(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path="/music/err.mp3",
                track_name="Error Song",
                artist_name="Artist",
                fetch_status="pending",
            )
        )

        client = DummyLrclibClient(raise_on_get=True, raise_on_search=True)
        config = Config(db_path=str(db_file))

        summary = fetch_lyrics_for_library(
            db=db,
            config=config,
            client=client,
            show_progress=False,
        )

        assert summary.total_processed == 1
        assert summary.errors == 1

        updated = db.get_track("/music/err.mp3")
        assert updated is not None
        assert updated.fetch_status == "error"


def test_fetch_skips_tracks_already_having_lyrics(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with Database(db_file) as db:
        # Track 1 already has synced lyrics
        db.upsert_track(
            TrackRecord(
                file_path="/music/has_synced.mp3",
                track_name="Has Synced",
                artist_name="Artist",
                has_synced=True,
                fetch_status="pending",
            )
        )
        # Track 2 does not have lyrics
        db.upsert_track(
            TrackRecord(
                file_path="/music/no_lyrics.mp3",
                track_name="No Lyrics",
                artist_name="Artist",
                has_synced=False,
                fetch_status="pending",
            )
        )

        mock_candidate = {
            "id": 8888,
            "trackName": "No Lyrics",
            "artistName": "Artist",
            "syncedLyrics": "[00:01] Yes",
        }
        client = DummyLrclibClient(exact_return=mock_candidate)
        config = Config(db_path=str(db_file), which_lyrics="prefer_synced")

        summary = fetch_lyrics_for_library(
            db=db,
            config=config,
            client=client,
            show_progress=False,
        )

        assert summary.skipped == 1
        assert summary.total_processed == 1
        assert summary.found == 1
        assert len(client.get_calls) == 1
        assert client.get_calls[0]["track_name"] == "No Lyrics"


def test_fetch_force_and_limit(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    with Database(db_file) as db:
        for i in range(5):
            db.upsert_track(
                TrackRecord(
                    file_path=f"/music/song_{i}.mp3",
                    track_name=f"Song {i}",
                    artist_name="Artist",
                    fetch_status="not_found",
                )
            )

        client = DummyLrclibClient(
            exact_return={"id": 1, "trackName": "Song", "artistName": "Artist", "plainLyrics": "hi"}
        )
        config = Config(db_path=str(db_file), which_lyrics="prefer_plain")

        # Without force: 0 tracks fetched because none are 'pending' or 'error'
        summary_normal = fetch_lyrics_for_library(
            db=db,
            config=config,
            force=False,
            client=client,
            show_progress=False,
        )
        assert summary_normal.total_processed == 0

        # With force and limit=2: exactly 2 tracks are processed
        summary_force = fetch_lyrics_for_library(
            db=db,
            config=config,
            force=True,
            limit=2,
            client=client,
            show_progress=False,
        )
        assert summary_force.total_processed == 2
        assert summary_force.found == 2
