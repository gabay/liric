"""Unit tests for liric.matcher candidate scoring and selection."""

import pytest

from liric.db import TrackRecord
from liric.matcher import (
    compute_duration_score,
    normalize_text,
    score_candidate,
    select_best_candidate,
    string_similarity,
)


def test_normalize_text() -> None:
    assert normalize_text("Don't Stop Me Now") == "dont stop me now"
    assert normalize_text("  Artist,   The  ") == "artist the"
    assert normalize_text("Song - Remastered (2021)") == "song remastered 2021"
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_string_similarity() -> None:
    assert string_similarity("Bohemian Rhapsody", "Bohemian Rhapsody") == 1.0
    assert string_similarity("bohemian rhapsody", "BOHEMIAN RHAPSODY") == 1.0
    assert string_similarity("Don't Stop", "Dont Stop") == 1.0
    assert string_similarity("Completely", "Different") < 0.3
    assert string_similarity("", "") == 1.0
    assert string_similarity("Title", "") == 0.0


def test_duration_score_penalties() -> None:
    # diff <= 2s
    assert compute_duration_score(200.0, 201.5) == 1.0
    assert compute_duration_score(200.0, 200.0) == 1.0

    # diff <= 5s
    assert compute_duration_score(200.0, 204.0) == 0.9

    # diff <= 10s
    assert compute_duration_score(200.0, 208.0) == 0.75

    # diff <= 20s (> 10s penalty)
    assert compute_duration_score(200.0, 215.0) == 0.4

    # diff <= 30s
    assert compute_duration_score(200.0, 225.0) == 0.2

    # diff > 30s
    assert compute_duration_score(200.0, 245.0) == 0.0

    # missing duration is neutral
    assert compute_duration_score(None, 200.0) == 1.0
    assert compute_duration_score(200.0, None) == 1.0


def test_score_candidate_exact_match() -> None:
    track = TrackRecord(
        file_path="/music/queen.mp3",
        track_name="Bohemian Rhapsody",
        artist_name="Queen",
        album_name="A Night at the Opera",
        duration=354.0,
    )
    candidate = {
        "id": 1,
        "trackName": "Bohemian Rhapsody",
        "artistName": "Queen",
        "albumName": "A Night at the Opera",
        "duration": 354,
        "syncedLyrics": "[00:10.00] Is this the real life?",
        "plainLyrics": "Is this the real life?",
        "instrumental": False,
    }

    score = score_candidate(candidate, track, which_lyrics="prefer_synced")
    assert score == 1.0


def test_duration_delta_impact_on_score() -> None:
    track = TrackRecord(
        file_path="/music/song.mp3",
        track_name="Song",
        artist_name="Artist",
        duration=200.0,
    )
    cand_perfect = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 201.0,
        "syncedLyrics": "[00:01] ...",
    }
    cand_small_diff = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 204.0,
        "syncedLyrics": "[00:01] ...",
    }
    cand_large_diff = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 218.0,
        "syncedLyrics": "[00:01] ...",
    }

    score_perfect = score_candidate(cand_perfect, track)
    score_small = score_candidate(cand_small_diff, track)
    score_large = score_candidate(cand_large_diff, track)

    assert score_perfect == 1.0
    assert score_small < score_perfect
    assert score_large < score_small
    assert score_large < 0.95


def test_which_lyrics_synced_only() -> None:
    track = TrackRecord(
        file_path="/music/song.mp3",
        track_name="Song",
        artist_name="Artist",
        duration=180.0,
    )
    cand_synced = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "syncedLyrics": "[00:01] Hello",
    }
    cand_plain = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "plainLyrics": "Hello",
    }
    cand_inst = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "instrumental": True,
    }

    assert score_candidate(cand_synced, track, which_lyrics="synced_only") == 1.0
    assert score_candidate(cand_inst, track, which_lyrics="synced_only") == 1.0
    assert score_candidate(cand_plain, track, which_lyrics="synced_only") == 0.0


def test_which_lyrics_plain_only() -> None:
    track = TrackRecord(
        file_path="/music/song.mp3",
        track_name="Song",
        artist_name="Artist",
        duration=180.0,
    )
    cand_synced = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "syncedLyrics": "[00:01] Hello",
    }
    cand_plain = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "plainLyrics": "Hello",
    }
    cand_inst = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "instrumental": True,
    }

    assert score_candidate(cand_plain, track, which_lyrics="plain_only") == 1.0
    assert score_candidate(cand_inst, track, which_lyrics="plain_only") == 1.0
    assert score_candidate(cand_synced, track, which_lyrics="plain_only") == 0.0


def test_which_lyrics_prefer_synced_and_prefer_plain() -> None:
    track = TrackRecord(
        file_path="/music/song.mp3",
        track_name="Song",
        artist_name="Artist",
        duration=180.0,
    )
    cand_synced = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "syncedLyrics": "[00:01] Hello",
    }
    cand_plain = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 180,
        "plainLyrics": "Hello",
    }

    # Under prefer_synced: synced candidate has higher score than plain
    score_s = score_candidate(cand_synced, track, which_lyrics="prefer_synced")
    score_p = score_candidate(cand_plain, track, which_lyrics="prefer_synced")
    assert score_s == 1.0
    assert score_p < score_s

    # Under prefer_plain: plain candidate has higher score than synced
    score_s2 = score_candidate(cand_synced, track, which_lyrics="prefer_plain")
    score_p2 = score_candidate(cand_plain, track, which_lyrics="prefer_plain")
    assert score_p2 == 1.0
    assert score_s2 < score_p2


def test_candidate_without_lyrics_scores_zero() -> None:
    track = TrackRecord(file_path="/song.mp3", track_name="Song", artist_name="Artist")
    cand_empty = {
        "trackName": "Song",
        "artistName": "Artist",
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": None,
    }
    assert score_candidate(cand_empty, track) == 0.0


def test_select_best_candidate() -> None:
    track = TrackRecord(
        file_path="/music/song.mp3",
        track_name="My Song",
        artist_name="The Singer",
        duration=200.0,
    )

    candidates = [
        {
            "id": 1,
            "trackName": "Another Track",
            "artistName": "The Singer",
            "duration": 200,
            "syncedLyrics": "[00:01]...",
        },
        {
            "id": 2,
            "trackName": "My Song",
            "artistName": "The Singer",
            "duration": 200,
            "syncedLyrics": "[00:01]...",
        },
        {
            "id": 3,
            "trackName": "My Song",
            "artistName": "Someone Else",
            "duration": 200,
            "syncedLyrics": "[00:01]...",
        },
    ]

    best, confidence = select_best_candidate(candidates, track, which_lyrics="prefer_synced")
    assert best is not None
    assert best["id"] == 2
    assert confidence == 1.0


def test_select_best_candidate_empty_or_no_matches() -> None:
    track = TrackRecord(file_path="/song.mp3", track_name="Song", artist_name="Artist")
    best, conf = select_best_candidate([], track)
    assert best is None
    assert conf == 0.0

    # Candidates that score 0 (e.g. no lyrics)
    no_lyrics_cand = [{"id": 99, "trackName": "Song", "artistName": "Artist"}]
    best2, conf2 = select_best_candidate(no_lyrics_cand, track)
    assert best2 is None
    assert conf2 == 0.0
