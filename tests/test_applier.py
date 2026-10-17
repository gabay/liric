"""Tests for lyrics applier engine (sidecar .lrc files and embedded audio tags)."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch
import wave

from mutagen.flac import FLAC
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis

from liric.applier import (
    apply_lyrics_to_library,
    apply_lyrics_to_track,
    embed_lyrics,
    write_lrc_file,
)
from liric.audio import extract_audio_metadata
from liric.config import Config
from liric.db import Database, TrackRecord


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


def create_dummy_mp3(path: Path) -> Path:
    """Helper to create a small valid MP3 file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = b"\xff\xfb\x90\x00" + b"\x00" * 413
    path.write_bytes(frame * 10)
    return path


def create_dummy_flac(path: Path) -> Path:
    """Helper to create a small valid FLAC file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sr_chan_bps_samples = (44100 << 44) | (1 << 41) | (15 << 36) | 1000
    sr_bytes = sr_chan_bps_samples.to_bytes(8, "big")
    si_data = b"\x10\x00\x10\x00\x00\x00\x00\x00\x00\x00" + sr_bytes + b"\x00" * 16
    flac_bytes = b"fLaC\x80\x00\x00\x22" + si_data
    path.write_bytes(flac_bytes)
    return path


def create_dummy_m4a(path: Path) -> Path:
    """Helper to create a small valid M4A file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ftyp = b"\x00\x00\x00\x14ftypM4A \x00\x00\x00\x00M4A "
    moov = b"\x00\x00\x00\x08moov"
    path.write_bytes(ftyp + moov)
    return path


def test_write_lrc_file_normal(tmp_path: Path) -> None:
    audio_path = tmp_path / "song.mp3"
    audio_path.touch()
    content = "[00:01.00] Line one\n[00:05.00] Line two\n"

    result = write_lrc_file(audio_path, content)
    assert result is True

    lrc_path = tmp_path / "song.lrc"
    assert lrc_path.exists()
    assert lrc_path.read_text(encoding="utf-8") == content


def test_write_lrc_file_instrumental(tmp_path: Path) -> None:
    audio_path = tmp_path / "inst.flac"
    audio_path.touch()
    content = "[au: instrumental]\n[00:00.00] Instrumental\n"

    result = write_lrc_file(audio_path, content)
    assert result is True

    lrc_path = tmp_path / "inst.lrc"
    assert lrc_path.exists()
    assert lrc_path.read_text(encoding="utf-8") == content


def test_write_lrc_file_dry_run(tmp_path: Path) -> None:
    audio_path = tmp_path / "dry_song.mp3"
    audio_path.touch()
    content = "[00:01.00] Dry run line"

    result = write_lrc_file(audio_path, content, dry_run=True)
    assert result is True

    lrc_path = tmp_path / "dry_song.lrc"
    assert not lrc_path.exists()


def test_write_lrc_file_invalid() -> None:
    assert write_lrc_file("/nonexistent/dir/cannot_write/song.mp3", "content") is False


def test_embed_lyrics_mp3(tmp_path: Path) -> None:
    mp3_file = tmp_path / "test.mp3"
    create_dummy_mp3(mp3_file)

    plain = "Plain lyrics"
    synced = "[00:01.00] Synced lyrics"
    assert embed_lyrics(mp3_file, plain_lyrics=plain, synced_lyrics=synced) is True

    audio = MP3(str(mp3_file))
    assert audio.tags is not None
    uslt = audio.tags.getall("USLT")
    assert len(uslt) > 0
    assert uslt[0].text == synced

    txxx = audio.tags.getall("TXXX")
    lyrics_txxx = [f for f in txxx if f.desc == "LYRICS"]
    assert len(lyrics_txxx) > 0
    assert lyrics_txxx[0].text == [synced]

    meta = extract_audio_metadata(mp3_file)
    assert meta is not None
    assert meta.is_embedded is True
    assert meta.has_plain is True
    assert meta.has_synced is True


def test_embed_lyrics_flac(tmp_path: Path) -> None:
    flac_file = tmp_path / "test.flac"
    create_dummy_flac(flac_file)

    plain = "FLAC plain lyrics"
    synced = "[00:02.00] FLAC synced lyrics"
    assert embed_lyrics(flac_file, plain_lyrics=plain, synced_lyrics=synced) is True

    audio = FLAC(str(flac_file))
    assert audio.tags is not None
    tags: Any = audio.tags
    assert tags["LYRICS"] == [synced]
    assert tags["UNSYNCEDLYRICS"] == [synced]
    assert tags["SYNCEDLYRICS"] == [synced]

    meta = extract_audio_metadata(flac_file)
    assert meta is not None
    assert meta.is_embedded is True
    assert meta.has_plain is True
    assert meta.has_synced is True


def test_embed_lyrics_m4a(tmp_path: Path) -> None:
    m4a_file = tmp_path / "test.m4a"
    create_dummy_m4a(m4a_file)

    synced = "[00:03.00] M4A synced line"
    assert embed_lyrics(m4a_file, plain_lyrics=None, synced_lyrics=synced) is True

    audio = MP4(str(m4a_file))
    assert audio.tags is not None
    assert audio.tags["\xa9lyr"] == [synced]

    meta = extract_audio_metadata(m4a_file)
    assert meta is not None
    assert meta.is_embedded is True
    assert meta.has_plain is True
    assert meta.has_synced is True


def test_embed_lyrics_ogg(tmp_path: Path) -> None:
    ogg_file = tmp_path / "test.ogg"
    ogg_file.touch()

    mock_audio = MagicMock(spec=OggVorbis)
    mock_audio.tags = {}

    with patch("liric.applier.mutagen.File", return_value=mock_audio):
        res = embed_lyrics(ogg_file, plain_lyrics="OGG Plain", synced_lyrics="[00:04.00] OGG Synced")
        assert res is True
        assert mock_audio.tags["LYRICS"] == ["[00:04.00] OGG Synced"]
        assert mock_audio.tags["UNSYNCEDLYRICS"] == ["[00:04.00] OGG Synced"]
        assert mock_audio.tags["SYNCEDLYRICS"] == ["[00:04.00] OGG Synced"]
        mock_audio.save.assert_called_once()


def test_embed_lyrics_instrumental(tmp_path: Path) -> None:
    mp3_file = tmp_path / "inst.mp3"
    create_dummy_mp3(mp3_file)

    assert embed_lyrics(mp3_file, plain_lyrics=None, synced_lyrics=None, is_instrumental=True) is True

    audio = MP3(str(mp3_file))
    assert audio.tags is not None
    uslt = audio.tags.getall("USLT")
    assert len(uslt) > 0
    assert uslt[0].text == "Instrumental"


def test_embed_lyrics_dry_run(tmp_path: Path) -> None:
    mp3_file = tmp_path / "dry.mp3"
    create_dummy_mp3(mp3_file)

    assert embed_lyrics(mp3_file, plain_lyrics="Lyrics", synced_lyrics=None, dry_run=True) is True

    audio = MP3(str(mp3_file))
    # In dry-run, tags were not saved to disk
    assert audio.tags is None


def test_embed_lyrics_no_content_fails(tmp_path: Path) -> None:
    mp3_file = tmp_path / "test.mp3"
    create_dummy_mp3(mp3_file)
    assert embed_lyrics(mp3_file, plain_lyrics=None, synced_lyrics=None, is_instrumental=False) is False


def test_embed_lyrics_missing_file() -> None:
    assert embed_lyrics("/nonexistent/file.mp3", plain_lyrics="test", synced_lyrics=None) is False


def test_apply_lyrics_to_track_store_mode_lrc(tmp_path: Path) -> None:
    mp3_file = tmp_path / "track_lrc.mp3"
    create_dummy_mp3(mp3_file)

    track = TrackRecord(
        file_path=str(mp3_file),
        track_name="Track LRC",
        artist_name="Artist",
        synced_lyrics="[00:01.00] Synced line",
    )

    success, error = apply_lyrics_to_track(track, store_mode="lrc")
    assert success is True
    assert error is None

    # Sidecar .lrc should exist
    lrc_path = tmp_path / "track_lrc.lrc"
    assert lrc_path.exists()
    assert "[00:01.00] Synced line" in lrc_path.read_text(encoding="utf-8")

    # Audio tags should not have been embedded
    audio = MP3(str(mp3_file))
    assert audio.tags is None


def test_apply_lyrics_to_track_store_mode_embed(tmp_path: Path) -> None:
    mp3_file = tmp_path / "track_embed.mp3"
    create_dummy_mp3(mp3_file)

    track = TrackRecord(
        file_path=str(mp3_file),
        track_name="Track Embed",
        artist_name="Artist",
        synced_lyrics="[00:01.00] Embed line",
    )

    success, error = apply_lyrics_to_track(track, store_mode="embed")
    assert success is True
    assert error is None

    # Sidecar .lrc should NOT exist
    lrc_path = tmp_path / "track_embed.lrc"
    assert not lrc_path.exists()

    # Audio tags should be embedded
    audio = MP3(str(mp3_file))
    assert audio.tags is not None
    uslt = audio.tags.getall("USLT")
    assert len(uslt) > 0
    assert uslt[0].text == "[00:01.00] Embed line"


def test_apply_lyrics_to_track_store_mode_both(tmp_path: Path) -> None:
    mp3_file = tmp_path / "track_both.mp3"
    create_dummy_mp3(mp3_file)

    track = TrackRecord(
        file_path=str(mp3_file),
        track_name="Track Both",
        artist_name="Artist",
        synced_lyrics="[00:01.00] Both line",
    )

    success, error = apply_lyrics_to_track(track, store_mode="both")
    assert success is True
    assert error is None

    # Sidecar .lrc should exist
    lrc_path = tmp_path / "track_both.lrc"
    assert lrc_path.exists()

    # Audio tags should be embedded
    audio = MP3(str(mp3_file))
    assert audio.tags is not None


def test_apply_lyrics_to_track_instrumental(tmp_path: Path) -> None:
    mp3_file = tmp_path / "track_inst.mp3"
    create_dummy_mp3(mp3_file)

    track = TrackRecord(
        file_path=str(mp3_file),
        track_name="Track Inst",
        artist_name="Artist",
        is_instrumental=True,
    )

    success, error = apply_lyrics_to_track(track, store_mode="both")
    assert success is True
    assert error is None

    lrc_path = tmp_path / "track_inst.lrc"
    assert lrc_path.exists()
    assert "[au: instrumental]" in lrc_path.read_text(encoding="utf-8")


def test_apply_lyrics_to_track_invalid_mode(tmp_path: Path) -> None:
    mp3_file = tmp_path / "track.mp3"
    create_dummy_mp3(mp3_file)

    track = TrackRecord(
        file_path=str(mp3_file),
        track_name="Track",
        artist_name="Artist",
        synced_lyrics="lyrics",
    )

    success, error = apply_lyrics_to_track(track, store_mode="invalid")
    assert success is False
    assert "Invalid store_mode" in (error or "")


def test_apply_lyrics_to_track_missing_file() -> None:
    track = TrackRecord(
        file_path="/nonexistent/missing.mp3",
        track_name="Track",
        artist_name="Artist",
        synced_lyrics="lyrics",
    )
    success, error = apply_lyrics_to_track(track, store_mode="lrc")
    assert success is False
    assert "not found" in (error or "")


def test_apply_lyrics_to_track_no_lyrics(tmp_path: Path) -> None:
    mp3_file = tmp_path / "track_empty.mp3"
    create_dummy_mp3(mp3_file)

    track = TrackRecord(
        file_path=str(mp3_file),
        track_name="Track",
        artist_name="Artist",
    )
    success, error = apply_lyrics_to_track(track, store_mode="lrc")
    assert success is False
    assert "No lyrics" in (error or "")


def test_apply_lyrics_to_library_dry_run(tmp_path: Path) -> None:
    mp3_file = tmp_path / "song.mp3"
    create_dummy_mp3(mp3_file)

    db_path = tmp_path / "test.db"
    with Database(db_path) as db:
        track = TrackRecord(
            file_path=str(mp3_file),
            track_name="Song",
            artist_name="Artist",
            fetch_status="fetched",
            synced_lyrics="[00:01.00] Line",
        )
        db.upsert_track(track)

        config = Config(store_mode="lrc")
        summary = apply_lyrics_to_library(db, config, dry_run=True, show_progress=False)

        assert summary.total_processed == 1
        assert summary.applied == 1
        assert summary.skipped == 0
        assert summary.errors == 0

        # File and DB should not be touched
        lrc_file = tmp_path / "song.lrc"
        assert not lrc_file.exists()

        stored = db.get_track(str(mp3_file))
        assert stored is not None
        assert stored.applied_at is None


def test_apply_lyrics_to_library_normal(tmp_path: Path) -> None:
    mp3_file = tmp_path / "song.mp3"
    create_dummy_mp3(mp3_file)

    db_path = tmp_path / "test.db"
    with Database(db_path) as db:
        track = TrackRecord(
            file_path=str(mp3_file),
            track_name="Song",
            artist_name="Artist",
            fetch_status="fetched",
            synced_lyrics="[00:01.00] Line",
        )
        db.upsert_track(track)

        config = Config(store_mode="both")
        summary = apply_lyrics_to_library(db, config, dry_run=False, show_progress=False)

        assert summary.total_processed == 1
        assert summary.applied == 1
        assert summary.skipped == 0
        assert summary.errors == 0

        # Sidecar file created
        lrc_file = tmp_path / "song.lrc"
        assert lrc_file.exists()

        # Database record updated with applied_at and flags
        stored = db.get_track(str(mp3_file))
        assert stored is not None
        assert stored.applied_at is not None
        assert stored.is_separate_file is True
        assert stored.is_embedded is True
        assert stored.has_synced is True


def test_apply_lyrics_to_library_force_option(tmp_path: Path) -> None:
    mp3_file = tmp_path / "song.mp3"
    create_dummy_mp3(mp3_file)

    db_path = tmp_path / "test.db"
    with Database(db_path) as db:
        track = TrackRecord(
            file_path=str(mp3_file),
            track_name="Song",
            artist_name="Artist",
            fetch_status="fetched",
            synced_lyrics="[00:01.00] Line",
            applied_at="2026-01-01T00:00:00+00:00",
        )
        db.upsert_track(track)

        config = Config(store_mode="lrc")
        # Without force: track is skipped
        sum_no_force = apply_lyrics_to_library(db, config, force=False, show_progress=False)
        assert sum_no_force.total_processed == 0
        assert sum_no_force.applied == 0
        assert sum_no_force.skipped == 1

        # With force: track is re-applied
        sum_force = apply_lyrics_to_library(db, config, force=True, show_progress=False)
        assert sum_force.total_processed == 1
        assert sum_force.applied == 1
        assert sum_force.skipped == 0


def test_apply_lyrics_to_library_limit_option(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with Database(db_path) as db:
        for i in range(5):
            p = create_dummy_mp3(tmp_path / f"song_{i}.mp3")
            track = TrackRecord(
                file_path=str(p),
                track_name=f"Song {i}",
                artist_name="Artist",
                fetch_status="fetched",
                synced_lyrics="[00:01.00] Line",
            )
            db.upsert_track(track)

        config = Config(store_mode="lrc")
        summary = apply_lyrics_to_library(db, config, limit=2, show_progress=False)

        assert summary.total_processed == 2
        assert summary.applied == 2

        applied_tracks = [t for t in db.get_all_tracks() if t.applied_at is not None]
        assert len(applied_tracks) == 2


def test_apply_lyrics_to_library_missing_file_error(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with Database(db_path) as db:
        track = TrackRecord(
            file_path="/missing/audio.mp3",
            track_name="Missing",
            artist_name="Artist",
            fetch_status="fetched",
            synced_lyrics="[00:01.00] Line",
        )
        db.upsert_track(track)

        config = Config(store_mode="lrc")
        summary = apply_lyrics_to_library(db, config, show_progress=False)

        assert summary.total_processed == 1
        assert summary.applied == 0
        assert summary.errors == 1
