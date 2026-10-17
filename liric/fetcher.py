"""Lyrics fetcher engine for searching, matching, and enriching track records from LRCLIB."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import json
from typing import Any, Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from liric.config import Config
from liric.db import Database, TrackRecord
from liric.lrclib import LrclibClient
from liric.matcher import score_candidate


@dataclass
class FetchSummary:
    """Summary of lyrics fetch operation results."""

    total_processed: int = 0
    found: int = 0
    not_found: int = 0
    errors: int = 0
    skipped: int = 0


def track_has_required_lyrics(track: TrackRecord, which_lyrics: str) -> bool:
    """Check if track already possesses the lyrics required by config."""
    if which_lyrics in ("synced_only", "prefer_synced"):
        return track.has_synced
    elif which_lyrics in ("plain_only", "prefer_plain"):
        return track.has_plain
    return False


def _has_preferred_lyrics(cand: dict[str, Any], which_lyrics: str) -> bool:
    """Check if a candidate has the user's preferred lyrics type."""
    if bool(cand.get("instrumental", False)):
        return True
    has_synced = bool(cand.get("syncedLyrics"))
    has_plain = bool(cand.get("plainLyrics"))
    if which_lyrics == "synced_only":
        return has_synced
    if which_lyrics == "plain_only":
        return has_plain
    if which_lyrics == "prefer_synced":
        return has_synced
    if which_lyrics == "prefer_plain":
        return has_plain
    return True


def _process_track(
    track: TrackRecord,
    client: LrclibClient,
    config: Config,
) -> tuple[TrackRecord, str]:
    """Fetch and score lyrics candidates for a single track.
    
    Returns (track, status) where status is 'found', 'not_found', or 'error'.
    """
    try:
        exact_candidate: Optional[dict[str, Any]] = None
        exact_failed = False
        try:
            exact_candidate = client.get_lyrics(
                track_name=track.track_name,
                artist_name=track.artist_name,
                album_name=track.album_name,
                duration=track.duration,
            )
        except Exception:
            exact_failed = True

        candidates: list[dict[str, Any]] = []
        if exact_candidate is not None:
            candidates.append(exact_candidate)

        # If exact lookup failed or lacks preferred lyrics type, search
        if exact_candidate is None or not _has_preferred_lyrics(exact_candidate, config.which_lyrics):
            try:
                search_results = client.search_lyrics(
                    track_name=track.track_name,
                    artist_name=track.artist_name,
                    album_name=track.album_name,
                )
                existing_ids = {c.get("id") for c in candidates if c.get("id") is not None}
                for sr in search_results:
                    if sr.get("id") is None or sr.get("id") not in existing_ids:
                        candidates.append(sr)
                        if sr.get("id") is not None:
                            existing_ids.add(sr.get("id"))
            except Exception:
                if exact_failed:
                    track.fetch_status = "error"
                    return track, "error"

        if not candidates:
            if exact_failed:
                track.fetch_status = "error"
                return track, "error"
            track.fetch_status = "not_found"
            track.confidence = 0.0
            track.candidates_json = None
            return track, "not_found"

        # Score and rank candidates
        scored: list[tuple[dict[str, Any], float]] = []
        for c in candidates:
            score = score_candidate(c, track, which_lyrics=config.which_lyrics)
            scored.append((c, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        top_5 = [c for c, _ in scored[:5]]
        track.candidates_json = json.dumps(top_5)

        best_cand, best_score = scored[0]
        # Match threshold: 0.40
        if best_cand is not None and best_score >= 0.40:
            track.fetch_status = "fetched"
            track.lrclib_id = best_cand.get("id")
            track.plain_lyrics = best_cand.get("plainLyrics")
            track.synced_lyrics = best_cand.get("syncedLyrics")
            track.is_instrumental = bool(best_cand.get("instrumental", False))
            track.confidence = best_score
            return track, "found"
        else:
            track.fetch_status = "not_found"
            track.confidence = best_score
            return track, "not_found"

    except Exception:
        track.fetch_status = "error"
        return track, "error"


def fetch_lyrics_for_library(
    db: Database,
    config: Config,
    dry_run: bool = False,
    force: bool = False,
    limit: Optional[int] = None,
    console: Optional[Console] = None,
    show_progress: bool = True,
    client: Optional[LrclibClient] = None,
) -> FetchSummary:
    """Fetch lyrics for tracks in library matching selection criteria."""
    if force:
        candidates = db.get_all_tracks()
    else:
        pending = db.get_tracks_by_status("pending")
        errors = db.get_tracks_by_status("error")
        candidates = pending + errors

    target_tracks: list[TrackRecord] = []
    skipped_count = 0

    for tr in candidates:
        if track_has_required_lyrics(tr, config.which_lyrics):
            skipped_count += 1
        else:
            target_tracks.append(tr)

    if limit is not None and limit >= 0:
        target_tracks = target_tracks[:limit]

    summary = FetchSummary(skipped=skipped_count)
    if not target_tracks:
        return summary

    own_client = client is None
    api_client = client or LrclibClient(api_key=config.api_key)

    try:
        workers = max(1, config.fetch_parallelism)

        def _run_worker(tr: TrackRecord) -> tuple[TrackRecord, str]:
            return _process_track(tr, api_client, config)

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
                    f"[cyan]Fetching lyrics ({len(target_tracks)} tracks)...",
                    total=len(target_tracks),
                )
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    future_to_track = {
                        executor.submit(_run_worker, track): track for track in target_tracks
                    }
                    for future in as_completed(future_to_track):
                        try:
                            processed_track, status = future.result()
                        except Exception:
                            processed_track = future_to_track[future]
                            processed_track.fetch_status = "error"
                            status = "error"

                        if not dry_run:
                            db.upsert_track(processed_track)

                        summary.total_processed += 1
                        if status == "found":
                            summary.found += 1
                        elif status == "not_found":
                            summary.not_found += 1
                        elif status == "error":
                            summary.errors += 1

                        progress.advance(task_id)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_track = {
                    executor.submit(_run_worker, track): track for track in target_tracks
                }
                for future in as_completed(future_to_track):
                    try:
                        processed_track, status = future.result()
                    except Exception:
                        processed_track = future_to_track[future]
                        processed_track.fetch_status = "error"
                        status = "error"

                    if not dry_run:
                        db.upsert_track(processed_track)

                    summary.total_processed += 1
                    if status == "found":
                        summary.found += 1
                    elif status == "not_found":
                        summary.not_found += 1
                    elif status == "error":
                        summary.errors += 1

    finally:
        if own_client:
            api_client.close()

    return summary
