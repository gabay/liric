"""Audio file lyrics applier (sidecar .lrc files and embedded audio tags)."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mutagen.flac import FLAC
from mutagen.id3 import ID3, TXXX, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.ogg import OggFileType
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
import mutagen
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from liric.audio import extract_audio_metadata
from liric.config import Config
from liric.db import Database, TrackRecord


@dataclass
class ApplySummary:
    """Summary of lyrics apply operation results."""

    total_processed: int = 0
    applied: int = 0
    skipped: int = 0
    errors: int = 0


def write_lrc_file(
    audio_path: Path | str,
    content: str,
    dry_run: bool = False,
) -> bool:
    """Writes UTF-8 encoded text to <audio_path_stem>.lrc."""
    try:
        p = Path(audio_path).expanduser().resolve()
        lrc_path = p.with_suffix(".lrc")
        if not dry_run:
            _ = lrc_path.write_text(content, encoding="utf-8")
        return True
    except Exception:
        return False


def embed_lyrics(
    audio_path: Path | str,
    plain_lyrics: str | None,
    synced_lyrics: str | None,
    is_instrumental: bool = False,
    dry_run: bool = False,
) -> bool:
    """Uses mutagen to embed lyrics into audio file tags.

    Supports:
    - MP3: ID3 tags. If tags don't exist, create ID3. Add USLT frame with
      language 'eng', desc='', text=plain or synced. Also add TXXX:LYRICS frame.
    - FLAC / OGG / Opus: Vorbis comments. Add/update LYRICS and UNSYNCEDLYRICS.
    - MP4 / M4A: Add/update \\xa9lyr.
    """
    if not is_instrumental and not plain_lyrics and not synced_lyrics:
        return False

    try:
        p = Path(audio_path).expanduser().resolve()
        if not p.is_file():
            return False

        audio: Any = mutagen.File(p)
        if audio is None:
            return False

        # Determine lyric text
        if is_instrumental:
            lyrics_text = "Instrumental"
        elif synced_lyrics:
            lyrics_text = synced_lyrics
        elif plain_lyrics:
            lyrics_text = plain_lyrics
        else:
            return False

        ext = p.suffix.lower()

        # 1. MP3 / ID3 tags
        if isinstance(audio, MP3) or ext == ".mp3" or isinstance(getattr(audio, "tags", None), ID3):
            if audio.tags is None:
                try:
                    audio.add_tags()
                except Exception:
                    pass
            tags: Any = audio.tags
            if tags is None:
                return False
            tags.delall("USLT")
            tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics_text))
            tags.add(TXXX(encoding=3, desc="LYRICS", text=[lyrics_text]))
            if synced_lyrics and not is_instrumental:
                tags.add(TXXX(encoding=3, desc="SYNCEDLYRICS", text=[synced_lyrics]))
            if plain_lyrics and not is_instrumental:
                tags.add(TXXX(encoding=3, desc="UNSYNCEDLYRICS", text=[plain_lyrics]))

        # 2. MP4 / M4A
        elif isinstance(audio, MP4) or ext in (".m4a", ".mp4"):
            if audio.tags is None:
                try:
                    audio.add_tags()
                except Exception:
                    pass
            tags = audio.tags
            if tags is None:
                return False
            tags["\xa9lyr"] = [lyrics_text]

        # 3. FLAC / OGG / Opus / Vorbis comments
        elif isinstance(audio, (FLAC, OggVorbis, OggOpus, OggFileType)) or ext in (
            ".flac",
            ".ogg",
            ".opus",
        ):
            if audio.tags is None:
                try:
                    audio.add_tags()
                except Exception:
                    pass
            tags = audio.tags
            if tags is None:
                return False
            tags["LYRICS"] = [lyrics_text]
            tags["UNSYNCEDLYRICS"] = [lyrics_text]
            if synced_lyrics and not is_instrumental:
                tags["SYNCEDLYRICS"] = [synced_lyrics]

        # 4. Fallback for other audio types with ID3 or dict tags (e.g. WAV/AIFF)
        elif hasattr(audio, "tags") and hasattr(audio.tags, "add"):
            tags = audio.tags
            tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics_text))
            tags.add(TXXX(encoding=3, desc="LYRICS", text=[lyrics_text]))
            if synced_lyrics and not is_instrumental:
                tags.add(TXXX(encoding=3, desc="SYNCEDLYRICS", text=[synced_lyrics]))
        elif hasattr(audio, "tags") and hasattr(audio.tags, "__setitem__"):
            tags = audio.tags
            tags["LYRICS"] = [lyrics_text]
            tags["UNSYNCEDLYRICS"] = [lyrics_text]
            if synced_lyrics and not is_instrumental:
                tags["SYNCEDLYRICS"] = [synced_lyrics]
        else:
            return False

        if not dry_run:
            audio.save()

        return True
    except Exception:
        return False


def apply_lyrics_to_track(
    track: TrackRecord,
    store_mode: str,
    dry_run: bool = False,
) -> tuple[bool, str | None]:
    """Applies lyrics to a single track based on store_mode."""
    if store_mode not in ("lrc", "embed", "both"):
        return False, f"Invalid store_mode: '{store_mode}'"

    has_any = track.is_instrumental or bool(track.synced_lyrics) or bool(track.plain_lyrics)
    if not has_any:
        return False, "No lyrics content available for track"

    audio_path = Path(track.file_path).expanduser().resolve()
    if not audio_path.is_file():
        return False, f"Audio file not found: {track.file_path}"

    # Determine lyrics content
    if track.is_instrumental:
        lrc_content = "[au: instrumental]\n[00:00.00] Instrumental\n"
        tag_plain = "Instrumental"
        tag_synced = None
        is_inst = True
    else:
        lrc_content = track.synced_lyrics or track.plain_lyrics
        tag_plain = track.plain_lyrics
        tag_synced = track.synced_lyrics
        is_inst = False

    if store_mode == "lrc":
        if lrc_content is None:
            return False, "No lyrics content to write to .lrc"
        ok = write_lrc_file(audio_path, lrc_content, dry_run=dry_run)
        if not ok:
            return False, f"Failed to write .lrc file for {track.file_path}"
        return True, None

    if store_mode == "embed":
        ok = embed_lyrics(
            audio_path,
            plain_lyrics=tag_plain,
            synced_lyrics=tag_synced,
            is_instrumental=is_inst,
            dry_run=dry_run,
        )
        if not ok:
            return False, f"Failed to embed lyrics in {track.file_path}"
        return True, None

    if store_mode == "both":
        if lrc_content is None:
            return False, "No lyrics content to write to .lrc"
        ok_lrc = write_lrc_file(audio_path, lrc_content, dry_run=dry_run)
        ok_embed = embed_lyrics(
            audio_path,
            plain_lyrics=tag_plain,
            synced_lyrics=tag_synced,
            is_instrumental=is_inst,
            dry_run=dry_run,
        )
        if not ok_lrc and not ok_embed:
            return False, f"Failed to write .lrc and embed lyrics for {track.file_path}"
        if not ok_lrc:
            return False, f"Failed to write .lrc file for {track.file_path}"
        if not ok_embed:
            return False, f"Failed to embed lyrics in {track.file_path}"
        return True, None


def apply_lyrics_to_library(
    db: Database,
    config: Config,
    dry_run: bool = False,
    force: bool = False,
    limit: int | None = None,
    console: Console | None = None,
    show_progress: bool = True,
) -> ApplySummary:
    """Apply lyrics to tracks in library matching selection criteria."""
    candidates = db.get_tracks_by_status("fetched")
    target_tracks: list[TrackRecord] = []
    skipped_count = 0

    for tr in candidates:
        has_lyrics = tr.is_instrumental or bool(tr.synced_lyrics) or bool(tr.plain_lyrics)
        if not has_lyrics:
            continue
        if not force and tr.applied_at is not None:
            skipped_count += 1
        else:
            target_tracks.append(tr)

    if limit is not None and limit >= 0:
        target_tracks = target_tracks[:limit]

    summary = ApplySummary(skipped=skipped_count)
    if not target_tracks:
        return summary

    def _process_one(track: TrackRecord) -> bool:
        success, _ = apply_lyrics_to_track(track, config.store_mode, dry_run=dry_run)
        if success:
            if not dry_run:
                track.applied_at = datetime.now(timezone.utc).isoformat()
                meta = extract_audio_metadata(track.file_path)
                if meta is not None:
                    track.has_plain = meta.has_plain
                    track.has_synced = meta.has_synced
                    track.is_embedded = meta.is_embedded
                    track.is_separate_file = meta.is_separate_file
                    track.file_mtime = meta.file_mtime
                    track.file_size = meta.file_size
                else:
                    if config.store_mode in ("lrc", "both"):
                        track.is_separate_file = True
                    if config.store_mode in ("embed", "both"):
                        track.is_embedded = True
                    if track.plain_lyrics or track.is_instrumental:
                        track.has_plain = True
                    if track.synced_lyrics or track.is_instrumental:
                        track.has_synced = True
                db.upsert_track(track)
            return True
        return False

    if show_progress:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task_id = progress.add_task(
                f"[cyan]Applying lyrics ({len(target_tracks)} tracks)...",
                total=len(target_tracks),
            )
            for track in target_tracks:
                summary.total_processed += 1
                if _process_one(track):
                    summary.applied += 1
                else:
                    summary.errors += 1
                progress.advance(task_id)
    else:
        for track in target_tracks:
            summary.total_processed += 1
            if _process_one(track):
                summary.applied += 1
            else:
                summary.errors += 1

    return summary
