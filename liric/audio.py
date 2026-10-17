"""Audio file metadata and lyrics state extraction using mutagen."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import mutagen

from liric.db import TrackRecord

AUDIO_EXTENSIONS: set[str] = {
    ".mp3",
    ".flac",
    ".m4a",
    ".mp4",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".aif",
    ".wma",
}


@dataclass
class AudioMetadata:
    """Extracted audio metadata and lyrics state."""

    file_path: str
    track_name: str
    artist_name: str
    album_name: str | None = None
    duration: float = 0.0
    file_mtime: float = 0.0
    file_size: int = 0
    has_plain: bool = False
    has_synced: bool = False
    is_embedded: bool = False
    is_separate_file: bool = False

    def to_track_record(self) -> TrackRecord:
        """Convert metadata to a Database TrackRecord."""
        return TrackRecord(
            file_path=self.file_path,
            track_name=self.track_name,
            artist_name=self.artist_name,
            album_name=self.album_name,
            duration=self.duration,
            file_mtime=self.file_mtime,
            file_size=self.file_size,
            has_plain=self.has_plain,
            has_synced=self.has_synced,
            is_embedded=self.is_embedded,
            is_separate_file=self.is_separate_file,
        )


def is_audio_file(file_path: Path | str) -> bool:
    """Check if file has an audio extension supported by liric."""
    return Path(file_path).suffix.lower() in AUDIO_EXTENSIONS


def is_synced_lrc_content(text: str) -> bool:
    """Check if the text contains synced LRC timestamps like [01:23 or [1:23."""
    if not text:
        return False
    return bool(re.search(r"\[\d{1,2}:\d{2}", text))


def _has_text_lines(text: str) -> bool:
    """Check if text contains lyric text lines (excluding bracketed metadata tags)."""
    if not text:
        return False
    for line in text.splitlines():
        cleaned = re.sub(r"\[.*?\]", "", line).strip()
        if cleaned:
            return True
    return False


def _parse_filename_fallback(stem: str) -> tuple[str, str | None]:
    """Parse filename stem into (track_name, artist_name)."""
    parts = [p.strip() for p in stem.split(" - ") if p.strip()]
    if len(parts) == 2:
        return parts[1], parts[0]
    elif len(parts) >= 3 and parts[0].isdigit():
        return " - ".join(parts[2:]), parts[1]
    elif len(parts) >= 3:
        return " - ".join(parts[1:]), parts[0]
    return stem.strip(), None


def _extract_tag_text(val: Any) -> str | None:
    """Extract string text from a mutagen tag value."""
    if val is None:
        return None
    if hasattr(val, "text"):
        t = val.text
        if isinstance(t, list) and len(t) > 0:
            return str(t[0]).strip() or None
        return str(t).strip() or None
    if isinstance(val, list) and len(val) > 0:
        return str(val[0]).strip() or None
    if isinstance(val, str):
        return val.strip() or None
    return str(val).strip() or None


def _find_tag_text(tags: Any, candidates: list[str]) -> str | None:
    """Look up first matching tag text among candidates."""
    if tags is None or not hasattr(tags, "get"):
        return None
    for cand in candidates:
        try:
            val = tags.get(cand)
            if val is None:
                val = tags.get(cand.lower())
            if val is None:
                val = tags.get(cand.upper())
            if val is not None:
                txt = _extract_tag_text(val)
                if txt:
                    return txt
        except (KeyError, ValueError):
            continue
    return None


def _inspect_embedded_lyrics(tags: Any) -> tuple[bool, bool, bool]:
    """Inspect mutagen tags for embedded plain and synced lyrics.

    Returns (has_plain, has_synced, is_embedded).
    """
    if tags is None:
        return False, False, False

    has_plain = False
    has_synced = False
    is_embedded = False

    # 1. ID3 frames (mutagen.id3.ID3 or tags with .getall)
    if hasattr(tags, "getall"):
        # SYLT (synchronized lyrics)
        sylt = tags.getall("SYLT")
        if sylt:
            is_embedded = True
            has_synced = True

        # USLT (unsynchronized lyrics/text)
        uslt = tags.getall("USLT")
        for frame in uslt:
            text = getattr(frame, "text", "")
            if text and str(text).strip():
                is_embedded = True
                if is_synced_lrc_content(str(text)):
                    has_synced = True
                if _has_text_lines(str(text)) or not is_synced_lrc_content(str(text)):
                    has_plain = True

        # TXXX frames (user-defined text info like TXXX:LYRICS)
        txxx = tags.getall("TXXX")
        for frame in txxx:
            desc = getattr(frame, "desc", "").upper()
            if desc in ("LYRICS", "UNSYNCEDLYRICS", "SYNCEDLYRICS"):
                text_list = getattr(frame, "text", [])
                text = "\n".join(text_list) if isinstance(text_list, list) else str(text_list)
                if text.strip():
                    is_embedded = True
                    if desc == "SYNCEDLYRICS" or is_synced_lrc_content(text):
                        has_synced = True
                    if desc != "SYNCEDLYRICS" or _has_text_lines(text):
                        has_plain = True

    # 2. MP4 / M4A tags (\xa9lyr)
    if hasattr(tags, "get"):
        try:
            lyr = tags.get("\xa9lyr")
            if lyr:
                text = "\n".join(lyr) if isinstance(lyr, list) else str(lyr)
                if text.strip():
                    is_embedded = True
                    if is_synced_lrc_content(text):
                        has_synced = True
                    if _has_text_lines(text) or not is_synced_lrc_content(text):
                        has_plain = True
        except (KeyError, ValueError):
            pass

    # 3. Vorbis tags (FLAC/OGG/Opus) & ASF / WMA & generic dict tags
    if hasattr(tags, "get"):
        # Synced lyrics tag
        for key in ("syncedlyrics", "SYNCEDLYRICS", "WM/Lyrics_Synchronised"):
            try:
                val = tags.get(key)
            except (KeyError, ValueError):
                val = None
            if val:
                text = "\n".join(val) if isinstance(val, list) else str(val)
                if text.strip():
                    is_embedded = True
                    has_synced = True
                    if _has_text_lines(text):
                        has_plain = True
                break

        # Unsynced lyrics tag
        for key in ("unsyncedlyrics", "UNSYNCEDLYRICS"):
            try:
                val = tags.get(key)
            except (KeyError, ValueError):
                val = None
            if val:
                text = "\n".join(val) if isinstance(val, list) else str(val)
                if text.strip():
                    is_embedded = True
                    if is_synced_lrc_content(text):
                        has_synced = True
                    if _has_text_lines(text) or not is_synced_lrc_content(text):
                        has_plain = True
                break

        # General lyrics tag
        for key in ("lyrics", "LYRICS", "WM/Lyrics", "Lyrics"):
            try:
                val = tags.get(key)
            except (KeyError, ValueError):
                val = None
            if val:
                text = "\n".join(val) if isinstance(val, list) else str(val)
                if text.strip():
                    is_embedded = True
                    if is_synced_lrc_content(text):
                        has_synced = True
                    if _has_text_lines(text) or not is_synced_lrc_content(text):
                        has_plain = True
                break

    return has_plain, has_synced, is_embedded


def _inspect_sidecar_lyrics(audio_path: Path) -> tuple[bool, bool, bool]:
    """Check for separate .lrc sidecar file and inspect its content.

    Returns (has_plain, has_synced, is_separate_file).
    """
    lrc_path = audio_path.with_suffix(".lrc")
    if not lrc_path.is_file():
        lrc_path = audio_path.with_suffix(".LRC")
        if not lrc_path.is_file():
            return False, False, False

    is_separate_file = True
    content = ""
    for enc in ("utf-8", "utf-8-sig", "latin-1"):
        try:
            content = lrc_path.read_text(encoding=enc)
            break
        except Exception:
            continue

    has_synced = is_synced_lrc_content(content)
    has_plain = _has_text_lines(content)

    return has_plain, has_synced, is_separate_file


def extract_audio_metadata(file_path: Path | str) -> Optional[AudioMetadata]:
    """Extract audio metadata and lyric status from an audio file.

    Returns AudioMetadata if extraction succeeds, or None if file cannot be parsed.
    """
    path = Path(file_path).expanduser().resolve()
    if not path.is_file() or not is_audio_file(path):
        return None

    try:
        stat = path.stat()
        file_mtime = float(stat.st_mtime)
        file_size = int(stat.st_size)
    except OSError:
        return None

    try:
        audio = mutagen.File(path)
    except Exception:
        return None

    if audio is None:
        return None

    duration = float(getattr(audio.info, "length", 0.0) or 0.0)
    if duration < 0:
        duration = 0.0

    tags = getattr(audio, "tags", None)

    title_candidates = ["TIT2", "title", "\xa9nam", "Title"]
    artist_candidates = ["TPE1", "TPE2", "artist", "\xa9ART", "aART", "Author", "WM/AlbumArtist"]
    album_candidates = ["TALB", "album", "\xa9alb", "WM/AlbumTitle"]

    tag_title = _find_tag_text(tags, title_candidates)
    tag_artist = _find_tag_text(tags, artist_candidates)
    tag_album = _find_tag_text(tags, album_candidates)

    track_name = tag_title
    artist_name = tag_artist

    if not track_name:
        fallback_title, fallback_artist = _parse_filename_fallback(path.stem)
        track_name = fallback_title
        if not artist_name and fallback_artist:
            artist_name = fallback_artist

    if not artist_name:
        artist_name = "Unknown Artist"

    if not track_name:
        track_name = path.stem

    emb_plain, emb_synced, is_embedded = _inspect_embedded_lyrics(tags)
    side_plain, side_synced, is_separate_file = _inspect_sidecar_lyrics(path)

    has_plain = emb_plain or side_plain
    has_synced = emb_synced or side_synced

    return AudioMetadata(
        file_path=str(path),
        track_name=track_name,
        artist_name=artist_name,
        album_name=tag_album,
        duration=duration,
        file_mtime=file_mtime,
        file_size=file_size,
        has_plain=has_plain,
        has_synced=has_synced,
        is_embedded=is_embedded,
        is_separate_file=is_separate_file,
    )
