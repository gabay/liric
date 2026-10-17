"""Tests for audio file handling and metadata extraction."""

import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

from mutagen.id3 import ID3, SYLT, TALB, TIT2, TPE1, TXXX, USLT

from liric.audio import (
    extract_audio_metadata,
    is_audio_file,
    is_synced_lrc_content,
)


def create_dummy_wav(path: Path, duration_seconds: float = 1.0) -> Path:
    """Helper to create a small valid WAV file."""
    framerate = 44100
    nframes = int(framerate * duration_seconds)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(framerate)
        f.writeframes(b"\x00\x00" * nframes)
    return path


def test_is_audio_file() -> None:
    assert is_audio_file("song.mp3") is True
    assert is_audio_file("song.MP3") is True
    assert is_audio_file("song.flac") is True
    assert is_audio_file("song.m4a") is True
    assert is_audio_file("song.mp4") is True
    assert is_audio_file("song.ogg") is True
    assert is_audio_file("song.opus") is True
    assert is_audio_file("song.wav") is True
    assert is_audio_file("song.aiff") is True
    assert is_audio_file("song.aif") is True
    assert is_audio_file("song.wma") is True

    assert is_audio_file("lyrics.lrc") is False
    assert is_audio_file("image.jpg") is False
    assert is_audio_file("text.txt") is False
    assert is_audio_file("song.mp3.bak") is False


def test_is_synced_lrc_content() -> None:
    assert is_synced_lrc_content("[00:12.34] Hello world") is True
    assert is_synced_lrc_content("[01:23] Hello world") is True
    assert is_synced_lrc_content("[1:23.45] Single digit min") is True
    assert is_synced_lrc_content("No timestamps here") is False
    assert is_synced_lrc_content("[ar:Artist Name]") is False
    assert is_synced_lrc_content("[ti:Track Title]") is False
    assert is_synced_lrc_content("") is False


def test_extract_audio_metadata_nonexistent_or_invalid(tmp_path: Path) -> None:
    # Non-existent file
    assert extract_audio_metadata(tmp_path / "missing.mp3") is None

    # Non-audio file
    txt_file = tmp_path / "notes.txt"
    txt_file.write_text("just text")
    assert extract_audio_metadata(txt_file) is None

    # Corrupted / unparseable file
    bad_mp3 = tmp_path / "bad.mp3"
    bad_mp3.write_text("not an mp3 content")
    assert extract_audio_metadata(bad_mp3) is None


def test_fallback_when_tags_missing(tmp_path: Path) -> None:
    # Filename format "Artist - Title.wav"
    wav_file1 = tmp_path / "Queen - Bohemian Rhapsody.wav"
    create_dummy_wav(wav_file1, duration_seconds=1.0)

    meta1 = extract_audio_metadata(wav_file1)
    assert meta1 is not None
    assert meta1.track_name == "Bohemian Rhapsody"
    assert meta1.artist_name == "Queen"
    assert meta1.album_name is None
    assert meta1.duration == 1.0
    assert meta1.has_plain is False
    assert meta1.has_synced is False
    assert meta1.is_embedded is False
    assert meta1.is_separate_file is False

    # Filename format "01 - Pink Floyd - Time.wav"
    wav_file2 = tmp_path / "01 - Pink Floyd - Time.wav"
    create_dummy_wav(wav_file2, duration_seconds=1.0)

    meta2 = extract_audio_metadata(wav_file2)
    assert meta2 is not None
    assert meta2.track_name == "Time"
    assert meta2.artist_name == "Pink Floyd"

    # Filename without hyphen "SongName.wav"
    wav_file3 = tmp_path / "SongName.wav"
    create_dummy_wav(wav_file3, duration_seconds=1.0)

    meta3 = extract_audio_metadata(wav_file3)
    assert meta3 is not None
    assert meta3.track_name == "SongName"
    assert meta3.artist_name == "Unknown Artist"


def test_metadata_extraction_with_real_wav_tags(tmp_path: Path) -> None:
    wav_file = tmp_path / "tagged.wav"
    create_dummy_wav(wav_file, duration_seconds=2.0)

    from mutagen.wave import WAVE

    w = WAVE(str(wav_file))
    w.add_tags()
    assert w.tags is not None
    w.tags.add(TIT2(encoding=3, text=["My Song"]))
    w.tags.add(TPE1(encoding=3, text=["My Artist"]))
    w.tags.add(TALB(encoding=3, text=["My Album"]))
    w.tags.add(USLT(encoding=3, lang="eng", desc="", text="Hello lyrics\nSecond line"))
    w.save()

    meta = extract_audio_metadata(wav_file)
    assert meta is not None
    assert meta.track_name == "My Song"
    assert meta.artist_name == "My Artist"
    assert meta.album_name == "My Album"
    assert meta.duration == 2.0
    assert meta.has_plain is True
    assert meta.has_synced is False
    assert meta.is_embedded is True
    assert meta.is_separate_file is False

    # Check to_track_record conversion
    record = meta.to_track_record()
    assert record.track_name == "My Song"
    assert record.artist_name == "My Artist"
    assert record.has_plain is True


def test_sidecar_lrc_synced_detection(tmp_path: Path) -> None:
    wav_file = tmp_path / "track.wav"
    create_dummy_wav(wav_file)

    lrc_file = tmp_path / "track.lrc"
    lrc_file.write_text("[00:01.00] First line\n[00:05.50] Second line\n")

    meta = extract_audio_metadata(wav_file)
    assert meta is not None
    assert meta.is_separate_file is True
    assert meta.has_synced is True
    assert meta.has_plain is True
    assert meta.is_embedded is False


def test_sidecar_lrc_plain_detection(tmp_path: Path) -> None:
    wav_file = tmp_path / "track_plain.wav"
    create_dummy_wav(wav_file)

    lrc_file = tmp_path / "track_plain.lrc"
    lrc_file.write_text("Just plain lyrics line 1\nJust plain lyrics line 2\n")

    meta = extract_audio_metadata(wav_file)
    assert meta is not None
    assert meta.is_separate_file is True
    assert meta.has_synced is False
    assert meta.has_plain is True
    assert meta.is_embedded is False


def test_sidecar_lrc_and_embedded_combination(tmp_path: Path) -> None:
    wav_file = tmp_path / "combo.wav"
    create_dummy_wav(wav_file)

    from mutagen.wave import WAVE

    w = WAVE(str(wav_file))
    w.add_tags()
    assert w.tags is not None
    w.tags.add(USLT(encoding=3, lang="eng", desc="", text="Plain embedded lyrics"))
    w.save()

    lrc_file = tmp_path / "combo.lrc"
    lrc_file.write_text("[00:02.00] Synced external line")

    meta = extract_audio_metadata(wav_file)
    assert meta is not None
    assert meta.is_embedded is True
    assert meta.is_separate_file is True
    assert meta.has_plain is True
    assert meta.has_synced is True


def test_mock_mp3_sylt_and_txxx(tmp_path: Path) -> None:
    mp3_file = tmp_path / "synced.mp3"
    mp3_file.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 500)

    mock_audio = MagicMock()
    mock_audio.info.length = 180.5

    mock_tags = ID3()
    mock_tags.add(TIT2(encoding=3, text=["SYLT Title"]))
    mock_tags.add(TPE1(encoding=3, text=["SYLT Artist"]))
    mock_tags.add(SYLT(encoding=3, lang="eng", format=1, type=1, desc="", text=[("Line", 100)]))
    mock_audio.tags = mock_tags

    with patch("liric.audio.mutagen.File", return_value=mock_audio):
        meta = extract_audio_metadata(mp3_file)
        assert meta is not None
        assert meta.track_name == "SYLT Title"
        assert meta.artist_name == "SYLT Artist"
        assert meta.duration == 180.5
        assert meta.has_synced is True
        assert meta.is_embedded is True


def test_mock_mp3_txxx_lyrics(tmp_path: Path) -> None:
    mp3_file = tmp_path / "txxx.mp3"
    mp3_file.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 500)

    mock_audio = MagicMock()
    mock_audio.info.length = 120.0

    mock_tags = ID3()
    mock_tags.add(TIT2(encoding=3, text=["TXXX Song"]))
    mock_tags.add(TXXX(encoding=3, desc="LYRICS", text=["Some lyric text"]))
    mock_audio.tags = mock_tags

    with patch("liric.audio.mutagen.File", return_value=mock_audio):
        meta = extract_audio_metadata(mp3_file)
        assert meta is not None
        assert meta.has_plain is True
        assert meta.is_embedded is True


def test_mock_vorbis_tags(tmp_path: Path) -> None:
    flac_file = tmp_path / "song.flac"
    flac_file.write_bytes(b"fLaC" + b"\x00" * 100)

    mock_audio = MagicMock()
    mock_audio.info.length = 200.0

    # Vorbis comments with plain lyrics
    mock_audio.tags = {
        "title": ["FLAC Song"],
        "artist": ["FLAC Artist"],
        "album": ["FLAC Album"],
        "lyrics": ["Line one\nLine two"],
    }

    with patch("liric.audio.mutagen.File", return_value=mock_audio):
        meta = extract_audio_metadata(flac_file)
        assert meta is not None
        assert meta.track_name == "FLAC Song"
        assert meta.artist_name == "FLAC Artist"
        assert meta.album_name == "FLAC Album"
        assert meta.has_plain is True
        assert meta.has_synced is False
        assert meta.is_embedded is True

    # Vorbis comments with synced lyrics
    mock_audio.tags = {
        "title": ["FLAC Synced"],
        "artist": ["FLAC Artist"],
        "syncedlyrics": ["[00:03.00] Synced line"],
    }

    with patch("liric.audio.mutagen.File", return_value=mock_audio):
        meta = extract_audio_metadata(flac_file)
        assert meta is not None
        assert meta.has_synced is True
        assert meta.has_plain is True
        assert meta.is_embedded is True


def test_mock_mp4_tags(tmp_path: Path) -> None:
    m4a_file = tmp_path / "song.m4a"
    m4a_file.write_bytes(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 100)

    mock_audio = MagicMock()
    mock_audio.info.length = 150.0
    mock_audio.tags = {
        "\xa9nam": ["M4A Song"],
        "\xa9ART": ["M4A Artist"],
        "\xa9alb": ["M4A Album"],
        "\xa9lyr": ["[00:05.10] MP4 Synced Line"],
    }

    with patch("liric.audio.mutagen.File", return_value=mock_audio):
        meta = extract_audio_metadata(m4a_file)
        assert meta is not None
        assert meta.track_name == "M4A Song"
        assert meta.artist_name == "M4A Artist"
        assert meta.album_name == "M4A Album"
        assert meta.has_synced is True
        assert meta.has_plain is True
        assert meta.is_embedded is True
