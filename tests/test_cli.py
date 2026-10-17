"""Tests for liric CLI commands."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from liric.cli import main
from liric.config import Config, load_config, save_config
from liric.db import Database, TrackRecord


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "liric - Music library lyrics enrichment CLI tool" in result.output
    for cmd in ["init", "configure", "clean", "scan", "fetch", "apply", "watch", "run", "interactive"]:
        assert cmd in result.output


def test_cli_version(runner: CliRunner) -> None:
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_interactive_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["interactive", "--help"])
    assert result.exit_code == 0
    assert "Interactively review and resolve lyric candidate matches." in result.output
    assert "--dry-run" in result.output
    assert "--threshold" in result.output
    assert "--include-not-found" in result.output
    assert "--apply" in result.output


def test_review_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["review", "--help"])
    assert result.exit_code == 0
    assert "Review and resolve lyric candidate matches (alias of interactive)." in result.output
    assert "--dry-run" in result.output
    assert "--threshold" in result.output
    assert "--include-not-found" in result.output
    assert "--apply" in result.output


def test_interactive_cli_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    from liric.interactive import ReviewSummary

    db_file = tmp_path / "interactive_dry.db"
    cfg_file = tmp_path / "interactive_dry.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with patch("liric.cli.review_library") as mock_review:
        mock_review.return_value = ReviewSummary(
            reviewed_tracks=3,
            accepted=1,
            marked_instrumental=1,
            published_to_lrclib=0,
            skipped=1,
            rejected=0,
        )
        result = runner.invoke(main, ["-c", str(cfg_file), "interactive", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry-run mode" in result.output
    assert "Review Summary (Dry Run)" in result.output
    assert "Total Reviewed" in result.output
    assert "Accepted" in result.output
    assert "Marked Instrumental" in result.output
    assert "Skipped" in result.output
    mock_review.assert_called_once()
    _, kwargs = mock_review.call_args
    assert kwargs["dry_run"] is True
    assert kwargs["confidence_threshold"] == 0.85
    assert kwargs["include_not_found"] is True
    assert kwargs["auto_apply"] is True


def test_review_cli_options(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    from liric.interactive import ReviewSummary

    db_file = tmp_path / "review_opt.db"
    cfg_file = tmp_path / "review_opt.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with patch("liric.cli.review_library") as mock_review:
        mock_review.return_value = ReviewSummary(reviewed_tracks=0)
        result = runner.invoke(
            main,
            [
                "-c",
                str(cfg_file),
                "review",
                "-t",
                "0.75",
                "--no-include-not-found",
                "--no-apply",
            ],
        )

    assert result.exit_code == 0
    assert "Review Summary" in result.output
    mock_review.assert_called_once()
    _, kwargs = mock_review.call_args
    assert kwargs["dry_run"] is False
    assert kwargs["confidence_threshold"] == 0.75
    assert kwargs["include_not_found"] is False
    assert kwargs["auto_apply"] is False


def test_init_default(runner: CliRunner, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    result = runner.invoke(main, ["-c", str(cfg_file), "init"])
    assert result.exit_code == 0
    assert cfg_file.exists()
    assert "Default configuration created" in result.output

    cfg = load_config(cfg_file)
    assert cfg.which_lyrics == "prefer_synced"
    assert cfg.store_mode == "lrc"


def test_init_existing_without_force(runner: CliRunner, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    original = Config(music_dir="/original/music")
    save_config(original, cfg_file)

    result = runner.invoke(main, ["-c", str(cfg_file), "init"])
    assert result.exit_code == 0
    assert "already exists" in result.output

    # File should not have been overwritten
    cfg = load_config(cfg_file)
    assert cfg.music_dir == "/original/music"


def test_init_existing_with_force(runner: CliRunner, tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.yaml"
    original = Config(music_dir="/original/music")
    save_config(original, cfg_file)

    result = runner.invoke(main, ["-c", str(cfg_file), "init", "--force"])
    assert result.exit_code == 0
    assert "Default configuration created" in result.output

    cfg = load_config(cfg_file)
    assert cfg.music_dir is None


def test_configure_alias(runner: CliRunner, tmp_path: Path) -> None:
    cfg_file = tmp_path / "configured.yaml"
    result = runner.invoke(main, ["-c", str(cfg_file), "configure"])
    assert result.exit_code == 0
    assert cfg_file.exists()
    assert "Default configuration created" in result.output


def test_init_interactive(runner: CliRunner, tmp_path: Path) -> None:
    cfg_file = tmp_path / "interactive.yaml"
    user_inputs = (
        "\n".join(
            [
                str(tmp_path / "music"),  # Music library directory
                str(tmp_path / "custom.db"),  # SQLite database path
                "synced_only",  # Lyrics preference
                "both",  # Store mode
                "3",  # Parallelism
                "my_secret_key",  # API key
            ]
        )
        + "\n"
    )

    result = runner.invoke(main, ["-c", str(cfg_file), "init", "--interactive"], input=user_inputs)
    assert result.exit_code == 0
    assert "Configuration saved" in result.output
    assert cfg_file.exists()

    cfg = load_config(cfg_file)
    assert cfg.music_dir == str(tmp_path / "music")
    assert cfg.db_path == str(tmp_path / "custom.db")
    assert cfg.which_lyrics == "synced_only"
    assert cfg.store_mode == "both"
    assert cfg.fetch_parallelism == 3
    assert cfg.api_key == "my_secret_key"


def test_clean_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    db_file = tmp_path / "clean_test.db"
    cfg_file = tmp_path / "clean_config.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(TrackRecord(file_path="/song1.mp3", track_name="S1", artist_name="A1"))
        db.upsert_track(TrackRecord(file_path="/song2.mp3", track_name="S2", artist_name="A2"))

    result = runner.invoke(main, ["-c", str(cfg_file), "clean", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run: 2 track(s) would be removed" in result.output

    # Verify tracks were not deleted
    with Database(db_file) as db:
        assert db.count_tracks() == 2


def test_clean_yes(runner: CliRunner, tmp_path: Path) -> None:
    db_file = tmp_path / "clean_test.db"
    cfg_file = tmp_path / "clean_config.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(TrackRecord(file_path="/song1.mp3", track_name="S1", artist_name="A1"))
        db.upsert_track(TrackRecord(file_path="/song2.mp3", track_name="S2", artist_name="A2"))

    result = runner.invoke(main, ["-c", str(cfg_file), "clean", "--yes"])
    assert result.exit_code == 0
    assert "Successfully removed 2 track(s)" in result.output

    with Database(db_file) as db:
        assert db.count_tracks() == 0


def test_clean_prompt_confirmed(runner: CliRunner, tmp_path: Path) -> None:
    db_file = tmp_path / "clean_test.db"
    cfg_file = tmp_path / "clean_config.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(TrackRecord(file_path="/song1.mp3", track_name="S1", artist_name="A1"))

    result = runner.invoke(main, ["-c", str(cfg_file), "clean"], input="y\n")
    assert result.exit_code == 0
    assert "Successfully removed 1 track(s)" in result.output

    with Database(db_file) as db:
        assert db.count_tracks() == 0


def test_clean_prompt_declined(runner: CliRunner, tmp_path: Path) -> None:
    db_file = tmp_path / "clean_test.db"
    cfg_file = tmp_path / "clean_config.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(TrackRecord(file_path="/song1.mp3", track_name="S1", artist_name="A1"))

    result = runner.invoke(main, ["-c", str(cfg_file), "clean"], input="n\n")
    assert result.exit_code == 0
    assert "Clean operation aborted" in result.output

    with Database(db_file) as db:
        assert db.count_tracks() == 1


def test_clean_empty_database(runner: CliRunner, tmp_path: Path) -> None:
    db_file = tmp_path / "empty.db"
    cfg_file = tmp_path / "clean_config.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    result = runner.invoke(main, ["-c", str(cfg_file), "clean", "--yes"])
    assert result.exit_code == 0
    assert "Database is already empty" in result.output


def test_scan_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["scan", "--help"])
    assert result.exit_code == 0
    assert "Scan music directory for audio files and populate database." in result.output
    assert "--dry-run" in result.output


def test_scan_cli_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_scanner import create_dummy_wav

    music_dir = tmp_path / "music"
    create_dummy_wav(music_dir / "Artist - Track.wav")

    db_file = tmp_path / "scan_cli.db"
    cfg_file = tmp_path / "scan_cli.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    result = runner.invoke(main, ["-c", str(cfg_file), "scan", str(music_dir), "--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run mode" in result.output
    assert "Scan Summary (Dry Run)" in result.output
    assert "Total Scanned" in result.output

    # Database must remain untouched
    with Database(db_file) as db:
        assert db.count_tracks() == 0


def test_scan_cli_normal_run(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_scanner import create_dummy_wav

    music_dir = tmp_path / "music"
    create_dummy_wav(music_dir / "Artist - Song.wav")

    db_file = tmp_path / "scan_cli.db"
    cfg_file = tmp_path / "scan_cli.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    result = runner.invoke(main, ["-c", str(cfg_file), "scan", str(music_dir)])
    assert result.exit_code == 0
    assert "Scan Summary" in result.output
    assert "New Tracks" in result.output

    # Tracks must be stored in database
    with Database(db_file) as db:
        assert db.count_tracks() == 1
        track = db.get_all_tracks()[0]
        assert track.track_name == "Song"
        assert track.artist_name == "Artist"


def test_scan_cli_default_music_dir(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_scanner import create_dummy_wav

    music_dir = tmp_path / "default_music"
    create_dummy_wav(music_dir / "Artist - DefaultSong.wav")

    db_file = tmp_path / "default_scan.db"
    cfg_file = tmp_path / "default_scan.yaml"
    save_config(Config(db_path=str(db_file), music_dir=str(music_dir)), cfg_file)

    # Calling scan without FOLDER argument should use music_dir from config
    result = runner.invoke(main, ["-c", str(cfg_file), "scan"])
    assert result.exit_code == 0
    assert "Scan Summary" in result.output

    with Database(db_file) as db:
        assert db.count_tracks() == 1


def test_scan_cli_nonexistent_directory(runner: CliRunner, tmp_path: Path) -> None:
    nonexistent = tmp_path / "missing_folder"
    result = runner.invoke(main, ["scan", str(nonexistent)])
    assert result.exit_code == 1
    assert "does not exist or is not a directory" in result.output


def test_fetch_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["fetch", "--help"])
    assert result.exit_code == 0
    assert "Fetch lyrics from LRCLIB for tracks in the database." in result.output
    assert "--dry-run" in result.output
    assert "--force" in result.output
    assert "--limit" in result.output


def test_fetch_cli_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    db_file = tmp_path / "fetch_dry.db"
    cfg_file = tmp_path / "fetch_dry.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(TrackRecord(file_path="/song.mp3", track_name="Song", artist_name="Artist"))

    with patch("liric.fetcher.LrclibClient.get_lyrics") as mock_get:
        mock_get.return_value = {
            "id": 10,
            "trackName": "Song",
            "artistName": "Artist",
            "syncedLyrics": "[00:01] Hello",
        }
        result = runner.invoke(main, ["-c", str(cfg_file), "fetch", "--dry-run"])

    assert result.exit_code == 0
    assert "Dry-run mode" in result.output
    assert "Fetch Summary (Dry Run)" in result.output
    assert "Total Processed" in result.output
    assert "Lyrics Found" in result.output

    # Database track should remain pending in dry-run
    with Database(db_file) as db:
        track = db.get_track("/song.mp3")
        assert track is not None
        assert track.fetch_status == "pending"


def test_fetch_cli_normal_run(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    db_file = tmp_path / "fetch_normal.db"
    cfg_file = tmp_path / "fetch_normal.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(TrackRecord(file_path="/song.mp3", track_name="Song", artist_name="Artist"))

    with patch("liric.fetcher.LrclibClient.get_lyrics") as mock_get:
        mock_get.return_value = {
            "id": 20,
            "trackName": "Song",
            "artistName": "Artist",
            "syncedLyrics": "[00:01] Hello",
        }
        result = runner.invoke(main, ["-c", str(cfg_file), "fetch"])

    assert result.exit_code == 0
    assert "Fetch Summary" in result.output
    assert "Lyrics Found" in result.output

    with Database(db_file) as db:
        track = db.get_track("/song.mp3")
        assert track is not None
        assert track.fetch_status == "fetched"
        assert track.lrclib_id == 20
        assert track.synced_lyrics == "[00:01] Hello"


def test_fetch_cli_force(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    db_file = tmp_path / "fetch_force.db"
    cfg_file = tmp_path / "fetch_force.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        # Track already has not_found status
        db.upsert_track(
            TrackRecord(
                file_path="/song.mp3",
                track_name="Song",
                artist_name="Artist",
                fetch_status="not_found",
            )
        )

    with patch("liric.fetcher.LrclibClient.get_lyrics") as mock_get:
        mock_get.return_value = {
            "id": 30,
            "trackName": "Song",
            "artistName": "Artist",
            "syncedLyrics": "[00:01] Now Found",
        }
        # Run without force: skips not_found track
        res_no_force = runner.invoke(main, ["-c", str(cfg_file), "fetch"])
        assert res_no_force.exit_code == 0
        with Database(db_file) as db:
            track = db.get_track("/song.mp3")
            assert track is not None
            assert track.fetch_status == "not_found"

        # Run with force: fetches track
        res_force = runner.invoke(main, ["-c", str(cfg_file), "fetch", "--force"])
        assert res_force.exit_code == 0
        with Database(db_file) as db:
            track = db.get_track("/song.mp3")
            assert track is not None
            assert track.fetch_status == "fetched"
            assert track.lrclib_id == 30


def test_fetch_cli_limit(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    db_file = tmp_path / "fetch_limit.db"
    cfg_file = tmp_path / "fetch_limit.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with Database(db_file) as db:
        for i in range(4):
            db.upsert_track(
                TrackRecord(
                    file_path=f"/song_{i}.mp3",
                    track_name=f"Song {i}",
                    artist_name="Artist",
                )
            )

    with patch("liric.fetcher.LrclibClient.get_lyrics") as mock_get:
        mock_get.return_value = {
            "id": 1,
            "trackName": "Song",
            "artistName": "Artist",
            "syncedLyrics": "[00:01] Synced",
        }
        result = runner.invoke(main, ["-c", str(cfg_file), "fetch", "--limit", "2"])

    assert result.exit_code == 0
    with Database(db_file) as db:
        fetched_count = db.count_tracks(status="fetched")
        assert fetched_count == 2
        pending_count = db.count_tracks(status="pending")
        assert pending_count == 2


def test_apply_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["apply", "--help"])
    assert result.exit_code == 0
    assert "Apply downloaded lyrics to audio files" in result.output
    assert "--dry-run" in result.output
    assert "--force" in result.output
    assert "--limit" in result.output


def test_apply_cli_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_applier import create_dummy_mp3

    audio_file = create_dummy_mp3(tmp_path / "song.mp3")
    db_file = tmp_path / "apply_dry.db"
    cfg_file = tmp_path / "apply_dry.yaml"
    save_config(Config(db_path=str(db_file), store_mode="lrc"), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path=str(audio_file),
                track_name="Song",
                artist_name="Artist",
                fetch_status="fetched",
                synced_lyrics="[00:01.00] Synced",
            )
        )

    result = runner.invoke(main, ["-c", str(cfg_file), "apply", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run mode" in result.output
    assert "Apply Summary (Dry Run)" in result.output
    assert "Lyrics Applied" in result.output

    # Files and DB must be untouched
    assert not (tmp_path / "song.lrc").exists()
    with Database(db_file) as db:
        track = db.get_track(str(audio_file))
        assert track is not None
        assert track.applied_at is None


def test_apply_cli_normal_run(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_applier import create_dummy_mp3

    audio_file = create_dummy_mp3(tmp_path / "song.mp3")
    db_file = tmp_path / "apply_normal.db"
    cfg_file = tmp_path / "apply_normal.yaml"
    save_config(Config(db_path=str(db_file), store_mode="lrc"), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path=str(audio_file),
                track_name="Song",
                artist_name="Artist",
                fetch_status="fetched",
                synced_lyrics="[00:01.00] Synced",
            )
        )

    result = runner.invoke(main, ["-c", str(cfg_file), "apply"])
    assert result.exit_code == 0
    assert "Apply Summary" in result.output
    assert "Lyrics Applied" in result.output

    # File must be created and DB updated
    assert (tmp_path / "song.lrc").exists()
    with Database(db_file) as db:
        track = db.get_track(str(audio_file))
        assert track is not None
        assert track.applied_at is not None


def test_apply_cli_force(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_applier import create_dummy_mp3

    audio_file = create_dummy_mp3(tmp_path / "song.mp3")
    db_file = tmp_path / "apply_force.db"
    cfg_file = tmp_path / "apply_force.yaml"
    save_config(Config(db_path=str(db_file), store_mode="lrc"), cfg_file)

    with Database(db_file) as db:
        db.upsert_track(
            TrackRecord(
                file_path=str(audio_file),
                track_name="Song",
                artist_name="Artist",
                fetch_status="fetched",
                synced_lyrics="[00:01.00] Synced",
                applied_at="2026-01-01T00:00:00+00:00",
            )
        )

    # Without force: skipped
    res_no_force = runner.invoke(main, ["-c", str(cfg_file), "apply"])
    assert res_no_force.exit_code == 0
    assert "Skipped" in res_no_force.output

    # With force: applied
    res_force = runner.invoke(main, ["-c", str(cfg_file), "apply", "--force"])
    assert res_force.exit_code == 0
    assert "Lyrics Applied" in res_force.output


def test_apply_cli_limit(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_applier import create_dummy_mp3

    db_file = tmp_path / "apply_limit.db"
    cfg_file = tmp_path / "apply_limit.yaml"
    save_config(Config(db_path=str(db_file), store_mode="lrc"), cfg_file)

    with Database(db_file) as db:
        for i in range(4):
            audio_file = create_dummy_mp3(tmp_path / f"song_{i}.mp3")
            db.upsert_track(
                TrackRecord(
                    file_path=str(audio_file),
                    track_name=f"Song {i}",
                    artist_name="Artist",
                    fetch_status="fetched",
                    synced_lyrics="[00:01.00] Synced",
                )
            )

    result = runner.invoke(main, ["-c", str(cfg_file), "apply", "--limit", "2"])
    assert result.exit_code == 0
    assert "Lyrics Applied" in result.output

    with Database(db_file) as db:
        applied_count = sum(1 for t in db.get_all_tracks() if t.applied_at is not None)
        assert applied_count == 2


def test_run_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["run", "--help"])
    assert result.exit_code == 0
    assert "Run full pipeline: scan, fetch, and apply." in result.output
    assert "--dry-run" in result.output
    assert "--force" in result.output
    assert "--limit" in result.output


def test_run_cli_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    from tests.test_applier import create_dummy_mp3

    music_dir = tmp_path / "music"
    create_dummy_mp3(music_dir / "Artist - Song.mp3")

    db_file = tmp_path / "run_dry.db"
    cfg_file = tmp_path / "run_dry.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    result = runner.invoke(main, ["-c", str(cfg_file), "run", str(music_dir), "--dry-run"])
    assert result.exit_code == 0
    assert "Dry-run mode" in result.output
    assert "Scan Summary (Dry Run)" in result.output
    assert "Fetch Summary (Dry Run)" in result.output
    assert "Apply Summary (Dry Run)" in result.output

    with Database(db_file) as db:
        assert db.count_tracks() == 0


def test_run_cli_normal_run(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    from tests.test_applier import create_dummy_mp3

    music_dir = tmp_path / "music"
    create_dummy_mp3(music_dir / "Artist - RunSong.mp3")

    db_file = tmp_path / "run_normal.db"
    cfg_file = tmp_path / "run_normal.yaml"
    save_config(Config(db_path=str(db_file), store_mode="both"), cfg_file)

    with patch("liric.fetcher.LrclibClient.get_lyrics") as mock_get:
        mock_get.return_value = {
            "id": 99,
            "trackName": "RunSong",
            "artistName": "Artist",
            "syncedLyrics": "[00:01.00] Run Synced",
        }
        result = runner.invoke(main, ["-c", str(cfg_file), "run", str(music_dir)])

    assert result.exit_code == 0
    assert "Scan Summary" in result.output
    assert "Fetch Summary" in result.output
    assert "Apply Summary" in result.output
    assert "Pipeline Execution Completed" in result.output

    # Check sidecar created
    assert (music_dir / "Artist - RunSong.lrc").exists()
    with Database(db_file) as db:
        assert db.count_tracks() == 1
        track = db.get_all_tracks()[0]
        assert track.fetch_status == "fetched"
        assert track.applied_at is not None


def test_watch_cli_help(runner: CliRunner) -> None:
    result = runner.invoke(main, ["watch", "--help"])
    assert result.exit_code == 0
    assert "Watch music directory for file changes" in result.output
    assert "--dry-run" in result.output
    assert "--debounce" in result.output


def test_watch_cli_nonexistent_directory(runner: CliRunner, tmp_path: Path) -> None:
    missing = tmp_path / "missing_watch_dir"
    result = runner.invoke(main, ["watch", str(missing)])
    assert result.exit_code == 1
    assert "does not exist or is not a directory" in result.output


def test_watch_cli_dry_run_invokes_watcher(runner: CliRunner, tmp_path: Path) -> None:
    from unittest.mock import patch

    music_dir = tmp_path / "watch_music"
    music_dir.mkdir()
    db_file = tmp_path / "watch.db"
    cfg_file = tmp_path / "watch.yaml"
    save_config(Config(db_path=str(db_file)), cfg_file)

    with patch("liric.cli.watch_folder") as mock_watch:
        result = runner.invoke(main, ["-c", str(cfg_file), "watch", str(music_dir), "--dry-run", "--debounce", "2.5"])

    assert result.exit_code == 0
    assert "Dry-run mode: watching" in result.output
    assert mock_watch.called
    kwargs = mock_watch.call_args.kwargs
    assert kwargs["dry_run"] is True
    assert kwargs["debounce_seconds"] == 2.5
    assert kwargs["folder"] == music_dir.resolve()
