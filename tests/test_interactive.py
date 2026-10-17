"""Unit and integration tests for interactive review mode and instrumental publishing."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from liric.config import Config
from liric.db import Database, TrackRecord
from liric.interactive import (
    ReviewSummary,
    format_duration,
    publish_instrumental_to_lrclib,
    review_library,
)
from liric.lrclib import LrclibClient


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    db_file = tmp_path / "test_interactive.db"
    return Database(db_file)


def test_review_summary_defaults() -> None:
    summary = ReviewSummary()
    assert summary.reviewed_tracks == 0
    assert summary.accepted == 0
    assert summary.marked_instrumental == 0
    assert summary.published_to_lrclib == 0
    assert summary.skipped == 0
    assert summary.rejected == 0


def test_format_duration() -> None:
    assert format_duration(None) == "N/A"
    assert format_duration(-1.0) == "N/A"
    assert format_duration(0.0) == "N/A"
    assert format_duration(59.0) == "0:59"
    assert format_duration(60.0) == "1:00"
    assert format_duration(125.4) == "2:05"


def test_publish_instrumental_to_lrclib_success() -> None:
    client = MagicMock(spec=LrclibClient)
    client.request_challenge.return_value = {"prefix": "test_prefix", "target": "0000ffff"}
    client.solve_challenge.return_value = "12345"
    client.publish_lyrics.return_value = True

    track = TrackRecord(
        file_path="/music/Ambient/Track 01.flac",
        track_name="Morning Mist",
        artist_name="Nature Sounds",
        album_name="Peaceful Mornings",
        duration=240.5,
    )

    result = publish_instrumental_to_lrclib(track, client)
    assert result is True

    client.request_challenge.assert_called_once()
    client.solve_challenge.assert_called_once_with("test_prefix", "0000ffff")

    expected_lyricsfile = (
        "version: '1.0'\n"
        "metadata:\n"
        "  title: Morning Mist\n"
        "  artist: Nature Sounds\n"
        "  album: Peaceful Mornings\n"
        "  duration_ms: 240500\n"
        "  instrumental: true\n"
        "lines: []\n"
    )
    expected_payload = {
        "trackName": "Morning Mist",
        "artistName": "Nature Sounds",
        "albumName": "Peaceful Mornings",
        "duration": 240.5,
        "plainLyrics": None,
        "syncedLyrics": "[au: instrumental]\n[00:00.00] Instrumental",
        "lyricsfile": expected_lyricsfile,
    }
    client.publish_lyrics.assert_called_once_with(expected_payload, "test_prefix", "12345")


def test_publish_instrumental_to_lrclib_publish_failure() -> None:
    client = MagicMock(spec=LrclibClient)
    client.request_challenge.return_value = {"prefix": "pref", "target": "targ"}
    client.solve_challenge.return_value = "99"
    client.publish_lyrics.return_value = False

    track = TrackRecord(
        file_path="/music/Track.mp3",
        track_name="Track",
        artist_name="Artist",
    )

    result = publish_instrumental_to_lrclib(track, client)
    assert result is False


def test_publish_instrumental_to_lrclib_exception() -> None:
    client = MagicMock(spec=LrclibClient)
    client.request_challenge.side_effect = RuntimeError("Network error")

    track = TrackRecord(
        file_path="/music/Track.mp3",
        track_name="Track",
        artist_name="Artist",
    )

    result = publish_instrumental_to_lrclib(track, client)
    assert result is False


def test_review_library_accept_low_confidence(temp_db: Database, tmp_path: Path) -> None:
    audio_file = tmp_path / "song.mp3"
    audio_file.write_bytes(b"dummy audio content")

    candidates = [
        {
            "id": 101,
            "trackName": "Song",
            "artistName": "Artist",
            "albumName": "Album",
            "duration": 180.0,
            "plainLyrics": "Hello world",
            "syncedLyrics": "[00:01.00] Hello world",
            "instrumental": False,
        }
    ]

    track = TrackRecord(
        file_path=str(audio_file),
        track_name="Song",
        artist_name="Artist",
        album_name="Album",
        duration=180.0,
        fetch_status="fetched",
        confidence=0.60,
        plain_lyrics="Hello world",
        synced_lyrics="[00:01.00] Hello world",
        candidates_json=json.dumps(candidates),
    )
    temp_db.upsert_track(track)

    config = Config(db_path=str(temp_db.db_path), store_mode="lrc")
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        confidence_threshold=0.85,
        prompt_func=lambda prompt, choices: "a",
    )

    assert summary.reviewed_tracks == 1
    assert summary.accepted == 1
    assert summary.skipped == 0
    assert summary.rejected == 0
    assert summary.marked_instrumental == 0

    updated = temp_db.get_track(str(audio_file))
    assert updated is not None
    assert updated.confidence == 1.0
    assert updated.fetch_status == "fetched"
    assert updated.lrclib_id == 101
    assert updated.plain_lyrics == "Hello world"
    assert updated.synced_lyrics == "[00:01.00] Hello world"
    assert updated.applied_at is not None
    # Sidecar .lrc was created by auto_apply
    assert (tmp_path / "song.lrc").exists()


def test_review_library_skip(temp_db: Database) -> None:
    track = TrackRecord(
        file_path="/music/skip.mp3",
        track_name="Skip Song",
        artist_name="Skip Artist",
        fetch_status="fetched",
        confidence=0.55,
        plain_lyrics="Unsure lyrics",
    )
    temp_db.upsert_track(track)

    config = Config(db_path=str(temp_db.db_path))
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        prompt_func=lambda prompt, choices: "s",
    )

    assert summary.reviewed_tracks == 1
    assert summary.skipped == 1
    assert summary.accepted == 0
    assert summary.rejected == 0

    unchanged = temp_db.get_track("/music/skip.mp3")
    assert unchanged is not None
    assert unchanged.confidence == 0.55
    assert unchanged.fetch_status == "fetched"
    assert unchanged.applied_at is None


def test_review_library_reject(temp_db: Database) -> None:
    track = TrackRecord(
        file_path="/music/reject.mp3",
        track_name="Wrong Song",
        artist_name="Wrong Artist",
        fetch_status="fetched",
        confidence=0.50,
        plain_lyrics="Wrong lyrics content",
        synced_lyrics="[00:01.00] Wrong lyrics content",
        lrclib_id=999,
    )
    temp_db.upsert_track(track)

    config = Config(db_path=str(temp_db.db_path))
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        prompt_func=lambda prompt, choices: "r",
    )

    assert summary.reviewed_tracks == 1
    assert summary.rejected == 1
    assert summary.accepted == 0

    updated = temp_db.get_track("/music/reject.mp3")
    assert updated is not None
    assert updated.fetch_status == "not_found"
    assert updated.confidence == 0.0
    assert updated.plain_lyrics is None
    assert updated.synced_lyrics is None
    assert updated.lrclib_id is None
    assert updated.is_instrumental is False


def test_review_library_mark_instrumental_and_publish(temp_db: Database, tmp_path: Path) -> None:
    audio_file = tmp_path / "ambient.mp3"
    audio_file.write_bytes(b"dummy audio")

    track = TrackRecord(
        file_path=str(audio_file),
        track_name="Ambient Soundscape",
        artist_name="Soundscapes",
        album_name="Relaxation",
        duration=300.0,
        fetch_status="not_found",
        confidence=0.0,
    )
    temp_db.upsert_track(track)

    mock_client = MagicMock(spec=LrclibClient)
    mock_client.request_challenge.return_value = {"prefix": "pow_p", "target": "pow_t"}
    mock_client.solve_challenge.return_value = "777"
    mock_client.publish_lyrics.return_value = True

    config = Config(db_path=str(temp_db.db_path), store_mode="lrc")
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        client=mock_client,
        prompt_func=lambda prompt, choices: "i",
        confirm_func=lambda prompt, default: True,
    )

    assert summary.reviewed_tracks == 1
    assert summary.marked_instrumental == 1
    assert summary.published_to_lrclib == 1
    assert summary.accepted == 0

    mock_client.publish_lyrics.assert_called_once()

    updated = temp_db.get_track(str(audio_file))
    assert updated is not None
    assert updated.is_instrumental is True
    assert updated.plain_lyrics is None
    assert updated.synced_lyrics is None
    assert updated.confidence == 1.0
    assert updated.fetch_status == "fetched"
    assert updated.applied_at is not None
    # Check .lrc file contains instrumental marker
    lrc_path = tmp_path / "ambient.lrc"
    assert lrc_path.exists()
    assert "[au: instrumental]" in lrc_path.read_text(encoding="utf-8")


def test_review_library_mark_instrumental_decline_publish(temp_db: Database, tmp_path: Path) -> None:
    audio_file = tmp_path / "sound.mp3"
    audio_file.write_bytes(b"dummy audio")

    track = TrackRecord(
        file_path=str(audio_file),
        track_name="Sound Effect",
        artist_name="FX",
        fetch_status="not_found",
        confidence=0.0,
    )
    temp_db.upsert_track(track)

    mock_client = MagicMock(spec=LrclibClient)

    config = Config(db_path=str(temp_db.db_path), store_mode="lrc")
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        client=mock_client,
        prompt_func=lambda prompt, choices: "i",
        confirm_func=lambda prompt, default: False,
    )

    assert summary.reviewed_tracks == 1
    assert summary.marked_instrumental == 1
    assert summary.published_to_lrclib == 0
    mock_client.publish_lyrics.assert_not_called()

    updated = temp_db.get_track(str(audio_file))
    assert updated is not None
    assert updated.is_instrumental is True
    assert updated.fetch_status == "fetched"


def test_review_library_manual_search(temp_db: Database, tmp_path: Path) -> None:
    audio_file = tmp_path / "search_song.mp3"
    audio_file.write_bytes(b"dummy audio")

    track = TrackRecord(
        file_path=str(audio_file),
        track_name="Misspelled Trck",
        artist_name="Famous Artist",
        fetch_status="not_found",
        confidence=0.0,
    )
    temp_db.upsert_track(track)

    mock_client = MagicMock(spec=LrclibClient)
    mock_client.search_lyrics.return_value = [
        {
            "id": 505,
            "trackName": "Correct Track",
            "artistName": "Famous Artist",
            "albumName": "Hit Album",
            "duration": 210.0,
            "plainLyrics": "Found lyrics",
            "syncedLyrics": "[00:05.00] Found lyrics",
            "instrumental": False,
        }
    ]

    inputs = iter(["Correct Track Famous Artist", "1"])
    config = Config(db_path=str(temp_db.db_path), store_mode="lrc")
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        client=mock_client,
        prompt_func=lambda prompt, choices: "m",
        input_func=lambda prompt: next(inputs),
    )

    assert summary.reviewed_tracks == 1
    assert summary.accepted == 1
    mock_client.search_lyrics.assert_called_once_with(q="Correct Track Famous Artist")

    updated = temp_db.get_track(str(audio_file))
    assert updated is not None
    assert updated.lrclib_id == 505
    assert updated.plain_lyrics == "Found lyrics"
    assert updated.synced_lyrics == "[00:05.00] Found lyrics"
    assert updated.confidence == 1.0
    assert updated.fetch_status == "fetched"
    assert updated.applied_at is not None
    assert (tmp_path / "search_song.lrc").exists()


def test_review_library_manual_search_cancelled_then_skip(temp_db: Database) -> None:
    track = TrackRecord(
        file_path="/music/cancel.mp3",
        track_name="Cancel Track",
        artist_name="Cancel Artist",
        fetch_status="not_found",
        confidence=0.0,
    )
    temp_db.upsert_track(track)

    mock_client = MagicMock(spec=LrclibClient)
    mock_client.search_lyrics.return_value = [{"id": 1, "trackName": "Irrelevant", "artistName": "Artist"}]

    # First prompt: "m", search query: "query", pick: "c" (cancel)
    # Second prompt: "s" (skip)
    prompts = iter(["m", "s"])
    inputs = iter(["query", "c"])

    config = Config(db_path=str(temp_db.db_path))
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        client=mock_client,
        prompt_func=lambda p, ch: next(prompts),
        input_func=lambda p: next(inputs),
    )

    assert summary.reviewed_tracks == 1
    assert summary.skipped == 1
    assert summary.accepted == 0


def test_review_library_dry_run_does_not_modify_db_or_files_or_publish(temp_db: Database, tmp_path: Path) -> None:
    audio_1 = tmp_path / "dry1.mp3"
    audio_1.write_bytes(b"audio 1")
    audio_2 = tmp_path / "dry2.mp3"
    audio_2.write_bytes(b"audio 2")

    cand1 = [
        {
            "id": 901,
            "trackName": "Dry Song 1",
            "artistName": "Artist 1",
            "plainLyrics": "Lyrics 1",
            "syncedLyrics": "[00:01.00] Lyrics 1",
            "instrumental": False,
        }
    ]

    t1 = TrackRecord(
        file_path=str(audio_1),
        track_name="Dry Song 1",
        artist_name="Artist 1",
        fetch_status="fetched",
        confidence=0.50,
        plain_lyrics="Lyrics 1",
        synced_lyrics="[00:01.00] Lyrics 1",
        candidates_json=json.dumps(cand1),
    )
    t2 = TrackRecord(
        file_path=str(audio_2),
        track_name="Dry Song 2",
        artist_name="Artist 2",
        fetch_status="not_found",
        confidence=0.0,
    )
    temp_db.upsert_track(t1)
    temp_db.upsert_track(t2)

    mock_client = MagicMock(spec=LrclibClient)
    mock_client.request_challenge.return_value = {"prefix": "pref", "target": "targ"}
    mock_client.solve_challenge.return_value = "111"
    mock_client.publish_lyrics.return_value = True

    # Track 1: accept ("a"); Track 2: instrumental ("i") with publish confirmation
    prompts = iter(["a", "i"])
    config = Config(db_path=str(temp_db.db_path), store_mode="both")

    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=True,
        client=mock_client,
        prompt_func=lambda p, ch: next(prompts),
        confirm_func=lambda p, d: True,
    )

    assert summary.reviewed_tracks == 2
    assert summary.accepted == 1
    assert summary.marked_instrumental == 1
    assert summary.published_to_lrclib == 0  # Dry-run must NOT publish

    mock_client.publish_lyrics.assert_not_called()
    mock_client.request_challenge.assert_not_called()

    # Verify Database was NOT modified
    saved_t1 = temp_db.get_track(str(audio_1))
    assert saved_t1 is not None
    assert saved_t1.confidence == 0.50  # Unchanged
    assert saved_t1.applied_at is None

    saved_t2 = temp_db.get_track(str(audio_2))
    assert saved_t2 is not None
    assert saved_t2.fetch_status == "not_found"  # Unchanged
    assert saved_t2.is_instrumental is False  # Unchanged
    assert saved_t2.applied_at is None

    # Verify no .lrc files were created
    assert not (tmp_path / "dry1.lrc").exists()
    assert not (tmp_path / "dry2.lrc").exists()


def test_review_library_threshold_and_not_found_filtering(temp_db: Database) -> None:
    # High confidence: confidence >= 0.85 -> not reviewed
    t_high = TrackRecord(
        file_path="/music/high.mp3",
        track_name="High Conf",
        artist_name="Artist",
        fetch_status="fetched",
        confidence=0.92,
    )
    # Low confidence: confidence < 0.85 -> reviewed
    t_low = TrackRecord(
        file_path="/music/low.mp3",
        track_name="Low Conf",
        artist_name="Artist",
        fetch_status="fetched",
        confidence=0.65,
    )
    # Not found -> reviewed when include_not_found=True
    t_not_found = TrackRecord(
        file_path="/music/not_found.mp3",
        track_name="Not Found",
        artist_name="Artist",
        fetch_status="not_found",
        confidence=0.0,
    )
    temp_db.upsert_track(t_high)
    temp_db.upsert_track(t_low)
    temp_db.upsert_track(t_not_found)

    config = Config(db_path=str(temp_db.db_path))

    # Without include_not_found: only t_low is reviewed
    summary_no_nf = review_library(
        db=temp_db,
        config=config,
        confidence_threshold=0.85,
        include_not_found=False,
        prompt_func=lambda p, ch: "s",
    )
    assert summary_no_nf.reviewed_tracks == 1
    assert summary_no_nf.skipped == 1

    # With higher threshold (0.95): t_high and t_low are both reviewed
    summary_high_thresh = review_library(
        db=temp_db,
        config=config,
        confidence_threshold=0.95,
        include_not_found=False,
        prompt_func=lambda p, ch: "s",
    )
    assert summary_high_thresh.reviewed_tracks == 2
    assert summary_high_thresh.skipped == 2


def test_review_library_pick_candidate_by_number(temp_db: Database, tmp_path: Path) -> None:
    audio_file = tmp_path / "multi_cand.mp3"
    _ = audio_file.write_bytes(b"dummy audio")

    candidates = [
        {
            "id": 1,
            "trackName": "Wrong Cand",
            "artistName": "Artist",
            "plainLyrics": "Wrong lyrics",
        },
        {
            "id": 2,
            "trackName": "Right Cand",
            "artistName": "Artist",
            "plainLyrics": "Right lyrics",
            "syncedLyrics": "[00:02.00] Right lyrics",
        },
    ]

    track = TrackRecord(
        file_path=str(audio_file),
        track_name="Song",
        artist_name="Artist",
        fetch_status="fetched",
        confidence=0.50,
        candidates_json=json.dumps(candidates),
    )
    temp_db.upsert_track(track)

    config = Config(db_path=str(temp_db.db_path), store_mode="lrc")
    # Action "2" picks candidate #2
    summary = review_library(
        db=temp_db,
        config=config,
        dry_run=False,
        prompt_func=lambda prompt, choices: "2",
    )

    assert summary.reviewed_tracks == 1
    assert summary.accepted == 1

    updated = temp_db.get_track(str(audio_file))
    assert updated is not None
    assert updated.lrclib_id == 2
    assert updated.plain_lyrics == "Right lyrics"
    assert updated.synced_lyrics == "[00:02.00] Right lyrics"
    assert updated.confidence == 1.0


def test_review_library_empty(temp_db: Database) -> None:
    config = Config(db_path=str(temp_db.db_path))
    summary = review_library(db=temp_db, config=config)
    assert summary.reviewed_tracks == 0
    assert summary.accepted == 0
