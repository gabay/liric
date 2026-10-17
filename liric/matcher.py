"""Candidate scoring and selection engine for matching LRCLIB lyrics with audio tracks."""

import difflib
import re
from typing import Any, Optional

from liric.db import TrackRecord


def normalize_text(text: Optional[str]) -> str:
    """Normalize text by lowercasing, removing apostrophes/quotes, and stripping punctuation."""
    if not text:
        return ""
    s = text.lower().strip()
    # Remove apostrophes and quotes so "Don't" matches "Dont"
    s = re.sub(r"['`’\"]", "", s)
    # Replace other punctuation with space
    s = re.sub(r"[^\w\s]", " ", s)
    # Collapse multiple whitespaces
    return " ".join(s.split())


def string_similarity(s1: Optional[str], s2: Optional[str]) -> float:
    """Compute normalized similarity ratio between two strings (0.0 to 1.0)."""
    n1 = normalize_text(s1)
    n2 = normalize_text(s2)
    if not n1 and not n2:
        return 1.0
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0

    ratio = difflib.SequenceMatcher(None, n1, n2).ratio()
    # Give prefix bonus if one starts with the other and sufficiently long
    if (n1.startswith(n2) or n2.startswith(n1)) and min(len(n1), len(n2)) >= 4:
        prefix_ratio = min(len(n1), len(n2)) / max(len(n1), len(n2))
        ratio = max(ratio, 0.85 * prefix_ratio + 0.15)
    return min(1.0, ratio)


def compute_duration_score(d1: Optional[float], d2: Optional[float]) -> float:
    """Compute score based on duration delta between track and candidate.
    
    diff <= 2s gives high score (1.0).
    diff > 10s receives a penalty.
    """
    if d1 is None or d2 is None or d1 <= 0 or d2 <= 0:
        return 1.0
    diff = abs(d1 - d2)
    if diff <= 2.0:
        return 1.0
    elif diff <= 5.0:
        return 0.9
    elif diff <= 10.0:
        return 0.75
    elif diff <= 20.0:
        return 0.4
    elif diff <= 30.0:
        return 0.2
    else:
        return 0.0


def score_candidate(
    candidate: dict[str, Any],
    track: TrackRecord,
    which_lyrics: str = "prefer_synced",
) -> float:
    """Score a candidate track against a TrackRecord based on metadata and lyrics preference.
    
    Returns confidence score between 0.0 and 1.0.
    """
    has_synced = bool(candidate.get("syncedLyrics"))
    has_plain = bool(candidate.get("plainLyrics"))
    is_instrumental = bool(candidate.get("instrumental", False))

    # Reject candidates with no lyrics content at all
    if not has_synced and not has_plain and not is_instrumental:
        return 0.0

    # Evaluate which_lyrics preference requirements
    if which_lyrics == "synced_only":
        if not (has_synced or is_instrumental):
            return 0.0
        lyrics_multiplier = 1.0
    elif which_lyrics == "plain_only":
        if not (has_plain or is_instrumental):
            return 0.0
        lyrics_multiplier = 1.0
    elif which_lyrics == "prefer_synced":
        if has_synced or is_instrumental:
            lyrics_multiplier = 1.0
        else:
            lyrics_multiplier = 0.85
    elif which_lyrics == "prefer_plain":
        if has_plain or is_instrumental:
            lyrics_multiplier = 1.0
        else:
            lyrics_multiplier = 0.85
    else:
        lyrics_multiplier = 1.0

    cand_title = candidate.get("trackName") or candidate.get("name") or ""
    cand_artist = candidate.get("artistName") or ""
    cand_album = candidate.get("albumName")
    raw_duration = candidate.get("duration")
    cand_duration = float(raw_duration) if raw_duration is not None else None

    sim_title = string_similarity(track.track_name, cand_title)
    sim_artist = string_similarity(track.artist_name, cand_artist)
    sim_duration = compute_duration_score(track.duration, cand_duration)

    if track.album_name and cand_album:
        sim_album = string_similarity(track.album_name, cand_album)
        base_score = (
            0.45 * sim_title
            + 0.35 * sim_artist
            + 0.15 * sim_duration
            + 0.05 * sim_album
        )
    else:
        base_score = (
            0.50 * sim_title
            + 0.35 * sim_artist
            + 0.15 * sim_duration
        )

    # Penalize if title or artist is a strong mismatch
    if sim_title < 0.2 or sim_artist < 0.2:
        base_score *= 0.5

    final_score = base_score * lyrics_multiplier
    return round(max(0.0, min(1.0, final_score)), 4)


def select_best_candidate(
    candidates: list[dict[str, Any]],
    track: TrackRecord,
    which_lyrics: str = "prefer_synced",
) -> tuple[Optional[dict[str, Any]], float]:
    """Select candidate with highest confidence score for the given track.
    
    Returns (best_candidate, confidence). If no suitable candidate is found, returns (None, 0.0).
    """
    if not candidates:
        return None, 0.0

    best_cand: Optional[dict[str, Any]] = None
    best_score: float = -1.0

    for cand in candidates:
        score = score_candidate(cand, track, which_lyrics=which_lyrics)
        if score > best_score:
            best_score = score
            best_cand = cand

    if best_cand is None or best_score <= 0.0:
        return None, 0.0

    return best_cand, best_score
