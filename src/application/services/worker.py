from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from src.application.use_cases.transcrever_chamada import transcrever_chamada_estereo
from src.infrastructure.config.settings import obter_configuracoes
from src.infrastructure.persistence.repository import TranscriptionRepository
from src.infrastructure.recognition.openai_whisper_recognizer import load_model


class TranscriptionWorker:
    def __init__(self, repository: TranscriptionRepository) -> None:
        self.repository = repository
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.model: Any | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(
            target=self._run,
            name="transcription-worker",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=10)

    def _run(self) -> None:
        settings = obter_configuracoes()
        while not self.stop_event.is_set():
            item = self.repository.claim_next_item()
            if item is None:
                self.stop_event.wait(2)
                continue
            try:
                if self.model is None:
                    logging.info("Worker carregando o modelo: %s", settings.model_source)
                    self.model = load_model(settings.model_source)
                detailed = item["requested_profile"] == "detalhado"
                result = transcrever_chamada_estereo(
                    Path(item["audio_path"]),
                    settings.model_source,
                    channel_names=(settings.channel_0_role, settings.channel_1_role),
                    loaded_model=self.model,
                    audio_preprocessing=settings.audio_preprocessing,
                    quality_retry=detailed,
                )
                result["processing"]["profile"] = item["requested_profile"]
                self.repository.complete_item(item, result)
            except Exception as exc:
                logging.exception("Falha no worker | item=%s", item["id"])
                self.repository.fail_item(item, str(exc))
