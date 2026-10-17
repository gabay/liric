# liric 🎵

[![Python](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](Dockerfile)
[![API](https://img.shields.io/badge/lyrics%20provider-LRCLIB-orange.svg)](https://lrclib.net/)
[![Code Style](https://img.shields.io/badge/code%20style-modern-black.svg)](https://github.com/astral-sh/uv)

**liric** is a fast, automated, and intelligent command-line lyrics enrichment tool and background daemon for your music collection.

It recursively scans your audio files, checks for existing synchronized or plain lyrics, queries the public [LRCLIB](https://lrclib.net/) database with smart fuzzy matching, and saves lyrics either directly into audio file metadata tags (ID3, Vorbis, MP4) or as `.lrc` sidecar files.

---

## Table of Contents

- [Features](#features)
- [Architecture & Workflow](#architecture--workflow)
- [Installation](#installation)
  - [Using uv (Recommended)](#using-uv-recommended)
  - [Using pip](#using-pip)
  - [Using Docker](#using-docker)
- [Configuration](#configuration)
  - [Configuration File](#configuration-file)
  - [Options Reference](#options-reference)
- [Command Reference](#command-reference)
  - [init / configure](#init--configure)
  - [scan](#scan)
  - [fetch](#fetch)
  - [apply](#apply)
  - [watch](#watch)
  - [run](#run)
  - [clean](#clean)
  - [interactive / review](#interactive--review)
- [Docker & Docker Compose](#docker--docker-compose)
  - [Running with Docker CLI](#running-with-docker-cli)
  - [Running with Docker Compose](#running-with-docker-compose)
- [Database Schema](#database-schema)
- [License](#license)

---

## Features

- **Broad Audio Format Support:** Recursively scans and reads metadata from MP3, FLAC, M4A, MP4, OGG, Opus, WAV, and AIFF files using [Mutagen](https://mutagen.readthedocs.io/).
- **Flexible Lyrics Storage:**
  - **Embedded Tags:** ID3 `USLT` (plain) and `SYLT` (synchronized) for MP3/WAV/AIFF, Vorbis `LYRICS` comments for FLAC/OGG/Opus, and MP4 `\xa9lyr` atom for M4A/MP4.
  - **Sidecar `.lrc` Files:** UTF-8 encoded `.lrc` text files placed alongside audio files.
  - **Both:** Simultaneously embed tags and write `.lrc` sidecars.
- **LRCLIB Integration & API Hygiene:**
  - Strictly complies with LRCLIB API terms of service.
  - Sets identifying `User-Agent` and `Lrclib-Client` headers.
  - Honors `Retry-After` response headers on HTTP 429 and 503 statuses.
  - Dynamic exponential backoff and jitter for transient errors.
  - Thread-safe synchronized rate-limit pauses across worker threads.
- **Persistent SQLite State:**
  - Maintains library status in an SQLite database using Write-Ahead Logging (`WAL` mode).
  - Tracks file modification times (`mtime`) and file sizes to avoid re-reading or re-fetching unchanged files.
  - Stores candidate JSON payloads, match confidences, and fetch statuses (`pending`, `fetched`, `not_found`, `manual`, `error`).
- **Real-Time Directory Watching:**
  - `watch` daemon monitors directories using [Watchdog](https://github.com/gorakhargosh/watchdog).
  - Configurable debouncing prevents redundant processing during active file writes or album transfers.
- **V2 Interactive Review & Manual Search:**
  - Identifies low-confidence matches below a configurable threshold.
  - Interactive terminal UI to inspect candidates, perform custom queries, accept alternatives, or skip.
  - Mark tracks as instrumental and optionally publish instrumental markers upstream to LRCLIB by solving cryptographic Proof-of-Work (PoW) SHA-256 challenges.
- **Rich Terminal UI:**
  - Formatted tables with color-coded status breakdowns.
  - Animated progress bars with real-time ETA via [Rich](https://github.com/Textualize/rich).
- **Universal Dry-Run Mode:**
  - Full support for `-n` / `--dry-run` across every single command to preview actions without modifying files or the database.
- **Lightweight Docker Container:**
  - Minimal Python 3.14-slim multi-stage image (~150 MB).
  - Operates as a non-root user (`appuser`, UID 1000) with volume mounts for `/music` and `/config`.

---

## Architecture & Workflow

```
+-------------------------------------------------------------+
|                        Music Library                        |
|              (MP3, FLAC, M4A, OGG, Opus, WAV)               |
+------------------------------+------------------------------+
                               |
                        [1. scan / watch]
                               v
+-------------------------------------------------------------+
|                      Local SQLite DB                        |
|                  (WAL Mode: liric.db)                       |
+------------------------------+------------------------------+
                               |
                        [2. fetch]
                               v
+-------------------------------------------------------------+
|                      LRCLIB REST API                        |
|           (/api/get, /api/search, rate-limited)             |
+------------------------------+------------------------------+
                               |
                        [3. match / review]
                               v
+-------------------------------------------------------------+
|               Best Candidate / Manual Match                 |
|             (Confidence scoring & threshold)                |
+------------------------------+------------------------------+
                               |
                        [4. apply]
                               v
+------------------------------+------------------------------+
|     Embedded Audio Tags      |     Sidecar (.lrc) Files     |
|   (ID3 USLT/SYLT, Vorbis)    |     (UTF-8 synchronized)     |
+------------------------------+------------------------------+
```

---

## Installation

### Using uv (Recommended)

[uv](https://docs.astral.sh/uv/) is an extremely fast Python package manager.

```bash
# Install tool globally
uv tool install .

# Or run directly in workspace
uv run liric --help
```

### Using pip

Ensure Python 3.14 or newer is installed:

```bash
pip install .
```

### Using Docker

Build the Docker container image locally:

```bash
docker build -t liric:latest .
```

---

## Configuration

### Configuration File

`liric` uses a YAML configuration file. By default, it is stored at:
- **Linux/macOS:** `~/.config/liric/config.yaml`
- **Docker:** `/config/config.yaml`

You can specify a custom configuration file path using the `-c` / `--config` option on any command.

### Options Reference

| Option | Type | Default | Allowed Values | Description |
| :--- | :--- | :--- | :--- | :--- |
| `db_path` | `string` | `~/.config/liric/liric.db` | Any valid path | Path to SQLite state database file. |
| `api_key` | `string` | `null` | String or `null` | Optional LRCLIB API key for elevated quotas. |
| `fetch_parallelism` | `integer`| `1` | `>= 1` | Number of concurrent worker threads used to fetch lyrics. |
| `which_lyrics` | `string` | `prefer_synced` | `synced_only`, `prefer_synced`, `prefer_plain`, `plain_only` | Strategy for selecting synchronized vs plain lyrics. |
| `store_mode` | `string` | `lrc` | `lrc`, `embed`, `both` | Method for saving lyrics (`lrc` sidecar files, `embed` inside audio tags, or `both`). |
| `music_dir` | `string` | `null` | Directory path or `null` | Default music library directory path. |

### Sample `config.yaml`

```yaml
db_path: ~/.config/liric/liric.db
api_key: null
fetch_parallelism: 2
which_lyrics: prefer_synced
store_mode: lrc
music_dir: ~/Music
```

---

## Command Reference

### `init` / `configure`

Initialize or update your configuration file.

```bash
# Create default configuration if not present
liric init

# Interactively configure settings with prompts
liric init --interactive

# Overwrite existing configuration file
liric init --force

# 'configure' is an alias for 'init'
liric configure -i
```

**Options:**
- `-i, --interactive`: Run interactive step-by-step configuration wizard.
- `-f, --force`: Overwrite existing configuration file.
- `-c, --config FILE`: Path to target configuration file.

---

### `scan`

Scan an audio directory recursively, read file tags and lyrics status, and record tracks into the SQLite database.

```bash
# Scan music directory configured in config.yaml
liric scan

# Scan a specific directory
liric scan /path/to/music

# Preview scan without writing to the database
liric scan /path/to/music --dry-run
```

**Options:**
- `-n, --dry-run`: Preview scan results without modifying the database.
- `-c, --config FILE`: Path to configuration file.

---

### `fetch`

Query the LRCLIB API for lyrics matching pending tracks in the database.

```bash
# Fetch lyrics for all pending tracks
liric fetch

# Limit number of tracks processed
liric fetch --limit 50

# Force refetching of already processed or not_found tracks
liric fetch --force

# Preview fetch operations without saving to the database
liric fetch --dry-run
```

**Options:**
- `-n, --dry-run`: Preview fetch results without modifying the database.
- `-f, --force`: Re-query LRCLIB for tracks already fetched or marked `not_found`.
- `-l, --limit INTEGER`: Maximum number of tracks to fetch.
- `-c, --config FILE`: Path to configuration file.

---

### `apply`

Write fetched lyrics from the database to your audio files or `.lrc` sidecars according to `store_mode`.

```bash
# Apply lyrics to audio files
liric apply

# Re-apply lyrics to previously processed tracks
liric apply --force

# Preview modifications without writing files or updating the database
liric apply --dry-run
```

**Options:**
- `-n, --dry-run`: Preview apply operations without modifying files or database.
- `-f, --force`: Re-apply lyrics to already applied tracks.
- `-l, --limit INTEGER`: Maximum number of tracks to apply.
- `-c, --config FILE`: Path to configuration file.

---

### `watch`

Run continuous real-time directory watcher that automatically scans, fetches, and applies lyrics when audio files are added or modified.

```bash
# Watch the default music directory
liric watch

# Watch a specific folder with custom debounce time (in seconds)
liric watch /path/to/music --debounce 2.0

# Watch in dry-run mode (log events without writing changes)
liric watch /path/to/music --dry-run
```

**Options:**
- `-n, --dry-run`: Log actions without modifying files or database.
- `--debounce FLOAT`: Delay in seconds to wait for file writing to complete (default: `1.0`).
- `-c, --config FILE`: Path to configuration file.

---

### `run`

Execute the complete end-to-end enrichment pipeline: **scan**, **fetch**, and **apply**.

```bash
# Run full pipeline on music folder
liric run /path/to/music

# Run pipeline in dry-run mode
liric run /path/to/music --dry-run

# Force refetch and reapply up to 100 tracks
liric run /path/to/music --force --limit 100
```

**Options:**
- `-n, --dry-run`: Execute full pipeline in simulation mode.
- `-f, --force`: Force refetch and reapply for all tracks.
- `-l, --limit INTEGER`: Limit number of tracks to fetch and apply.
- `-c, --config FILE`: Path to configuration file.

---

### `clean`

Purge all tracked entries from the SQLite database.

```bash
# Preview how many records would be deleted
liric clean --dry-run

# Prompt for confirmation before deleting
liric clean

# Confirm deletion non-interactively
liric clean --yes
```

**Options:**
- `-n, --dry-run`: Show how many records would be deleted without modifying the database.
- `-y, --yes`: Skip confirmation prompt.
- `-c, --config FILE`: Path to configuration file.

---

### `interactive` / `review`

Launch the terminal review interface to resolve tracks with low match confidence or not found in LRCLIB.

```bash
# Review low-confidence matches (default threshold: 0.85)
liric interactive

# Use a custom confidence threshold
liric review --threshold 0.70

# Skip tracks marked as 'not_found'
liric review --no-include-not-found

# Review matches without automatically applying lyrics
liric review --no-apply

# Dry-run review mode
liric review --dry-run
```

**Review Actions:**
- **[1..N]**: Accept candidate match #N.
- **[m]**: Manual search with custom track/artist query.
- **[i]**: Mark track as instrumental. Prompts to publish instrumental marker upstream to LRCLIB via Proof-of-Work challenge solver.
- **[s]**: Skip track for later review.
- **[r]**: Reject all candidates and mark as `not_found`.
- **[q]**: Quit review session.

**Options:**
- `-t, --threshold FLOAT`: Confidence threshold below which tracks require review (default: `0.85`).
- `--include-not-found / --no-include-not-found`: Whether to review tracks marked `not_found` (default: `True`).
- `--apply / --no-apply`: Automatically apply lyrics immediately upon accepting a match (default: `True`).
- `-n, --dry-run`: Review tracks without modifying files or database.
- `-c, --config FILE`: Path to configuration file.

---

## Docker & Docker Compose

### Running with Docker CLI

The official container image sets non-root user `appuser` (UID 1000) and exposes `/music` and `/config` mount points.

```bash
# Show help and available commands
docker run --rm liric:latest --help

# Initialize default configuration inside mounted config volume
docker run --rm \
  -v $(pwd)/config:/config \
  liric:latest init

# Run one-off pipeline on mounted music directory
docker run --rm \
  -v /path/to/your/music:/music \
  -v $(pwd)/config:/config \
  liric:latest run /music

# Run watcher daemon in the foreground
docker run --rm -it \
  -v /path/to/your/music:/music \
  -v $(pwd)/config:/config \
  liric:latest watch /music
```

### Running with Docker Compose

A production-ready `docker-compose.yml` is included in the repository:

```yaml
services:
  liric:
    build:
      context: .
      dockerfile: Dockerfile
    image: liric:latest
    container_name: liric
    restart: unless-stopped
    user: "1000:1000"
    volumes:
      - /path/to/your/music:/music
      - ./config:/config
    command: ["watch", "/music"]
```

Start the daemon in detached mode:

```bash
docker compose up -d
```

View live logs:

```bash
docker compose logs -f
```

---

## Database Schema

`liric` uses an SQLite database with WAL journaling enabled. The primary table is `tracks`:

```sql
CREATE TABLE tracks (
    file_path        TEXT PRIMARY KEY,
    track_name       TEXT NOT NULL,
    artist_name      TEXT NOT NULL,
    album_name       TEXT,
    duration         REAL,
    file_mtime       REAL,
    file_size        INTEGER,
    has_plain        INTEGER NOT NULL DEFAULT 0,
    has_synced       INTEGER NOT NULL DEFAULT 0,
    is_embedded      INTEGER NOT NULL DEFAULT 0,
    is_separate_file INTEGER NOT NULL DEFAULT 0,
    fetch_status     TEXT NOT NULL DEFAULT 'pending',
    lrclib_id        INTEGER,
    plain_lyrics     TEXT,
    synced_lyrics    TEXT,
    is_instrumental  INTEGER NOT NULL DEFAULT 0,
    candidates_json  TEXT,
    confidence       REAL NOT NULL DEFAULT 0.0,
    applied_at       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX idx_tracks_fetch_status ON tracks(fetch_status);
```

### Status Lifecycle

- `pending`: Newly scanned file awaiting lyrics lookup.
- `fetched`: Lyrics successfully found and stored in database.
- `not_found`: No candidate met confidence threshold or was found on LRCLIB.
- `manual`: Matched or marked via interactive review session.
- `error`: Network or unhandled failure during fetch.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
