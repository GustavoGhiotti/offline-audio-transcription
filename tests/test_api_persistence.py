from __future__ import annotations

import unittest
from pathlib import Path

from src.api.schemas import CreateTranscriptionRequest
from src.infrastructure.persistence.database import Database
from src.infrastructure.persistence.repository import TranscriptionRepository


class ApiPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = Path(".tmp/test-api.db").resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)
        self.repository = TranscriptionRepository(Database(self.path))
        self.call = {
            "audio_key": "audio-001",
            "source_call_id": "call-001",
            "audio_path": "C:/audios/audio-001.mp3",
            "file_name": "audio-001.mp3",
            "duration_seconds": 60.0,
        }

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.path) + suffix).unlink(missing_ok=True)

    def test_same_audio_can_belong_to_multiple_requests_and_reuse_result(self) -> None:
        first = self.repository.create_request(
            "request-1", None, "rapido", {}, [self.call], 180.0
        )
        item = self.repository.claim_next_item()
        self.assertIsNotNone(item)
        self.repository.complete_item(
            item,
            {"channels": [{"quality_status": "ok"}], "full_text": "teste"},
        )

        second = self.repository.create_request(
            "request-2", None, "rapido", {}, [self.call], 180.0
        )

        self.assertEqual(first["total_items"], 1)
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second["estimated_duration_seconds"], 0)
        items = self.repository.list_items("request-2", 0, 10)
        self.assertEqual(items[0]["reused"], 1)
        self.assertEqual(items[0]["result_profile"], "rapido")

    def test_detailed_request_does_not_reuse_only_fast_result(self) -> None:
        self.repository.create_request("request-1", None, "rapido", {}, [self.call], 180)
        item = self.repository.claim_next_item()
        self.repository.complete_item(
            item,
            {"channels": [{"quality_status": "ok"}]},
        )

        detailed = self.repository.create_request(
            "request-2", None, "detalhado", {}, [self.call], 270
        )

        self.assertEqual(detailed["status"], "queued")
        self.assertEqual(detailed["estimated_duration_seconds"], 270)

    def test_manual_revision_keeps_automatic_results(self) -> None:
        self.repository.create_request("request-1", None, "rapido", {}, [self.call], 180)
        item = self.repository.claim_next_item()
        self.repository.complete_item(
            item,
            {"channels": [{"quality_status": "ok"}], "full_text": "automático"},
        )

        result = self.repository.save_manual_revision(
            "audio-001", "usuario.atlas", "correção", {"full_text": "corrigido"}
        )

        self.assertEqual(result["fast_json"]["full_text"], "automático")
        self.assertEqual(result["effective_source"], "manual")
        self.assertEqual(result["effective_transcription"]["full_text"], "corrigido")
        self.assertEqual(len(self.repository.list_revisions("audio-001")), 1)

    def test_request_schema_validates_dates_and_duration(self) -> None:
        payload = CreateTranscriptionRequest.model_validate(
            {
                "perfil": "rapido",
                "duracao_minima_segundos": 10,
                "duracao_maxima_segundos": 120,
                "tabulacoes": ["PROMESSA"],
                "quantidade_por_operador": 5,
                "data_inicial": "2026-08-01",
                "data_final": "2026-08-03",
                "carteiras": ["ETA"],
            }
        )
        self.assertEqual(payload.perfil.value, "rapido")


if __name__ == "__main__":
    unittest.main()
