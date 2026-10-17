"""Tests for liric.watcher module."""

from pathlib import Path
import threading
import time
from unittest.mock import MagicMock, patch
import pytest

from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileMovedEvent

from liric.config import Config
from liric.db import Database, TrackRecord
from liric.lrclib import LrclibClient
from liric.watcher import MusicWatchHandler, process_single_file, watch_folder
from tests.test_applier import create_dummy_mp3


def test_process_single_file_non_audio(tmp_path: Path) -> None:
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("hello", encoding="utf-8")
    db_file = tmp_path / "test.db"

    with Database(db_file) as db:
        config = Config(db_path=str(db_file))
        res = process_single_file(txt_file, db, config)
        assert res is False
        assert db.count_tracks() == 0


def test_process_single_file_new_audio_fetched_and_applied(tmp_path: Path) -> None:
    audio_path = create_dummy_mp3(tmp_path / "Artist - Song.mp3")
    db_file = tmp_path / "test.db"

    mock_client = MagicMock(spec=LrclibClient)
    mock_client.get_lyrics.return_value = {
        "id": 101,
        "trackName": "Song",
        "artistName": "Artist",
        "syncedLyrics": "[00:01.00] Hello World",
        "plainLyrics": "Hello World",
        "instrumental": False,
    }

    with Database(db_file) as db:
        config = Config(db_path=str(db_file), store_mode="lrc")
        res = process_single_file(audio_path, db, config, client=mock_client)
        assert res is True

        track = db.get_track(str(audio_path.resolve()))
        assert track is not None
        assert track.fetch_status == "fetched"
        assert track.applied_at is not None
        assert (tmp_path / "Artist - Song.lrc").exists()
        assert "[00:01.00] Hello World" in (tmp_path / "Artist - Song.lrc").read_text(encoding="utf-8")


def test_process_single_file_dry_run(tmp_path: Path) -> None:
    audio_path = create_dummy_mp3(tmp_path / "Artist - DrySong.mp3")
    db_file = tmp_path / "test.db"

    mock_client = MagicMock(spec=LrclibClient)
    mock_client.get_lyrics.return_value = {
        "id": 102,
        "trackName": "DrySong",
        "artistName": "Artist",
        "syncedLyrics": "[00:01.00] Dry",
        "instrumental": False,
    }

    with Database(db_file) as db:
        config = Config(db_path=str(db_file), store_mode="lrc")
        res = process_single_file(audio_path, db, config, dry_run=True, client=mock_client)
        assert res is True

        # Database and filesystem must remain untouched
        assert db.count_tracks() == 0
        assert not (tmp_path / "Artist - DrySong.lrc").exists()


def test_process_single_file_already_has_lyrics(tmp_path: Path) -> None:
    audio_path = create_dummy_mp3(tmp_path / "Artist - HasLyrics.mp3")
    db_file = tmp_path / "test.db"

    with Database(db_file) as db:
        # Pre-seed track with synced lyrics
        db.upsert_track(
            TrackRecord(
                file_path=str(audio_path.resolve()),
                track_name="HasLyrics",
                artist_name="Artist",
                has_synced=True,
                fetch_status="fetched",
            )
        )
        config = Config(db_path=str(db_file), which_lyrics="prefer_synced")
        mock_client = MagicMock(spec=LrclibClient)

        res = process_single_file(audio_path, db, config, client=mock_client)
        assert res is False
        # Client should not have been called
        assert not mock_client.get_lyrics.called


def test_process_single_file_not_found(tmp_path: Path) -> None:
    audio_path = create_dummy_mp3(tmp_path / "Unknown - Obscure.mp3")
    db_file = tmp_path / "test.db"

    mock_client = MagicMock(spec=LrclibClient)
    mock_client.get_lyrics.return_value = None
    mock_client.search_lyrics.return_value = []

    with Database(db_file) as db:
        config = Config(db_path=str(db_file))
        res = process_single_file(audio_path, db, config, client=mock_client)
        assert res is False

        track = db.get_track(str(audio_path.resolve()))
        assert track is not None
        assert track.fetch_status == "not_found"


def test_music_watch_handler_debounce(tmp_path: Path) -> None:
    audio_path = create_dummy_mp3(tmp_path / "Debounced - Track.mp3")
    db_file = tmp_path / "test.db"

    with Database(db_file) as db:
        config = Config(db_path=str(db_file))
        handler = MusicWatchHandler(db=db, config=config, debounce_seconds=10.0)

        event = FileCreatedEvent(str(audio_path))
        handler.on_created(event)

        assert handler.pending_count == 1
        assert str(audio_path.resolve()) in handler.pending_paths

        # Cancel all to cleanly shut down background timers
        handler.cancel_all()
        assert handler.pending_count == 0


def test_music_watch_handler_ignores_lrc_files(tmp_path: Path) -> None:
    lrc_file = tmp_path / "Song.lrc"
    lrc_file.write_text("[00:00.00] test", encoding="utf-8")
    db_file = tmp_path / "test.db"

    with Database(db_file) as db:
        config = Config(db_path=str(db_file))
        handler = MusicWatchHandler(db=db, config=config, debounce_seconds=1.0)

        event = FileCreatedEvent(str(lrc_file))
        handler.on_created(event)

        assert handler.pending_count == 0


def test_watch_folder_clean_termination_with_stop_event(tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    db_file = tmp_path / "test.db"

    stop_event = threading.Event()

    def set_stop():
        time.sleep(0.1)
        stop_event.set()

    stopper = threading.Thread(target=set_stop)
    stopper.start()

    with Database(db_file) as db:
        config = Config(db_path=str(db_file))
        # Should start observer, wait 0.1s, detect stop_event, and stop cleanly
        watch_folder(
            folder=music_dir,
            db=db,
            config=config,
            stop_event=stop_event,
        )

    stopper.join()
