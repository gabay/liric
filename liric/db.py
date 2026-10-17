"""SQLite database manager for liric track metadata and lyrics state."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import threading
from typing import Any

ALLOWED_FETCH_STATUSES = ("pending", "fetched", "not_found", "manual", "error")


@dataclass
class TrackRecord:
    """Track metadata and lyrics state record."""

    file_path: str
    track_name: str
    artist_name: str
    album_name: str | None = None
    duration: float | None = None
    file_mtime: float | None = None
    file_size: int | None = None
    has_plain: bool = False
    has_synced: bool = False
    is_embedded: bool = False
    is_separate_file: bool = False
    fetch_status: str = "pending"
    lrclib_id: int | None = None
    plain_lyrics: str | None = None
    synced_lyrics: str | None = None
    is_instrumental: bool = False
    candidates_json: str | None = None
    confidence: float = 0.0
    applied_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def __post_init__(self) -> None:
        if self.fetch_status not in ALLOWED_FETCH_STATUSES:
            raise ValueError(
                f"Invalid fetch_status: '{self.fetch_status}'. Allowed: {', '.join(ALLOWED_FETCH_STATUSES)}"
            )
        now = datetime.now(timezone.utc).isoformat()
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = self.created_at

    def to_dict(self) -> dict[str, Any]:
        """Convert TrackRecord to plain dictionary."""
        return asdict(self)


class Database:
    """SQLite Database manager for liric."""

    db_path: str | Path
    _lock: threading.Lock
    _conn: sqlite3.Connection

    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        if str(db_path) == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(db_path).expanduser().resolve()
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        target = str(self.db_path)
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tracks (
                    file_path TEXT PRIMARY KEY,
                    track_name TEXT NOT NULL,
                    artist_name TEXT NOT NULL,
                    album_name TEXT,
                    duration REAL,
                    file_mtime REAL,
                    file_size INTEGER,
                    has_plain INTEGER NOT NULL DEFAULT 0,
                    has_synced INTEGER NOT NULL DEFAULT 0,
                    is_embedded INTEGER NOT NULL DEFAULT 0,
                    is_separate_file INTEGER NOT NULL DEFAULT 0,
                    fetch_status TEXT NOT NULL DEFAULT 'pending',
                    lrclib_id INTEGER,
                    plain_lyrics TEXT,
                    synced_lyrics TEXT,
                    is_instrumental INTEGER NOT NULL DEFAULT 0,
                    candidates_json TEXT,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    applied_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_tracks_fetch_status ON tracks(fetch_status);
                """
            )
            self._conn.commit()

    @staticmethod
    def _row_to_track(row: sqlite3.Row) -> TrackRecord:
        return TrackRecord(
            file_path=str(row["file_path"]),
            track_name=str(row["track_name"]),
            artist_name=str(row["artist_name"]),
            album_name=str(row["album_name"]) if row["album_name"] is not None else None,
            duration=float(row["duration"]) if row["duration"] is not None else None,
            file_mtime=float(row["file_mtime"]) if row["file_mtime"] is not None else None,
            file_size=int(row["file_size"]) if row["file_size"] is not None else None,
            has_plain=bool(row["has_plain"]),
            has_synced=bool(row["has_synced"]),
            is_embedded=bool(row["is_embedded"]),
            is_separate_file=bool(row["is_separate_file"]),
            fetch_status=str(row["fetch_status"]),
            lrclib_id=int(row["lrclib_id"]) if row["lrclib_id"] is not None else None,
            plain_lyrics=str(row["plain_lyrics"]) if row["plain_lyrics"] is not None else None,
            synced_lyrics=str(row["synced_lyrics"]) if row["synced_lyrics"] is not None else None,
            is_instrumental=bool(row["is_instrumental"]),
            candidates_json=str(row["candidates_json"]) if row["candidates_json"] is not None else None,
            confidence=float(row["confidence"]),
            applied_at=str(row["applied_at"]) if row["applied_at"] is not None else None,
            created_at=str(row["created_at"]) if row["created_at"] is not None else None,
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )

    def upsert_track(self, track: TrackRecord) -> None:
        """Insert or update a track record."""
        track.updated_at = datetime.now(timezone.utc).isoformat()
        if track.created_at is None:
            track.created_at = track.updated_at

        sql = """
            INSERT INTO tracks (
                file_path, track_name, artist_name, album_name, duration,
                file_mtime, file_size, has_plain, has_synced, is_embedded,
                is_separate_file, fetch_status, lrclib_id, plain_lyrics,
                synced_lyrics, is_instrumental, candidates_json, confidence,
                applied_at, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(file_path) DO UPDATE SET
                track_name = excluded.track_name,
                artist_name = excluded.artist_name,
                album_name = excluded.album_name,
                duration = excluded.duration,
                file_mtime = excluded.file_mtime,
                file_size = excluded.file_size,
                has_plain = excluded.has_plain,
                has_synced = excluded.has_synced,
                is_embedded = excluded.is_embedded,
                is_separate_file = excluded.is_separate_file,
                fetch_status = excluded.fetch_status,
                lrclib_id = excluded.lrclib_id,
                plain_lyrics = excluded.plain_lyrics,
                synced_lyrics = excluded.synced_lyrics,
                is_instrumental = excluded.is_instrumental,
                candidates_json = excluded.candidates_json,
                confidence = excluded.confidence,
                applied_at = excluded.applied_at,
                updated_at = excluded.updated_at;
        """
        params = (
            track.file_path,
            track.track_name,
            track.artist_name,
            track.album_name,
            track.duration,
            track.file_mtime,
            track.file_size,
            int(track.has_plain),
            int(track.has_synced),
            int(track.is_embedded),
            int(track.is_separate_file),
            track.fetch_status,
            track.lrclib_id,
            track.plain_lyrics,
            track.synced_lyrics,
            int(track.is_instrumental),
            track.candidates_json,
            track.confidence,
            track.applied_at,
            track.created_at,
            track.updated_at,
        )
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def get_track(self, file_path: str) -> TrackRecord | None:
        """Fetch a single track record by file path."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM tracks WHERE file_path = ?", (file_path,))
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_track(row)

    def get_tracks_by_status(self, status: str) -> list[TrackRecord]:
        """Fetch all track records with a given fetch status."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT * FROM tracks WHERE fetch_status = ? ORDER BY file_path",
                (status,),
            )
            return [self._row_to_track(row) for row in cursor.fetchall()]

    def get_all_tracks(self) -> list[TrackRecord]:
        """Fetch all track records."""
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT * FROM tracks ORDER BY file_path")
            return [self._row_to_track(row) for row in cursor.fetchall()]

    def count_tracks(self, status: str | None = None) -> int:
        """Count total tracks, optionally filtered by status."""
        with self._lock:
            cursor = self._conn.cursor()
            if status is not None:
                cursor.execute(
                    "SELECT COUNT(*) FROM tracks WHERE fetch_status = ?", (status,)
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM tracks")
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def clean(self, dry_run: bool = False) -> int:
        """Remove all records from db or resets state, returns count of deleted tracks."""
        count = self.count_tracks()
        if not dry_run and count > 0:
            with self._lock:
                self._conn.execute("DELETE FROM tracks;")
                self._conn.commit()
        return count

    def close(self) -> None:
        """Close the SQLite database connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
