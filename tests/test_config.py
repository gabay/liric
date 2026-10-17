"""Tests for configuration management."""

from pathlib import Path

import pytest
import yaml

from liric.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_DB_PATH,
    Config,
    create_default_config,
    expand_path,
    interactive_configure,
    load_config,
    resolve_config_path,
    save_config,
)


def test_default_config() -> None:
    config = Config()
    assert config.db_path == DEFAULT_DB_PATH
    assert config.app_id is None
    assert config.api_key is None
    assert config.fetch_parallelism == 1
    assert config.which_lyrics == "prefer_synced"
    assert config.store_mode == "lrc"
    assert config.music_dir is None


def test_custom_config() -> None:
    config = Config(
        db_path="/custom/path/liric.db",
        app_id="test_app_id",
        api_key="test_api_key",
        fetch_parallelism=4,
        which_lyrics="synced_only",
        store_mode="both",
        music_dir="/media/music",
    )
    assert config.db_path == "/custom/path/liric.db"
    assert config.app_id == "test_app_id"
    assert config.api_key == "test_api_key"
    assert config.fetch_parallelism == 4
    assert config.which_lyrics == "synced_only"
    assert config.store_mode == "both"
    assert config.music_dir == "/media/music"


@pytest.mark.parametrize("invalid_lyrics", ["invalid", "", "all"])
def test_invalid_which_lyrics(invalid_lyrics: str) -> None:
    with pytest.raises(ValueError, match="Invalid which_lyrics"):
        Config(which_lyrics=invalid_lyrics)


@pytest.mark.parametrize("invalid_mode", ["invalid", "file", ""])
def test_invalid_store_mode(invalid_mode: str) -> None:
    with pytest.raises(ValueError, match="Invalid store_mode"):
        Config(store_mode=invalid_mode)


@pytest.mark.parametrize("invalid_parallelism", [0, -1, -5])
def test_invalid_fetch_parallelism(invalid_parallelism: int) -> None:
    with pytest.raises(ValueError, match="fetch_parallelism must be a positive integer"):
        Config(fetch_parallelism=invalid_parallelism)


def test_path_expansion() -> None:
    expanded = expand_path("~/music/library")
    assert str(expanded).startswith("/")
    assert "~" not in str(expanded)

    config = Config(
        db_path="~/liric_test.db",
        music_dir="~/Music",
    )
    assert str(config.expanded_db_path).startswith("/")
    assert "~" not in str(config.expanded_db_path)
    assert config.expanded_music_dir is not None
    assert str(config.expanded_music_dir).startswith("/")
    assert "~" not in str(config.expanded_music_dir)

    empty_music_config = Config(music_dir=None)
    assert empty_music_config.expanded_music_dir is None


def test_resolve_config_path() -> None:
    resolved_default = resolve_config_path(None)
    assert resolved_default == Path(DEFAULT_CONFIG_PATH).expanduser().resolve()

    resolved_custom = resolve_config_path("~/myconfig.yaml")
    assert resolved_custom == Path("~/myconfig.yaml").expanduser().resolve()


def test_save_and_load_config(tmp_path: Path) -> None:
    config_file = tmp_path / "subdir" / "config.yaml"
    original = Config(
        db_path=str(tmp_path / "test.db"),
        app_id="lrclib_app_id",
        api_key="lrclib_secret",
        fetch_parallelism=2,
        which_lyrics="prefer_plain",
        store_mode="embed",
        music_dir=str(tmp_path / "music"),
    )

    saved_path = save_config(original, config_file)
    assert saved_path == config_file
    assert config_file.exists()

    with open(config_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert data["app_id"] == "lrclib_app_id"
    assert data["api_key"] == "lrclib_secret"
    assert data["which_lyrics"] == "prefer_plain"

    loaded = load_config(config_file)
    assert loaded == original


def test_load_nonexistent_config_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(FileNotFoundError):
        load_config(missing)


def test_create_default_config(tmp_path: Path) -> None:
    config_file = tmp_path / "default_config.yaml"
    cfg = create_default_config(config_file)
    assert config_file.exists()
    assert cfg.db_path == DEFAULT_DB_PATH
    assert cfg.which_lyrics == "prefer_synced"

    loaded = load_config(config_file)
    assert loaded == cfg


def test_interactive_configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "interactive_config.yaml"

    prompts = {
        "Music library directory": str(tmp_path / "my_music"),
        "SQLite database path": str(tmp_path / "custom.db"),
        "Lyrics preference": "synced_only",
        "Store mode": "both",
        "Fetch parallelism (concurrency)": 3,
        "LRCLIB App ID (optional, press Enter to skip)": "test_app_id_123",
        "LRCLIB API key (optional, press Enter to skip)": "test_api_key_123",
    }

    def fake_prompt(text: str, default=None, **kwargs):
        return prompts.get(text, default)

    monkeypatch.setattr("click.prompt", fake_prompt)

    config = interactive_configure(config_file)
    assert config_file.exists()
    assert config.music_dir == str(tmp_path / "my_music")
    assert config.db_path == str(tmp_path / "custom.db")
    assert config.which_lyrics == "synced_only"
    assert config.store_mode == "both"
    assert config.fetch_parallelism == 3
    assert config.app_id == "test_app_id_123"
    assert config.api_key == "test_api_key_123"
