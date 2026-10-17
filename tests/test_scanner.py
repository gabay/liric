"""Tests for directory scanning and database synchronization."""

import os
import time
import wave
from pathlib import Path

import pytest

from liric.db import Database
from liric.scanner import scan_directory


def create_dummy_wav(path: Path, duration_seconds: float = 1.0) -> Path:
    """Helper to create a small valid WAV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    framerate = 44100
    nframes = int(framerate * duration_seconds)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(framerate)
        f.writeframes(b"\x00\x00" * nframes)
    return path


@pytest.fixture
def test_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_scanner.db"
    return Database(db_file)


def test_scan_empty_directory(test_db: Database, tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty_music"
    empty_dir.mkdir()

    summary = scan_directory(empty_dir, test_db, show_progress=False)
    assert summary.total_scanned == 0
    assert summary.new_tracks == 0
    assert summary.updated_tracks == 0
    assert summary.unchanged_tracks == 0
    assert summary.failed_files == 0
    assert test_db.count_tracks() == 0


def test_scan_nonexistent_directory(test_db: Database, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        scan_directory(tmp_path / "nonexistent", test_db, show_progress=False)


def test_scan_directory_dry_run(test_db: Database, tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    create_dummy_wav(music_dir / "Artist A - Track 1.wav")
    create_dummy_wav(music_dir / "Artist B - Track 2.wav")

    summary = scan_directory(music_dir, test_db, dry_run=True, show_progress=False)

    assert summary.total_scanned == 2
    assert summary.new_tracks == 2
    assert summary.updated_tracks == 0
    assert summary.unchanged_tracks == 0
    assert summary.failed_files == 0

    # In dry-run mode, database must remain empty
    assert test_db.count_tracks() == 0


def test_scan_directory_normal_run(test_db: Database, tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    create_dummy_wav(music_dir / "Artist A - Track 1.wav")
    create_dummy_wav(music_dir / "Artist B - Track 2.wav")

    summary = scan_directory(music_dir, test_db, dry_run=False, show_progress=False)

    assert summary.total_scanned == 2
    assert summary.new_tracks == 2
    assert summary.updated_tracks == 0
    assert summary.unchanged_tracks == 0
    assert summary.failed_files == 0

    # Records must be saved to DB
    assert test_db.count_tracks() == 2
    tracks = test_db.get_all_tracks()
    track_names = {t.track_name for t in tracks}
    assert track_names == {"Track 1", "Track 2"}


def test_scan_directory_skipping_unchanged(test_db: Database, tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    create_dummy_wav(music_dir / "Artist A - Track 1.wav")
    create_dummy_wav(music_dir / "Artist B - Track 2.wav")

    # Initial scan
    scan_directory(music_dir, test_db, dry_run=False, show_progress=False)
    assert test_db.count_tracks() == 2

    # Second scan: files have not changed
    summary2 = scan_directory(music_dir, test_db, dry_run=False, show_progress=False)
    assert summary2.total_scanned == 2
    assert summary2.new_tracks == 0
    assert summary2.updated_tracks == 0
    assert summary2.unchanged_tracks == 2


def test_scan_directory_updating_modified_track(test_db: Database, tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    file1 = create_dummy_wav(music_dir / "Artist A - Track 1.wav")
    create_dummy_wav(music_dir / "Artist B - Track 2.wav")

    # Initial scan
    scan_directory(music_dir, test_db, dry_run=False, show_progress=False)
    assert test_db.count_tracks() == 2

    # Modify file1 (change mtime and duration)
    time.sleep(0.01)
    create_dummy_wav(file1, duration_seconds=3.0)
    new_mtime = time.time() + 10.0
    os.utime(file1, (new_mtime, new_mtime))

    # Second scan: file1 should be updated, file2 unchanged
    summary = scan_directory(music_dir, test_db, dry_run=False, show_progress=False)
    assert summary.total_scanned == 2
    assert summary.new_tracks == 0
    assert summary.updated_tracks == 1
    assert summary.unchanged_tracks == 1

    # Verify updated record
    rec = test_db.get_track(str(file1.resolve()))
    assert rec is not None
    assert rec.duration == 3.0


def test_scan_directory_lyrics_stats(test_db: Database, tmp_path: Path) -> None:
    music_dir = tmp_path / "music"
    create_dummy_wav(music_dir / "synced_track.wav")
    create_dummy_wav(music_dir / "plain_track.wav")
    create_dummy_wav(music_dir / "no_lyrics_track.wav")

    # Add synced .lrc
    (music_dir / "synced_track.lrc").write_text("[00:01.00] Synced line\n")

    # Add plain .lrc
    (music_dir / "plain_track.lrc").write_text("Plain line\n")

    summary = scan_directory(music_dir, test_db, show_progress=False)
    assert summary.total_scanned == 3
    assert summary.with_synced == 1
    assert summary.with_plain == 2
    assert summary.with_no_lyrics == 1


def test_scan_directory_subdirectories_and_hidden_files(test_db: Database, tmp_path: Path) -> None:
    music_dir = tmp_path / "nested_music"
    create_dummy_wav(music_dir / "Genre1" / "Album1" / "Artist - Song1.wav")
    create_dummy_wav(music_dir / "Genre2" / "Album2" / "Artist - Song2.wav")

    # Create hidden files and directories
    create_dummy_wav(music_dir / ".hidden_folder" / "secret.wav")
    create_dummy_wav(music_dir / "Genre1" / ".hidden_song.wav")
    (music_dir / "Genre1" / ".DS_Store").write_text("garbage")

    summary = scan_directory(music_dir, test_db, show_progress=False)
    assert summary.total_scanned == 2
    assert summary.new_tracks == 2


def test_scan_failed_files(test_db: Database, tmp_path: Path) -> None:
    music_dir = tmp_path / "corrupt_music"
    music_dir.mkdir()
    corrupt_file = music_dir / "bad.mp3"
    corrupt_file.write_text("not real audio content")

    create_dummy_wav(music_dir / "valid.wav")

    summary = scan_directory(music_dir, test_db, show_progress=False)
    assert summary.total_scanned == 2
    assert summary.failed_files == 1
    assert summary.new_tracks == 1
    assert test_db.count_tracks() == 1
