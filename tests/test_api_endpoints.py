from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["TRANSCRIPTION_DATABASE"] = ".tmp/test-endpoints.db"

from fastapi.testclient import TestClient

from src.api.app import app


class ApiEndpointTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        path = Path(".tmp/test-endpoints.db").resolve()
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)

    def test_create_and_query_async_request(self) -> None:
        with patch("src.api.app.worker.start"), patch("src.api.app.worker.stop"):
            with TestClient(app) as client:
                health = client.get("/health")
                self.assertEqual(health.status_code, 200)

                response = client.post(
                    "/api/v1/transcription-requests",
                    json={
                        "perfil": "rapido",
                        "duracao_minima_segundos": 0,
                        "duracao_maxima_segundos": 1000,
                        "tabulacoes": ["PROMESSA"],
                        "quantidade_por_operador": 1,
                        "data_inicial": "2026-08-01",
                        "data_final": "2026-08-03",
                        "carteiras": ["TESTE"],
                        "limite_total": 1,
                        "client_request_id": "endpoint-test-001",
                    },
                )
                self.assertEqual(response.status_code, 202, response.text)
                created = response.json()
                self.assertEqual(created["total_items"], 1)
                self.assertEqual(created["status"], "queued")

                request_id = created["id"]
                status_response = client.get(
                    f"/api/v1/transcription-requests/{request_id}"
                )
                self.assertEqual(status_response.status_code, 200)

                items_response = client.get(
                    f"/api/v1/transcription-requests/{request_id}/items"
                )
                self.assertEqual(items_response.status_code, 200)
                self.assertEqual(len(items_response.json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
