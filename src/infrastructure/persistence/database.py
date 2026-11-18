from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS transcription_request (
    id TEXT PRIMARY KEY,
    client_request_id TEXT UNIQUE,
    profile TEXT NOT NULL CHECK (profile IN ('rapido', 'detalhado')),
    status TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    total_items INTEGER NOT NULL DEFAULT 0,
    completed_items INTEGER NOT NULL DEFAULT 0,
    review_items INTEGER NOT NULL DEFAULT 0,
    failed_items INTEGER NOT NULL DEFAULT 0,
    total_duration_seconds REAL NOT NULL DEFAULT 0,
    estimated_duration_seconds REAL NOT NULL DEFAULT 0,
    queue_delay_seconds REAL NOT NULL DEFAULT 0,
    estimated_completion_at TEXT,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audio_transcription (
    audio_key TEXT PRIMARY KEY,
    source_call_id TEXT,
    audio_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    fast_json TEXT,
    fast_status TEXT,
    fast_completed_at TEXT,
    detailed_json TEXT,
    detailed_status TEXT,
    detailed_completed_at TEXT,
    manual_json TEXT,
    manual_version INTEGER NOT NULL DEFAULT 0,
    manual_updated_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS transcription_request_item (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES transcription_request(id),
    audio_key TEXT NOT NULL REFERENCES audio_transcription(audio_key),
    requested_profile TEXT NOT NULL,
    result_profile TEXT,
    status TEXT NOT NULL,
    reused INTEGER NOT NULL DEFAULT 0,
    progress_percent REAL NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(request_id, audio_key)
);

CREATE TABLE IF NOT EXISTS transcription_revision (
    id TEXT PRIMARY KEY,
    audio_key TEXT NOT NULL REFERENCES audio_transcription(audio_key),
    version INTEGER NOT NULL,
    edited_by TEXT NOT NULL,
    reason TEXT,
    previous_json TEXT,
    corrected_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(audio_key, version)
);

CREATE INDEX IF NOT EXISTS ix_request_item_status ON transcription_request_item(status, created_at);
CREATE INDEX IF NOT EXISTS ix_request_item_request ON transcription_request_item(request_id, status);
CREATE INDEX IF NOT EXISTS ix_revision_audio ON transcription_revision(audio_key, version);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(transcription_request)")
            }
            if "queue_delay_seconds" not in columns:
                connection.execute(
                    "ALTER TABLE transcription_request ADD COLUMN queue_delay_seconds REAL NOT NULL DEFAULT 0"
                )
            if "estimated_completion_at" not in columns:
                connection.execute(
                    "ALTER TABLE transcription_request ADD COLUMN estimated_completion_at TEXT"
                )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    @staticmethod
    def decode(value: str | None) -> Any:
        return json.loads(value) if value else None
