"""Tests for SQLite database manager."""

from collections.abc import Generator
from pathlib import Path
import pytest

from liric.db import Database, TrackRecord


@pytest.fixture
def db(tmp_path: Path) -> Generator[Database, None, None]:
    db_file = tmp_path / "test_liric.db"
    database = Database(db_file)
    yield database
    database.close()


def test_table_and_index_creation(db: Database) -> None:
    cursor = db._conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'")
    assert cursor.fetchone() is not None

    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tracks_fetch_status'"
    )
    assert cursor.fetchone() is not None


def test_upsert_and_get_track(db: Database) -> None:
    track = TrackRecord(
        file_path="/music/artist/album/01-song.mp3",
        track_name="Song Name",
        artist_name="Artist Name",
        album_name="Album Name",
        duration=215.5,
        file_mtime=1690000000.0,
        file_size=5242880,
        has_plain=False,
        has_synced=False,
        is_embedded=False,
        is_separate_file=False,
        fetch_status="pending",
    )

    db.upsert_track(track)

    retrieved = db.get_track("/music/artist/album/01-song.mp3")
    assert retrieved is not None
    assert retrieved.file_path == "/music/artist/album/01-song.mp3"
    assert retrieved.track_name == "Song Name"
    assert retrieved.artist_name == "Artist Name"
    assert retrieved.album_name == "Album Name"
    assert retrieved.duration == 215.5
    assert retrieved.fetch_status == "pending"
    assert retrieved.has_plain is False

    # Update existing record
    track.fetch_status = "fetched"
    track.plain_lyrics = "These are lyrics."
    track.has_plain = True
    track.confidence = 0.95
    db.upsert_track(track)

    updated = db.get_track("/music/artist/album/01-song.mp3")
    assert updated is not None
    assert updated.fetch_status == "fetched"
    assert updated.plain_lyrics == "These are lyrics."
    assert updated.has_plain is True
    assert updated.confidence == 0.95
    assert db.count_tracks() == 1


def test_get_nonexistent_track(db: Database) -> None:
    assert db.get_track("/nonexistent/file.mp3") is None


def test_query_by_status(db: Database) -> None:
    t1 = TrackRecord(
        file_path="/music/t1.mp3",
        track_name="Track 1",
        artist_name="Artist 1",
        fetch_status="pending",
    )
    t2 = TrackRecord(
        file_path="/music/t2.mp3",
        track_name="Track 2",
        artist_name="Artist 2",
        fetch_status="fetched",
    )
    t3 = TrackRecord(
        file_path="/music/t3.mp3",
        track_name="Track 3",
        artist_name="Artist 3",
        fetch_status="pending",
    )
    t4 = TrackRecord(
        file_path="/music/t4.mp3",
        track_name="Track 4",
        artist_name="Artist 4",
        fetch_status="error",
    )

    for t in (t1, t2, t3, t4):
        db.upsert_track(t)

    pending = db.get_tracks_by_status("pending")
    assert len(pending) == 2
    assert {t.file_path for t in pending} == {"/music/t1.mp3", "/music/t3.mp3"}

    fetched = db.get_tracks_by_status("fetched")
    assert len(fetched) == 1
    assert fetched[0].file_path == "/music/t2.mp3"

    not_found = db.get_tracks_by_status("not_found")
    assert not_found == []


def test_get_all_tracks_and_count(db: Database) -> None:
    assert db.count_tracks() == 0
    assert db.get_all_tracks() == []

    tracks = [
        TrackRecord(
            file_path=f"/music/track_{i}.mp3",
            track_name=f"Track {i}",
            artist_name="Artist",
            fetch_status="pending" if i % 2 == 0 else "fetched",
        )
        for i in range(5)
    ]

    for t in tracks:
        db.upsert_track(t)

    assert db.count_tracks() == 5
    assert db.count_tracks("pending") == 3
    assert db.count_tracks("fetched") == 2
    assert db.count_tracks("not_found") == 0

    all_tracks = db.get_all_tracks()
    assert len(all_tracks) == 5
    assert [t.file_path for t in all_tracks] == sorted(t.file_path for t in tracks)


def test_clean_dry_run_and_deletion(db: Database) -> None:
    tracks = [
        TrackRecord(
            file_path=f"/music/track_{i}.mp3",
            track_name=f"Track {i}",
            artist_name="Artist",
        )
        for i in range(3)
    ]
    for t in tracks:
        db.upsert_track(t)

    assert db.count_tracks() == 3

    # Dry run should not delete records
    count = db.clean(dry_run=True)
    assert count == 3
    assert db.count_tracks() == 3

    # Actual deletion
    deleted = db.clean(dry_run=False)
    assert deleted == 3
    assert db.count_tracks() == 0
    assert db.get_all_tracks() == []

    # Clean on empty database returns 0
    assert db.clean(dry_run=False) == 0


def test_invalid_fetch_status() -> None:
    with pytest.raises(ValueError, match="Invalid fetch_status"):
        TrackRecord(
            file_path="/test.mp3",
            track_name="Test",
            artist_name="Test",
            fetch_status="invalid_status",
        )
