from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, status

from src.api.schemas import CreateTranscriptionRequest, ManualRevisionRequest
from src.application.catalog.local_audio_catalog import LocalAudioCatalog
from src.application.services.request_service import RequestService
from src.application.services.worker import TranscriptionWorker
from src.infrastructure.config.settings import ROOT, obter_configuracoes
from src.infrastructure.persistence.database import Database
from src.infrastructure.persistence.repository import TranscriptionRepository
from src.shared.logging_config import configurar_logging


def _database_path() -> Path:
    configured = Path(os.getenv("TRANSCRIPTION_DATABASE", "data/transcricoes.db"))
    return configured if configured.is_absolute() else ROOT / configured


database = Database(_database_path())
repository = TranscriptionRepository(database)
catalog = LocalAudioCatalog(obter_configuracoes().input_dir)
request_service = RequestService(repository, catalog)
worker = TranscriptionWorker(repository)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configurar_logging(ROOT, debug=False)
    worker.start()
    yield
    worker.stop()


app = FastAPI(
    title="API de Transcrição de Ligações",
    description="Solicitações assíncronas de transcrição para integração com o Atlas.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/transcription-requests",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_request(payload: CreateTranscriptionRequest) -> dict:
    return request_service.create(payload.model_dump(mode="json"))


@app.get("/api/v1/transcription-requests/{request_id}")
def get_request(request_id: str) -> dict:
    result = repository.get_request(request_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    return result


@app.get("/api/v1/transcription-requests/{request_id}/items")
def list_request_items(
    request_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> dict:
    request = repository.get_request(request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    items = repository.list_items(request_id, (page - 1) * page_size, page_size)
    return {
        "request_id": request_id,
        "page": page,
        "page_size": page_size,
        "total": request["total_items"],
        "items": items,
    }


@app.get("/api/v1/transcriptions/{audio_key}")
def get_transcription(audio_key: str) -> dict:
    result = repository.get_transcription(audio_key)
    if result is None:
        raise HTTPException(status_code=404, detail="Transcrição não encontrada")
    return result


@app.patch("/api/v1/transcriptions/{audio_key}/manual")
def save_manual_revision(audio_key: str, payload: ManualRevisionRequest) -> dict:
    result = repository.save_manual_revision(
        audio_key=audio_key,
        edited_by=payload.edited_by,
        reason=payload.reason,
        corrected=payload.transcription,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Transcrição não encontrada")
    return result


@app.get("/api/v1/transcriptions/{audio_key}/revisions")
def list_manual_revisions(audio_key: str) -> dict:
    revisions = repository.list_revisions(audio_key)
    if revisions is None:
        raise HTTPException(status_code=404, detail="Transcrição não encontrada")
    return {"audio_key": audio_key, "revisions": revisions}


@app.post(
    "/api/v1/transcription-requests/{request_id}/items/{audio_key}/reprocess-detailed",
    status_code=status.HTTP_202_ACCEPTED,
)
def reprocess_detailed(request_id: str, audio_key: str) -> dict:
    if repository.get_request(request_id) is None:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    item_id = repository.enqueue_detailed_reprocess(request_id, audio_key)
    if item_id is None:
        raise HTTPException(status_code=404, detail="Áudio não encontrado")
    return {
        "request_id": request_id,
        "audio_key": audio_key,
        "item_id": item_id,
        "status": "queued",
        "profile": "detalhado",
    }
