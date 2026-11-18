from __future__ import annotations

import json
import uuid
from typing import Any

from src.infrastructure.persistence.database import Database, utc_now


class TranscriptionRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def create_request(
        self,
        request_id: str,
        client_request_id: str | None,
        profile: str,
        filters: dict[str, Any],
        calls: list[dict[str, Any]],
        estimated_duration_seconds: float,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                """
                INSERT INTO transcription_request (
                    id, client_request_id, profile, status, filters_json,
                    total_items, total_duration_seconds, estimated_duration_seconds,
                    requested_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    client_request_id,
                    profile,
                    json.dumps(filters, ensure_ascii=False),
                    len(calls),
                    sum(call["duration_seconds"] for call in calls),
                    estimated_duration_seconds,
                    now,
                    now,
                ),
            )
            for call in calls:
                connection.execute(
                    """
                    INSERT INTO audio_transcription (
                        audio_key, source_call_id, audio_path, file_name,
                        duration_seconds, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(audio_key) DO UPDATE SET
                        source_call_id=excluded.source_call_id,
                        audio_path=excluded.audio_path,
                        file_name=excluded.file_name,
                        duration_seconds=excluded.duration_seconds,
                        updated_at=excluded.updated_at
                    """,
                    (
                        call["audio_key"],
                        call.get("source_call_id"),
                        call["audio_path"],
                        call["file_name"],
                        call["duration_seconds"],
                        now,
                    ),
                )
                cached = connection.execute(
                    """
                    SELECT fast_json, detailed_json FROM audio_transcription
                    WHERE audio_key = ?
                    """,
                    (call["audio_key"],),
                ).fetchone()
                result_profile = None
                if profile == "rapido" and cached["detailed_json"]:
                    result_profile = "detalhado"
                elif profile == "rapido" and cached["fast_json"]:
                    result_profile = "rapido"
                elif profile == "detalhado" and cached["detailed_json"]:
                    result_profile = "detalhado"
                reused = result_profile is not None
                connection.execute(
                    """
                    INSERT INTO transcription_request_item (
                        id, request_id, audio_key, requested_profile, result_profile,
                        status, reused, progress_percent, completed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        request_id,
                        call["audio_key"],
                        profile,
                        result_profile,
                        "completed" if reused else "queued",
                        1 if reused else 0,
                        100 if reused else 0,
                        now if reused else None,
                        now,
                        now,
                    ),
                )
            pending_duration = connection.execute(
                """
                SELECT COALESCE(SUM(a.duration_seconds), 0) duration
                FROM transcription_request_item i
                JOIN audio_transcription a ON a.audio_key=i.audio_key
                WHERE i.request_id=? AND i.status='queued'
                """,
                (request_id,),
            ).fetchone()["duration"]
            total_duration = sum(call["duration_seconds"] for call in calls)
            adjusted_estimate = (
                estimated_duration_seconds * float(pending_duration) / total_duration
                if total_duration
                else 0.0
            )
            connection.execute(
                """
                UPDATE transcription_request SET estimated_duration_seconds=?, updated_at=?
                WHERE id=?
                """,
                (adjusted_estimate, now, request_id),
            )
            self._refresh_request(connection, request_id)
        return self.get_request(request_id) or {}

    def find_by_client_request_id(self, client_request_id: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM transcription_request WHERE client_request_id = ?",
                (client_request_id,),
            ).fetchone()
        return self._request_payload(row)

    def estimate_queue_delay(self) -> float:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT estimated_duration_seconds, total_items,
                       completed_items, failed_items
                FROM transcription_request
                WHERE status IN ('queued', 'processing')
                """
            ).fetchall()
        delay = 0.0
        for row in rows:
            total = max(1, int(row["total_items"]))
            finished = int(row["completed_items"]) + int(row["failed_items"])
            remaining_ratio = max(0.0, 1.0 - finished / total)
            delay += float(row["estimated_duration_seconds"]) * remaining_ratio
        return delay

    def update_request_estimate(
        self,
        request_id: str,
        queue_delay_seconds: float,
        estimated_completion_at: str,
    ) -> None:
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE transcription_request
                SET queue_delay_seconds=?, estimated_completion_at=?, updated_at=?
                WHERE id=?
                """,
                (queue_delay_seconds, estimated_completion_at, utc_now(), request_id),
            )

    def get_request(self, request_id: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM transcription_request WHERE id = ?",
                (request_id,),
            ).fetchone()
        return self._request_payload(row)

    def list_items(self, request_id: str, offset: int, limit: int) -> list[dict[str, Any]]:
        with self.database.connection() as connection:
            rows = connection.execute(
                """
                SELECT i.*, a.file_name, a.audio_path, a.duration_seconds,
                       a.fast_status, a.detailed_status, a.manual_version
                FROM transcription_request_item i
                JOIN audio_transcription a ON a.audio_key = i.audio_key
                WHERE i.request_id = ? ORDER BY i.created_at, i.id LIMIT ? OFFSET ?
                """,
                (request_id, limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_next_item(self) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT i.*, a.audio_path, a.file_name, a.duration_seconds
                FROM transcription_request_item i
                JOIN audio_transcription a ON a.audio_key = i.audio_key
                WHERE i.status = 'queued' ORDER BY i.created_at, i.id LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE transcription_request_item
                SET status='processing', progress_percent=5, started_at=?, updated_at=?
                WHERE id=? AND status='queued'
                """,
                (now, now, row["id"]),
            )
            if updated.rowcount != 1:
                return None
            connection.execute(
                """
                UPDATE transcription_request SET status='processing',
                    started_at=COALESCE(started_at, ?), updated_at=? WHERE id=?
                """,
                (now, now, row["request_id"]),
            )
            return dict(row)

    def complete_item(self, item: dict[str, Any], result: dict[str, Any]) -> None:
        now = utc_now()
        profile = item["requested_profile"]
        status = "review" if any(
            channel.get("quality_status") == "review"
            for channel in result.get("channels", [])
        ) else "completed"
        column = "fast_json" if profile == "rapido" else "detailed_json"
        status_column = "fast_status" if profile == "rapido" else "detailed_status"
        completed_column = (
            "fast_completed_at" if profile == "rapido" else "detailed_completed_at"
        )
        with self.database.connection() as connection:
            connection.execute(
                f"""
                UPDATE audio_transcription
                SET {column}=?, {status_column}=?, {completed_column}=?, updated_at=?
                WHERE audio_key=?
                """,
                (json.dumps(result, ensure_ascii=False), status, now, now, item["audio_key"]),
            )
            connection.execute(
                """
                UPDATE transcription_request_item
                SET status=?, result_profile=?, progress_percent=100,
                    completed_at=?, updated_at=? WHERE id=?
                """,
                (status, profile, now, now, item["id"]),
            )
            self._refresh_request(connection, item["request_id"])

    def fail_item(self, item: dict[str, Any], error: str) -> None:
        now = utc_now()
        with self.database.connection() as connection:
            connection.execute(
                """
                UPDATE transcription_request_item
                SET status='failed', error=?, completed_at=?, updated_at=? WHERE id=?
                """,
                (error, now, now, item["id"]),
            )
            self._refresh_request(connection, item["request_id"])

    def get_transcription(self, audio_key: str) -> dict[str, Any] | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT * FROM audio_transcription WHERE audio_key = ?",
                (audio_key,),
            ).fetchone()
        if row is None:
            return None
        payload = dict(row)
        for field in ("fast_json", "detailed_json", "manual_json"):
            payload[field] = self.database.decode(payload[field])
        if payload["manual_json"] is not None:
            payload["effective_source"] = "manual"
            payload["effective_transcription"] = payload["manual_json"]
        elif payload["detailed_json"] is not None:
            payload["effective_source"] = "detalhado"
            payload["effective_transcription"] = payload["detailed_json"]
        else:
            payload["effective_source"] = "rapido"
            payload["effective_transcription"] = payload["fast_json"]
        return payload

    def save_manual_revision(
        self,
        audio_key: str,
        edited_by: str,
        reason: str | None,
        corrected: dict[str, Any],
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT manual_json, manual_version FROM audio_transcription WHERE audio_key=?",
                (audio_key,),
            ).fetchone()
            if row is None:
                return None
            version = int(row["manual_version"]) + 1
            corrected_json = json.dumps(corrected, ensure_ascii=False)
            connection.execute(
                """
                INSERT INTO transcription_revision (
                    id, audio_key, version, edited_by, reason,
                    previous_json, corrected_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()), audio_key, version, edited_by, reason,
                    row["manual_json"], corrected_json, now,
                ),
            )
            connection.execute(
                """
                UPDATE audio_transcription SET manual_json=?, manual_version=?,
                    manual_updated_at=?, updated_at=? WHERE audio_key=?
                """,
                (corrected_json, version, now, now, audio_key),
            )
        return self.get_transcription(audio_key)

    def list_revisions(self, audio_key: str) -> list[dict[str, Any]] | None:
        with self.database.connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM audio_transcription WHERE audio_key=?",
                (audio_key,),
            ).fetchone()
            if exists is None:
                return None
            rows = connection.execute(
                """
                SELECT id, audio_key, version, edited_by, reason, corrected_json, created_at
                FROM transcription_revision WHERE audio_key=? ORDER BY version DESC
                """,
                (audio_key,),
            ).fetchall()
        revisions = [dict(row) for row in rows]
        for revision in revisions:
            revision["transcription"] = self.database.decode(
                revision.pop("corrected_json")
            )
        return revisions

    def enqueue_detailed_reprocess(self, request_id: str, audio_key: str) -> str | None:
        now = utc_now()
        with self.database.connection() as connection:
            audio = connection.execute(
                "SELECT audio_key FROM audio_transcription WHERE audio_key=?",
                (audio_key,),
            ).fetchone()
            if audio is None:
                return None
            item_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO transcription_request_item (
                    id, request_id, audio_key, requested_profile, status,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'detalhado', 'queued', ?, ?)
                ON CONFLICT(request_id, audio_key) DO UPDATE SET
                    requested_profile='detalhado', result_profile=NULL, status='queued',
                    reused=0, progress_percent=0, error=NULL, completed_at=NULL, updated_at=excluded.updated_at
                """,
                (item_id, request_id, audio_key, now, now),
            )
            self._refresh_request(connection, request_id)
        return item_id

    def _refresh_request(self, connection: Any, request_id: str) -> None:
        counts = connection.execute(
            """
            SELECT COUNT(*) total,
                   SUM(status IN ('completed','review')) completed,
                   SUM(status='review') review,
                   SUM(status='failed') failed,
                   SUM(status IN ('queued','processing')) active
            FROM transcription_request_item WHERE request_id=?
            """,
            (request_id,),
        ).fetchone()
        total = int(counts["total"] or 0)
        completed = int(counts["completed"] or 0)
        review = int(counts["review"] or 0)
        failed = int(counts["failed"] or 0)
        active = int(counts["active"] or 0)
        if active:
            status = "processing" if completed or failed else "queued"
            completed_at = None
        elif failed and completed:
            status = "partially_completed"
            completed_at = utc_now()
        elif failed and not completed:
            status = "failed"
            completed_at = utc_now()
        elif review:
            status = "completed_with_review"
            completed_at = utc_now()
        else:
            status = "completed"
            completed_at = utc_now()
        connection.execute(
            """
            UPDATE transcription_request SET status=?, total_items=?, completed_items=?,
                review_items=?, failed_items=?, completed_at=COALESCE(?, completed_at), updated_at=?
            WHERE id=?
            """,
            (status, total, completed, review, failed, completed_at, utc_now(), request_id),
        )

    def _request_payload(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["request_id"] = payload["id"]
        payload["filters"] = self.database.decode(payload.pop("filters_json"))
        total = max(1, int(payload["total_items"]))
        finished = int(payload["completed_items"]) + int(payload["failed_items"])
        payload["progress_percent"] = round(finished / total * 100, 2)
        return payload
