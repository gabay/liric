"""Tests for liric CLI commands."""

from pathlib import Path
from click.testing import CliRunner
import pytest

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


@pytest.mark.parametrize(
    "cmd",
    ["fetch", "apply", "watch", "run", "interactive", "review"],
)
def test_placeholder_commands(runner: CliRunner, cmd: str) -> None:
    result = runner.invoke(main, [cmd])
    assert result.exit_code == 0
    assert "not yet implemented" in result.output

    # Also test --help for each placeholder command
    help_result = runner.invoke(main, [cmd, "--help"])
    assert help_result.exit_code == 0
    assert "Usage: liric" in help_result.output


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
    user_inputs = "\n".join([
        str(tmp_path / "music"),      # Music library directory
        str(tmp_path / "custom.db"),  # SQLite database path
        "synced_only",                # Lyrics preference
        "both",                       # Store mode
        "3",                          # Parallelism
        "my_secret_key",              # API key
    ]) + "\n"

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
