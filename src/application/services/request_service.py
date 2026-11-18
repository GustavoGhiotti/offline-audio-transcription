from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.application.catalog.local_audio_catalog import LocalAudioCatalog
from src.infrastructure.persistence.repository import TranscriptionRepository


class RequestService:
    def __init__(
        self,
        repository: TranscriptionRepository,
        catalog: LocalAudioCatalog,
    ) -> None:
        self.repository = repository
        self.catalog = catalog

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        client_request_id = payload.get("client_request_id")
        if client_request_id:
            existing = self.repository.find_by_client_request_id(client_request_id)
            if existing:
                existing["reused_request"] = True
                return existing

        profile = payload.pop("perfil")
        profile = profile.value if hasattr(profile, "value") else str(profile)
        calls, warnings = self.catalog.select(payload)
        queue_delay_seconds = self.repository.estimate_queue_delay()
        rtf = float(os.getenv(
            "FAST_ESTIMATED_RTF" if profile == "rapido" else "DETAILED_ESTIMATED_RTF",
            "3.0" if profile == "rapido" else "4.5",
        ))
        estimated_seconds = sum(call["duration_seconds"] for call in calls) * rtf
        request_id = str(uuid.uuid4())
        result = self.repository.create_request(
            request_id=request_id,
            client_request_id=client_request_id,
            profile=profile,
            filters=payload,
            calls=calls,
            estimated_duration_seconds=estimated_seconds,
        )
        estimated_completion_at = (
            datetime.now(timezone.utc)
            + timedelta(
                seconds=queue_delay_seconds + result["estimated_duration_seconds"]
            )
        ).isoformat()
        self.repository.update_request_estimate(
            request_id,
            queue_delay_seconds,
            estimated_completion_at,
        )
        result = self.repository.get_request(request_id) or result
        result["catalog_warnings"] = warnings
        result["status_url"] = f"/api/v1/transcription-requests/{request_id}"
        return result
