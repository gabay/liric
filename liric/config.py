"""Configuration management for liric."""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import click
import yaml

DEFAULT_CONFIG_PATH = Path("~/.config/liric/config.yaml")
DEFAULT_DB_PATH = "~/.config/liric/liric.db"

ALLOWED_WHICH_LYRICS = ("synced_only", "prefer_synced", "prefer_plain", "plain_only")
ALLOWED_STORE_MODES = ("lrc", "embed", "both")


def expand_path(p: str | Path) -> Path:
    """Expand user tilde and resolve to absolute path."""
    return Path(p).expanduser().resolve()


def resolve_config_path(path: str | Path | None = None) -> Path:
    """Resolve config path, falling back to default config path if None."""
    if path is None:
        return Path(DEFAULT_CONFIG_PATH).expanduser().resolve()
    return Path(path).expanduser().resolve()


@dataclass
class Config:
    """Configuration dataclass for liric."""

    db_path: str = DEFAULT_DB_PATH
    app_id: str | None = None
    api_key: str | None = None
    fetch_parallelism: int = 1
    which_lyrics: str = "prefer_synced"
    store_mode: str = "lrc"
    music_dir: str | None = None

    def __post_init__(self) -> None:
        if self.which_lyrics not in ALLOWED_WHICH_LYRICS:
            raise ValueError(f"Invalid which_lyrics: '{self.which_lyrics}'. Allowed: {', '.join(ALLOWED_WHICH_LYRICS)}")
        if self.store_mode not in ALLOWED_STORE_MODES:
            raise ValueError(f"Invalid store_mode: '{self.store_mode}'. Allowed: {', '.join(ALLOWED_STORE_MODES)}")
        if not isinstance(self.fetch_parallelism, int) or self.fetch_parallelism < 1:
            raise ValueError(f"fetch_parallelism must be a positive integer, got: {self.fetch_parallelism}")

    @property
    def expanded_db_path(self) -> Path:
        """Return expanded database Path."""
        return expand_path(self.db_path)

    @property
    def expanded_music_dir(self) -> Path | None:
        """Return expanded music directory Path, if set."""
        return expand_path(self.music_dir) if self.music_dir else None

    def to_dict(self) -> dict[str, Any]:
        """Convert Config to a plain dictionary."""
        return asdict(self)


def save_config(config: Config, path: str | Path | None = None) -> Path:
    """Save config to a YAML file, creating parent directories if needed."""
    target_path = resolve_config_path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.to_dict()
    with open(target_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    return target_path


def load_config(path: str | Path | None = None) -> Config:
    """Load config from a YAML file. Raises FileNotFoundError if missing."""
    target_path = resolve_config_path(path)
    if not target_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {target_path}")
    with open(target_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        data = {}
    valid_keys = {
        "db_path",
        "app_id",
        "api_key",
        "fetch_parallelism",
        "which_lyrics",
        "store_mode",
        "music_dir",
    }
    filtered: dict[str, Any] = {k: v for k, v in data.items() if k in valid_keys}
    return Config(**filtered)


def create_default_config(path: str | Path | None = None) -> Config:
    """Create default configuration file and return Config instance."""
    config = Config()
    save_config(config, path)
    return config


def interactive_configure(path: str | Path | None = None) -> Config:
    """Interactively prompt user for configuration values and save them."""
    target_path = resolve_config_path(path)
    current = Config()
    if target_path.exists():
        try:
            current = load_config(target_path)
        except Exception:
            pass

    music_dir_prompt: str = click.prompt(
        "Music library directory",
        default=current.music_dir or "",
        show_default=bool(current.music_dir),
        type=str,
    )
    music_dir = music_dir_prompt.strip() or None

    db_path: str = click.prompt(
        "SQLite database path",
        default=current.db_path,
        type=str,
    )

    which_lyrics: str = click.prompt(
        "Lyrics preference",
        default=current.which_lyrics,
        type=click.Choice(list(ALLOWED_WHICH_LYRICS), case_sensitive=False),
    )

    store_mode: str = click.prompt(
        "Store mode",
        default=current.store_mode,
        type=click.Choice(list(ALLOWED_STORE_MODES), case_sensitive=False),
    )

    fetch_parallelism: int = click.prompt(
        "Fetch parallelism (concurrency)",
        default=current.fetch_parallelism,
        type=int,
    )

    app_id_prompt: str = click.prompt(
        "LRCLIB App ID (optional, press Enter to skip)",
        default=current.app_id or "",
        show_default=False,
        type=str,
    )
    app_id = app_id_prompt.strip() or None

    api_key_prompt: str = click.prompt(
        "LRCLIB API key (optional, press Enter to skip)",
        default=current.api_key or "",
        show_default=False,
        type=str,
    )
    api_key = api_key_prompt.strip() or None

    config = Config(
        db_path=db_path.strip(),
        app_id=app_id,
        api_key=api_key,
        fetch_parallelism=fetch_parallelism,
        which_lyrics=which_lyrics,
        store_mode=store_mode,
        music_dir=music_dir,
    )
    save_config(config, target_path)
    return config
