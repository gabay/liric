"""Interactive review mode and instrumental publishing for liric."""

import json
import signal
import sys
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import click
from rich.console import Console

from liric.applier import apply_lyrics_to_track
from liric.audio import extract_audio_metadata
from liric.config import Config
from liric.db import Database, TrackRecord
from liric.lrclib import LrclibClient


@dataclass
class ReviewSummary:
    """Summary of interactive review operation results."""

    reviewed_tracks: int = 0
    accepted: int = 0
    marked_instrumental: int = 0
    published_to_lrclib: int = 0
    skipped: int = 0
    rejected: int = 0


def format_duration(seconds: float | None) -> str:
    """Format duration in seconds to M:SS or 'N/A'."""
    if seconds is None or seconds <= 0:
        return "N/A"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def _publish_instrumental_task(
    track: TrackRecord,
    client: LrclibClient,
) -> tuple[bool, str]:
    """Request a PoW challenge, solve it, and publish an instrumental marker to LRCLIB.

    Returns a tuple of (success: bool, message: str).
    Does not print directly to console so output can be deferred between user prompts.
    """
    try:
        challenge = client.request_challenge()
        prefix = challenge["prefix"]
        target = challenge["target"]
        nonce = client.solve_challenge(prefix, target)

        payload = {
            "trackName": track.track_name,
            "artistName": track.artist_name,
            "albumName": track.album_name or "",
            "duration": track.duration or 0.0,
            "plainLyrics": None,
            "syncedLyrics": "[au: instrumental]\n[00:00.00] Instrumental",
            "lyricsfile": (
                f"version: '1.0'\n"
                f"metadata:\n"
                f"  title: {track.track_name}\n"
                f"  artist: {track.artist_name}\n"
                f"  album: {track.album_name or ''}\n"
                f"  duration_ms: {int((track.duration or 0) * 1000)}\n"
                f"  instrumental: true\n"
                f"lines: []\n"
            ),
        }

        published = client.publish_lyrics(payload, prefix, nonce)
        if published:
            return True, f"[green]Published instrumental marker to LRCLIB for '{track.track_name}'.[/green]"
        else:
            return False, f"[red]Failed to publish instrumental marker to LRCLIB for '{track.track_name}'.[/red]"
    except Exception as exc:
        return False, f"[red]Error publishing instrumental marker: {exc}[/red]"


def publish_instrumental_to_lrclib(
    track: TrackRecord,
    client: LrclibClient,
    console: Optional[Console] = None,
) -> bool:
    """Request a PoW challenge, solve it, and publish an instrumental marker to LRCLIB."""
    success, message = _publish_instrumental_task(track, client)
    if console:
        console.print(message)
    return success


def _default_prompt(prompt_text: str, choices: list[str], default: str | None) -> str:
    return click.prompt(
        prompt_text,
        default=default,
        type=click.Choice(choices, case_sensitive=False),
        show_choices=True,
    )


def _default_confirm(prompt_text: str, default: bool = True) -> bool:
    return click.confirm(prompt_text, default=default)


def _default_input(prompt_text: str) -> str:
    return click.prompt(prompt_text, default="", show_default=False)


def _apply_lyrics_and_update_track(track: TrackRecord, config: Config) -> None:
    _ = apply_lyrics_to_track(track, config.store_mode, dry_run=False)
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


def review_library(
    db: Database,
    config: Config,
    dry_run: bool = False,
    confidence_threshold: float = 0.85,
    include_not_found: bool = True,
    auto_apply: bool = True,
    console: Optional[Console] = None,
    client: Optional[LrclibClient] = None,
    prompt_func: Optional[Callable[[str, list[str], str], str]] = None,
    confirm_func: Optional[Callable[[str, bool], bool]] = None,
    input_func: Optional[Callable[[str], str]] = None,
    executor: Optional[Executor] = None,
) -> ReviewSummary:
    """Interactively review low-confidence matches and not-found tracks in library."""
    summary = ReviewSummary()
    prompt = prompt_func or _default_prompt
    confirm = confirm_func or _default_confirm
    ask_input = input_func or _default_input

    all_tracks = db.get_all_tracks()
    tracks_to_review = [
        t
        for t in all_tracks
        if not t.is_instrumental
        and (
            (t.fetch_status == "fetched" and t.confidence < confidence_threshold)
            or (include_not_found and t.fetch_status == "not_found")
        )
    ]

    if not tracks_to_review:
        if console:
            console.print("[dim]No tracks requiring interactive review.[/dim]")
        return summary

    own_client = client is None
    api_client = client or LrclibClient(app_id=config.app_id, api_key=config.api_key)

    own_executor = executor is None
    pool = executor or ThreadPoolExecutor(max_workers=1)
    pending_publishes: list[Future[tuple[bool, str]]] = []

    def drain_completed(wait: bool = False) -> None:
        nonlocal pending_publishes
        if not pending_publishes:
            return

        if wait:
            for fut in pending_publishes:
                try:
                    success, msg = fut.result()
                    if console:
                        console.print(msg)
                    if success:
                        summary.published_to_lrclib += 1
                except Exception as exc:
                    if console:
                        console.print(f"[red]Error publishing instrumental marker: {exc}[/red]")
            pending_publishes.clear()
        else:
            still_pending = []
            for fut in pending_publishes:
                if fut.done():
                    try:
                        success, msg = fut.result()
                        if console:
                            console.print(msg)
                        if success:
                            summary.published_to_lrclib += 1
                    except Exception as exc:
                        if console:
                            console.print(f"[red]Error publishing instrumental marker: {exc}[/red]")
                else:
                    still_pending.append(fut)
            pending_publishes = still_pending

    original_handlers: dict[int, Any] = {}
    is_main_thread = threading.current_thread() is threading.main_thread()

    def _restore_signals() -> None:
        for sig, h in original_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, AttributeError, OSError):
                pass
        original_handlers.clear()

    if is_main_thread:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                original_handlers[sig] = signal.getsignal(sig)

                def make_handler(signo: int) -> Callable[[int, Any], None]:
                    def _signal_handler(sig_received: int, frame: Any) -> None:
                        drain_completed(wait=True)
                        orig = original_handlers.get(sig_received)
                        _restore_signals()
                        if callable(orig) and orig not in (signal.SIG_DFL, signal.SIG_IGN):
                            orig(sig_received, frame)
                        elif sig_received == signal.SIGINT:
                            raise KeyboardInterrupt()
                        elif sig_received == signal.SIGTERM:
                            sys.exit(128 + sig_received)

                    return _signal_handler

                signal.signal(sig, make_handler(sig))
            except (ValueError, AttributeError, OSError):
                pass

    try:
        total = len(tracks_to_review)
        for idx, track in enumerate(tracks_to_review, 1):
            summary.reviewed_tracks += 1
            drain_completed(wait=False)

            # Parse candidates from track
            candidates: list[dict[str, Any]] = []
            if track.candidates_json:
                try:
                    loaded = json.loads(track.candidates_json)
                    if isinstance(loaded, list):
                        candidates = [c for c in loaded if isinstance(c, dict)]
                except Exception:
                    candidates = []

            # Fallback if track already has lyrics/instrumental but candidates_json was empty
            if not candidates and (track.plain_lyrics or track.synced_lyrics or track.is_instrumental):
                candidates = [
                    {
                        "id": track.lrclib_id,
                        "trackName": track.track_name,
                        "artistName": track.artist_name,
                        "albumName": track.album_name,
                        "duration": track.duration,
                        "plainLyrics": track.plain_lyrics,
                        "syncedLyrics": track.synced_lyrics,
                        "instrumental": track.is_instrumental,
                    }
                ]

            if console:
                console.print(
                    f"\n[bold cyan]━━━ Track {idx}/{total}: {track.artist_name} - {track.track_name} ━━━[/bold cyan]"
                )
                console.print(f"  [bold]Album:[/bold] {track.album_name or 'N/A'}")
                console.print(f"  [bold]Duration:[/bold] {format_duration(track.duration)}")
                console.print(
                    f"  [bold]Current Confidence:[/bold] {track.confidence:.2f} ({track.confidence:.0%}) | [bold]Status:[/bold] {track.fetch_status}"
                )

                if candidates:
                    console.print("  [bold yellow]Candidates:[/bold yellow]")
                    for c_idx, cand in enumerate(candidates[:3], 1):
                        c_name = str(cand.get("trackName") or cand.get("name") or "Unknown")
                        c_artist = str(cand.get("artistName") or "Unknown")
                        c_album = str(cand.get("albumName") or "N/A")
                        dur_val = cand.get("duration")
                        c_dur = format_duration(float(dur_val) if isinstance(dur_val, (int, float)) else None)
                        c_type = (
                            "Instrumental"
                            if cand.get("instrumental")
                            else (
                                "Synced"
                                if cand.get("syncedLyrics")
                                else ("Plain" if cand.get("plainLyrics") else "None")
                            )
                        )
                        console.print(f"    [{c_idx}] {c_name} by {c_artist} ({c_album}) [{c_dur}] [{c_type}]")
                        preview = ""
                        synced = cand.get("syncedLyrics")
                        plain = cand.get("plainLyrics")
                        if isinstance(synced, str) and synced:
                            lines = [
                                line.strip()
                                for line in synced.splitlines()
                                if line.strip() and not line.strip().startswith("[au:")
                            ]
                            preview = " / ".join(lines[:3])
                        elif isinstance(plain, str) and plain:
                            lines = [line.strip() for line in plain.splitlines() if line.strip()]
                            preview = " / ".join(lines[:3])
                        elif cand.get("instrumental"):
                            preview = "[Instrumental]"
                        if preview:
                            if len(preview) > 120:
                                preview = f"{preview[:120]}..."
                            console.print(f'        [dim]"{preview}"[/dim]')
                else:
                    console.print("  [dim]No candidates available.[/dim]")

            while True:
                drain_completed(wait=False)
                has_candidates = bool(candidates)
                if has_candidates:
                    choices = ["a", "s", "r", "m", "i", *map(str, range(1, len(candidates) + 1))]
                    default = "s"
                    prompt_label = "Action: [A]ccept, [S]kip, [R]eject, [M]anual search, [I]nstrumental"
                else:
                    choices = ["s", "r", "m", "i"]
                    default = "s"
                    prompt_label = "Action: [S]kip, [R]eject, [M]anual search, [I]nstrumental"

                user_input = prompt(prompt_label, choices, default)
                action = user_input.strip().lower()

                # 1. Accept
                if (action in ("a", "accept") and has_candidates) or (
                    action.isdigit() and has_candidates and 1 <= int(action) <= len(candidates)
                ):
                    chosen_cand = candidates[int(action) - 1] if action.isdigit() else candidates[0]
                    cand_id = chosen_cand.get("id")
                    track.lrclib_id = int(cand_id) if cand_id is not None else None
                    plain_val = chosen_cand.get("plainLyrics")
                    track.plain_lyrics = str(plain_val) if plain_val is not None else None
                    synced_val = chosen_cand.get("syncedLyrics")
                    track.synced_lyrics = str(synced_val) if synced_val is not None else None
                    track.is_instrumental = bool(chosen_cand.get("instrumental", False))
                    track.confidence = 1.0
                    track.fetch_status = "fetched"
                    summary.accepted += 1

                    if auto_apply and not dry_run:
                        _apply_lyrics_and_update_track(track, config)

                    if not dry_run:
                        db.upsert_track(track)
                    break

                # 2. Skip
                elif action in ("s", "skip"):
                    summary.skipped += 1
                    break

                # 3. Reject
                elif action in ("r", "reject"):
                    track.fetch_status = "not_found"
                    track.plain_lyrics = None
                    track.synced_lyrics = None
                    track.is_instrumental = False
                    track.confidence = 0.0
                    track.lrclib_id = None
                    summary.rejected += 1

                    if not dry_run:
                        db.upsert_track(track)
                    break

                # 4. Instrumental
                elif action in ("i", "instrumental"):
                    track.is_instrumental = True
                    track.plain_lyrics = None
                    track.synced_lyrics = None
                    track.confidence = 1.0
                    track.fetch_status = "fetched"
                    summary.marked_instrumental += 1

                    drain_completed(wait=False)
                    should_publish = confirm(
                        "Publish this instrumental marker to LRCLIB?",
                        True,
                    )
                    if should_publish and not dry_run:
                        fut = pool.submit(_publish_instrumental_task, track, api_client)
                        pending_publishes.append(fut)

                    if auto_apply and not dry_run:
                        _apply_lyrics_and_update_track(track, config)

                    if not dry_run:
                        db.upsert_track(track)
                    break

                # 5. Manual search
                elif action in ("m", "manual", "search"):
                    default_query = f"{track.artist_name} {track.track_name}".strip()
                    drain_completed(wait=False)
                    query = ask_input(f"Enter search query (default: {default_query})").strip()
                    if not query:
                        query = default_query

                    results: list[dict[str, Any]] = []
                    try:
                        results = api_client.search_lyrics(q=query)
                    except Exception as exc:
                        if console:
                            console.print(f"[red]Error during search: {exc}[/red]")
                        continue

                    if not results:
                        if console:
                            console.print("[yellow]No results found on LRCLIB.[/yellow]")
                        continue

                    if console:
                        console.print("\n  [bold yellow]Search Results:[/bold yellow]")
                        for r_idx, res in enumerate(results[:5], 1):
                            r_name = str(res.get("trackName") or res.get("name") or "Unknown")
                            r_artist = str(res.get("artistName") or "Unknown")
                            r_album = str(res.get("albumName") or "N/A")
                            r_dur_val = res.get("duration")
                            r_dur = format_duration(float(r_dur_val) if isinstance(r_dur_val, (int, float)) else None)
                            r_type = (
                                "Instrumental"
                                if res.get("instrumental")
                                else (
                                    "Synced"
                                    if res.get("syncedLyrics")
                                    else ("Plain" if res.get("plainLyrics") else "None")
                                )
                            )
                            console.print(f"    [{r_idx}] {r_name} by {r_artist} ({r_album}) [{r_dur}] [{r_type}]")

                    drain_completed(wait=False)
                    pick_str = (
                        ask_input(f"Select candidate [1-{min(5, len(results))}], or 0/c to cancel").strip().lower()
                    )
                    if pick_str in ("0", "c", "cancel", ""):
                        if console:
                            console.print("[dim]Manual search cancelled.[/dim]")
                        continue

                    try:
                        pick_idx = int(pick_str) - 1
                        if 0 <= pick_idx < len(results[:5]):
                            chosen = results[pick_idx]
                            chosen_id = chosen.get("id")
                            track.lrclib_id = int(chosen_id) if chosen_id is not None else None
                            chosen_plain = chosen.get("plainLyrics")
                            track.plain_lyrics = str(chosen_plain) if chosen_plain is not None else None
                            chosen_synced = chosen.get("syncedLyrics")
                            track.synced_lyrics = str(chosen_synced) if chosen_synced is not None else None
                            track.is_instrumental = bool(chosen.get("instrumental", False))
                            track.confidence = 1.0
                            track.fetch_status = "fetched"
                            track.candidates_json = json.dumps(results[:5])
                            summary.accepted += 1

                            if auto_apply and not dry_run:
                                _apply_lyrics_and_update_track(track, config)

                            if not dry_run:
                                db.upsert_track(track)
                            break
                        else:
                            if console:
                                console.print("[red]Invalid selection.[/red]")
                            continue
                    except ValueError:
                        if console:
                            console.print("[red]Invalid input.[/red]")
                        continue
                else:
                    if console:
                        console.print(f"[red]Unknown option: '{action}'[/red]")
                    continue

    finally:
        _restore_signals()
        drain_completed(wait=True)
        if own_executor:
            pool.shutdown(wait=True)
        if own_client:
            api_client.close()

    return summary
