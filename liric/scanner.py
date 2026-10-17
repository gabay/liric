"""Music library directory scanner and metadata database synchronizer."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from liric.audio import extract_audio_metadata, is_audio_file
from liric.db import Database


@dataclass
class ScanSummary:
    """Summary of library scan results."""

    total_scanned: int = 0
    new_tracks: int = 0
    updated_tracks: int = 0
    unchanged_tracks: int = 0
    failed_files: int = 0
    with_synced: int = 0
    with_plain: int = 0
    with_no_lyrics: int = 0


def _process_audio_file(
    file_path: Path,
    db: Database,
    dry_run: bool,
    summary: ScanSummary,
) -> None:
    """Extract metadata and update database or summary counts for a single file."""
    meta = extract_audio_metadata(file_path)
    if meta is None:
        summary.failed_files += 1
        return

    if meta.has_synced:
        summary.with_synced += 1
    if meta.has_plain:
        summary.with_plain += 1
    if not meta.has_synced and not meta.has_plain:
        summary.with_no_lyrics += 1

    existing = db.get_track(meta.file_path)
    if existing is None:
        summary.new_tracks += 1
        if not dry_run:
            db.upsert_track(meta.to_track_record())
    else:
        # Check if mtime and size match
        mtime_matches = existing.file_mtime is not None and abs(existing.file_mtime - meta.file_mtime) < 1e-4
        size_matches = existing.file_size == meta.file_size
        lyrics_matches = (
            existing.has_synced == meta.has_synced
            and existing.has_plain == meta.has_plain
            and existing.is_separate_file == meta.is_separate_file
        )

        if mtime_matches and size_matches and lyrics_matches:
            summary.unchanged_tracks += 1
        else:
            summary.updated_tracks += 1
            if not dry_run:
                existing.track_name = meta.track_name
                existing.artist_name = meta.artist_name
                existing.album_name = meta.album_name
                existing.duration = meta.duration
                existing.file_mtime = meta.file_mtime
                existing.file_size = meta.file_size
                existing.has_plain = meta.has_plain
                existing.has_synced = meta.has_synced
                existing.is_embedded = meta.is_embedded
                existing.is_separate_file = meta.is_separate_file
                db.upsert_track(existing)


def scan_directory(
    folder: Path | str,
    db: Database,
    dry_run: bool = False,
    console: Optional[Console] = None,
    show_progress: bool = True,
) -> ScanSummary:
    """Recursively scan folder for audio files, extract metadata, and populate the database."""
    target_folder = Path(folder).expanduser().resolve()
    if not target_folder.exists() or not target_folder.is_dir():
        raise FileNotFoundError(f"Directory not found: {target_folder}")

    # Recursively collect audio files
    audio_files: list[Path] = []
    for p in target_folder.rglob("*"):
        if p.is_file() and is_audio_file(p):
            # Skip hidden files or files in hidden directories
            try:
                rel = p.relative_to(target_folder)
                if any(part.startswith(".") for part in rel.parts):
                    continue
            except ValueError:
                if p.name.startswith("."):
                    continue
            audio_files.append(p)

    audio_files.sort()
    summary = ScanSummary(total_scanned=len(audio_files))

    if show_progress and audio_files:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("[cyan]Scanning audio files...", total=len(audio_files))
            for file_path in audio_files:
                _process_audio_file(file_path, db, dry_run, summary)
                progress.advance(task)
    else:
        for file_path in audio_files:
            _process_audio_file(file_path, db, dry_run, summary)

    return summary
