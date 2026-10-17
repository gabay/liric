"""Tests for sequential pipeline runner."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from liric.config import Config
from liric.db import Database
from liric.lrclib import LrclibClient
from liric.runner import PipelineSummary, run_pipeline
from tests.test_applier import create_dummy_mp3


@pytest.fixture
def mock_client() -> LrclibClient:
    client = MagicMock(spec=LrclibClient)
    client.get_lyrics.return_value = {
        "id": 100,
        "trackName": "Song",
        "artistName": "Artist",
        "albumName": "Album",
        "duration": 1.0,
        "instrumental": False,
        "plainLyrics": "Plain lyrics line",
        "syncedLyrics": "[00:01.00] Synced lyrics line",
    }
    client.search_lyrics.return_value = []
    return client


def test_run_pipeline_end_to_end(tmp_path: Path, mock_client: LrclibClient) -> None:
    music_dir = tmp_path / "music"
    p1 = create_dummy_mp3(music_dir / "Artist - Song.mp3")

    db_path = tmp_path / "test.db"
    cfg = Config(store_mode="both")

    with Database(db_path) as db:
        summary = run_pipeline(
            folder=music_dir,
            db=db,
            config=cfg,
            dry_run=False,
            client=mock_client,
            show_progress=False,
        )

        assert isinstance(summary, PipelineSummary)

        # Step 1: Scan
        assert summary.scan_summary.total_scanned == 1
        assert summary.scan_summary.new_tracks == 1

        # Step 2: Fetch
        assert summary.fetch_summary.total_processed == 1
        assert summary.fetch_summary.found == 1

        # Step 3: Apply
        assert summary.apply_summary.total_processed == 1
        assert summary.apply_summary.applied == 1

        # Check file artifacts
        lrc_file = music_dir / "Artist - Song.lrc"
        assert lrc_file.exists()
        assert "[00:01.00] Synced lyrics line" in lrc_file.read_text(encoding="utf-8")

        # Check DB state
        track = db.get_track(str(p1))
        assert track is not None
        assert track.fetch_status == "fetched"
        assert track.applied_at is not None
        assert track.is_separate_file is True
        assert track.is_embedded is True


def test_run_pipeline_dry_run(tmp_path: Path, mock_client: LrclibClient) -> None:
    music_dir = tmp_path / "music"
    create_dummy_mp3(music_dir / "Artist - Song.mp3")

    db_path = tmp_path / "test.db"
    cfg = Config(store_mode="both")

    with Database(db_path) as db:
        summary = run_pipeline(
            folder=music_dir,
            db=db,
            config=cfg,
            dry_run=True,
            client=mock_client,
            show_progress=False,
        )

        assert summary.scan_summary.total_scanned == 1
        assert summary.scan_summary.new_tracks == 1

        # In dry run, scan does not write to DB, so fetch and apply have 0 DB tracks to process
        assert summary.fetch_summary.total_processed == 0
        assert summary.apply_summary.total_processed == 0

        # Files and DB must be completely untouched
        lrc_file = music_dir / "Artist - Song.lrc"
        assert not lrc_file.exists()
        assert db.count_tracks() == 0


def test_run_pipeline_with_limit_and_force(tmp_path: Path, mock_client: LrclibClient) -> None:
    music_dir = tmp_path / "music"
    create_dummy_mp3(music_dir / "Artist - Song 1.mp3")
    create_dummy_mp3(music_dir / "Artist - Song 2.mp3")

    db_path = tmp_path / "test.db"
    cfg = Config(store_mode="lrc")

    with Database(db_path) as db:
        summary = run_pipeline(
            folder=music_dir,
            db=db,
            config=cfg,
            dry_run=False,
            force=False,
            limit=1,
            client=mock_client,
            show_progress=False,
        )

        assert summary.scan_summary.total_scanned == 2
        # Limit applied to fetch and apply
        assert summary.fetch_summary.total_processed == 1
        assert summary.apply_summary.total_processed == 1
