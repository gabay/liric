"""Real-time directory watcher and automatic lyrics enrichment handler using watchdog."""

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import override

from rich.console import Console
from watchdog.events import (
    FileSystemEvent,
    FileSystemEventHandler,
    FileSystemMovedEvent,
)
from watchdog.observers import Observer

from liric.applier import apply_lyrics_to_track
from liric.audio import extract_audio_metadata, is_audio_file
from liric.config import Config
from liric.db import Database
from liric.fetcher import process_track, track_has_required_lyrics
from liric.lrclib import LrclibClient


def process_single_file(
    file_path: Path | str,
    db: Database,
    config: Config,
    dry_run: bool = False,
    client: LrclibClient | None = None,
    console: Console | None = None,
) -> bool:
    """Process a single audio file: extract metadata, fetch lyrics if missing, and apply them.

    Returns:
        bool: True if lyrics were fetched and applied (or would be in dry_run mode),
              False if the file is not an audio file, already has required lyrics,
              no lyrics were found, or an error occurred.
    """
    target_path = Path(file_path).expanduser().resolve()
    if not is_audio_file(target_path) or not target_path.is_file():
        return False

    meta = extract_audio_metadata(target_path)
    if meta is None:
        if console:
            console.print(f"[red]✗ Failed to extract metadata for {target_path.name}[/red]")
        return False

    existing = db.get_track(meta.file_path)
    if existing is None:
        track = meta.to_track_record()
    else:
        track = existing
        track.track_name = meta.track_name
        track.artist_name = meta.artist_name
        track.album_name = meta.album_name
        track.duration = meta.duration
        track.file_mtime = meta.file_mtime
        track.file_size = meta.file_size
        track.has_plain = meta.has_plain or existing.has_plain
        track.has_synced = meta.has_synced or existing.has_synced
        track.is_embedded = meta.is_embedded or existing.is_embedded
        track.is_separate_file = meta.is_separate_file or existing.is_separate_file

    if not dry_run:
        db.upsert_track(track)

    if track_has_required_lyrics(track, config.which_lyrics):
        if console:
            console.print(
                f"[dim]• Skipping '{track.track_name}' by '{track.artist_name}' (already has required lyrics)[/dim]"
            )
        return False

    own_client = client is None
    api_client = client or LrclibClient(api_key=config.api_key)

    try:
        try:
            track, status = process_track(track, api_client, config)
        except Exception as exc:
            if not dry_run:
                track.fetch_status = "error"
                db.upsert_track(track)
            if console:
                console.print(f"[red]✗ Error fetching lyrics for {track.track_name} - {track.artist_name}: {exc}[/red]")
            return False

        if status == "found":
            if not dry_run:
                db.upsert_track(track)

            ok, err = apply_lyrics_to_track(track, config.store_mode, dry_run=dry_run)
            if not ok:
                if not dry_run:
                    track.fetch_status = "error"
                    db.upsert_track(track)
                if console:
                    console.print(
                        f"[red]✗ Failed to apply lyrics for {track.track_name} - {track.artist_name}: {err}[/red]"
                    )
                return False

            if not dry_run:
                track.applied_at = datetime.now(timezone.utc).isoformat()
                meta_after = extract_audio_metadata(track.file_path)
                if meta_after is not None:
                    track.has_plain = meta_after.has_plain
                    track.has_synced = meta_after.has_synced
                    track.is_embedded = meta_after.is_embedded
                    track.is_separate_file = meta_after.is_separate_file
                    track.file_mtime = meta_after.file_mtime
                    track.file_size = meta_after.file_size
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

            dry_suffix = " (Dry Run)" if dry_run else ""
            if console:
                console.print(
                    f"[green]✓ Fetched and applied lyrics for {track.track_name} - {track.artist_name}{dry_suffix}[/green]"
                )
            return True

        elif status == "not_found":
            if not dry_run:
                db.upsert_track(track)
            if console:
                console.print(f"[yellow]• Lyrics not found for {track.track_name} - {track.artist_name}[/yellow]")
            return False

        else:  # status == "error"
            if not dry_run:
                db.upsert_track(track)
            if console:
                console.print(f"[red]✗ Error fetching lyrics for {track.track_name} - {track.artist_name}[/red]")
            return False

    finally:
        if own_client:
            api_client.close()


class MusicWatchHandler(FileSystemEventHandler):
    """Watchdog event handler for monitoring music library changes."""

    db: Database
    config: Config
    dry_run: bool
    debounce_seconds: float
    client: LrclibClient | None
    console: Console | None
    _timers: dict[str, threading.Timer]
    _recently_processed: dict[str, float]
    _lock: threading.Lock

    def __init__(
        self,
        db: Database,
        config: Config,
        dry_run: bool = False,
        debounce_seconds: float = 1.0,
        client: LrclibClient | None = None,
        console: Console | None = None,
    ) -> None:
        super().__init__()
        self.db = db
        self.config = config
        self.dry_run = dry_run
        self.debounce_seconds = debounce_seconds
        self.client = client
        self.console = console
        self._timers = {}
        self._recently_processed = {}
        self._lock = threading.Lock()

    @property
    def pending_count(self) -> int:
        """Return the count of currently pending debounced files."""
        with self._lock:
            return len(self._timers)

    @property
    def pending_paths(self) -> list[str]:
        """Return a copy of currently pending debounced file paths."""
        with self._lock:
            return list(self._timers.keys())

    def cancel_all(self) -> None:
        """Cancel and clear all pending debounce timers."""
        with self._lock:
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()

    def flush(self) -> None:
        """Immediately execute and clear all pending debounced tasks."""
        with self._lock:
            pending = list(self._timers.items())
            for _, timer in pending:
                timer.cancel()
            self._timers.clear()
        for path_str, _ in pending:
            self._dispatch(path_str)

    def _handle_path(self, path: Path | str, is_modification: bool = False) -> None:
        p = Path(path).expanduser().resolve()

        # Ignores .lrc files created by liric itself to avoid infinite loops!
        if p.suffix.lower() == ".lrc" or not is_audio_file(p):
            return

        path_str = str(p)

        # If modified, ignore if it was recently processed (e.g. lyrics embedded by liric)
        if is_modification:
            with self._lock:
                last_time = self._recently_processed.get(path_str, 0.0)
                if time.time() - last_time < max(5.0, self.debounce_seconds * 2):
                    return

        with self._lock:
            if path_str in self._timers:
                self._timers[path_str].cancel()

            if self.debounce_seconds <= 0:
                _ = self._timers.pop(path_str, None)
                self._dispatch(path_str)
                return

            timer = threading.Timer(self.debounce_seconds, self._dispatch, args=[path_str])
            timer.daemon = True
            self._timers[path_str] = timer
            timer.start()

    def _dispatch(self, path_str: str) -> None:
        with self._lock:
            _ = self._timers.pop(path_str, None)
            self._recently_processed[path_str] = time.time()
        try:
            _ = process_single_file(
                file_path=path_str,
                db=self.db,
                config=self.config,
                dry_run=self.dry_run,
                client=self.client,
                console=self.console,
            )
        except Exception as exc:
            if self.console:
                self.console.print(f"[red]Error processing {path_str}: {exc}[/red]")

    @override
    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = str(event.src_path)
        self._handle_path(src_path, is_modification=False)

    @override
    def on_moved(self, event: FileSystemMovedEvent | FileSystemEvent) -> None:
        if event.is_directory:
            return
        if isinstance(event, FileSystemMovedEvent):
            self._handle_path(str(event.dest_path), is_modification=False)
        else:
            dest_path = getattr(event, "dest_path", None)
            if isinstance(dest_path, (str, Path)):
                self._handle_path(str(dest_path), is_modification=False)

    @override
    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        src_path = str(event.src_path)
        self._handle_path(src_path, is_modification=True)


def watch_folder(
    folder: Path | str,
    db: Database,
    config: Config,
    dry_run: bool = False,
    debounce_seconds: float = 1.0,
    stop_event: threading.Event | None = None,
    console: Console | None = None,
    client: LrclibClient | None = None,
) -> None:
    """Set up and run watchdog folder observer for recursive audio file monitoring."""
    target_folder = Path(folder).expanduser().resolve()
    if not target_folder.exists() or not target_folder.is_dir():
        raise FileNotFoundError(f"Directory not found: {target_folder}")

    c = console or Console()
    own_client = client is None
    api_client = client or LrclibClient(api_key=config.api_key)

    handler = MusicWatchHandler(
        db=db,
        config=config,
        dry_run=dry_run,
        debounce_seconds=debounce_seconds,
        client=api_client,
        console=c,
    )

    observer = Observer()
    _ = observer.schedule(handler, str(target_folder), recursive=True)
    observer.start()

    dry_suffix = " [yellow](Dry Run)[/yellow]" if dry_run else ""
    c.print(f"[bold cyan]Watching for music changes in:[/bold cyan] {target_folder}{dry_suffix}")
    c.print("[dim]Press Ctrl+C to stop watching.[/dim]")

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        c.print("\n[yellow]Stopping watcher...[/yellow]")
    finally:
        handler.cancel_all()
        observer.stop()
        observer.join(timeout=5.0)
        if own_client:
            api_client.close()
        c.print("[green]Watcher stopped.[/green]")
