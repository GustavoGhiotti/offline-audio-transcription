from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.application.use_cases.transcrever_chamada import transcrever_chamada_estereo
from src.infrastructure.audio.ffmpeg import inspect_audio
from src.infrastructure.recognition.openai_whisper_recognizer import load_model

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".ogg", ".flac", ".m4a", ".opus"}
CURRENT_SCHEMA_VERSION = "1.6"
_worker_context = threading.local()


@dataclass(frozen=True, slots=True)
class AudioJob:
    source: Path
    duration_seconds: float


def discover_audio_files(input_dir: Path, recursive: bool) -> list[Path]:
    iterator = input_dir.rglob("*") if recursive else input_dir.glob("*")
    return sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def caminho_json(source: Path, input_dir: Path, output_dir: Path) -> Path:
    relative = source.relative_to(input_dir)
    return output_dir / relative.with_suffix(".json")


def transcricao_concluida(destination: Path, source: Path) -> bool:
    if not destination.is_file():
        return False
    try:
        data = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        logging.warning(
            "JSON existente invalido; audio sera reprocessado | audio=%s json=%s",
            source.name,
            destination,
        )
        return False

    audio = data.get("audio") or {}
    return (
        data.get("schema_version") == CURRENT_SCHEMA_VERSION
        and data.get("status") == "completed"
        and audio.get("file_name") == source.name
    )


def ordenar_por_duracao(files: list[Path]) -> list[Path]:
    durations = {
        source: inspect_audio(source).duration_seconds
        for source in files
    }
    return sorted(
        files,
        key=lambda source: (durations[source], source.name.lower(), str(source).lower()),
    )


def criar_batches(
    files: list[Path],
    max_files: int,
    max_minutes: float,
) -> list[list[AudioJob]]:
    max_seconds = max_minutes * 60
    batches: list[list[AudioJob]] = []
    current: list[AudioJob] = []
    current_seconds = 0.0

    for source in files:
        duration = inspect_audio(source).duration_seconds
        job = AudioJob(source=source, duration_seconds=duration)
        exceeds_quantity = len(current) >= max_files
        exceeds_duration = bool(current) and current_seconds + duration > max_seconds
        if exceeds_quantity or exceeds_duration:
            batches.append(current)
            current = []
            current_seconds = 0.0
        current.append(job)
        current_seconds += duration

    if current:
        batches.append(current)
    return batches


def _worker_model(model_source: str) -> Any:
    model = getattr(_worker_context, "model", None)
    if model is None:
        logging.info("Worker carregando modelo | thread=%s", threading.current_thread().name)
        model = load_model(model_source)
        _worker_context.model = model
    return model


def _processar_job(
    job: AudioJob,
    input_dir: Path,
    model_source: str,
    output_dir: Path,
    channel_names: tuple[str, str],
    audio_preprocessing: str,
) -> tuple[dict[str, Any], Path]:
    result = transcrever_chamada_estereo(
        job.source,
        model_source,
        channel_names=channel_names,
        loaded_model=_worker_model(model_source),
        audio_preprocessing=audio_preprocessing,
    )
    destination = caminho_json(job.source, input_dir, output_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result, destination


def transcrever_lote(
    input_dir: Path,
    model_source: str,
    output_dir: Path,
    channel_names: tuple[str, str],
    recursive: bool,
    workers: int,
    batch_max_files: int,
    batch_max_minutes: float,
    audio_preprocessing: str = "telephony",
    max_audios: int | None = None,
) -> dict[str, Any]:
    if workers > 1:
        logging.warning(
            "OpenAI Whisper em CPU limitado a 1 worker para evitar cópias do modelo na memória"
        )
        workers = 1
    files = discover_audio_files(input_dir, recursive)
    pending_files: list[Path] = []
    skipped_files: list[Path] = []
    for source in files:
        destination = caminho_json(source, input_dir, output_dir)
        if transcricao_concluida(destination, source):
            skipped_files.append(source)
            logging.info(
                "Audio ignorado; transcricao ja concluida | audio=%s json=%s",
                source.name,
                destination,
            )
        else:
            pending_files.append(source)

    pending_files = ordenar_por_duracao(pending_files)
    selected_files = pending_files[:max_audios] if max_audios is not None else pending_files
    batches = criar_batches(selected_files, batch_max_files, batch_max_minutes)
    logging.info(
        "Início do lote | entrada=%s arquivos=%s batches=%s workers=%s saída=%s",
        input_dir,
        len(files),
        len(batches),
        workers,
        output_dir,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = time.perf_counter()
    successes = 0
    failures: list[dict[str, str]] = []
    total_audio_seconds = 0.0

    batch_metrics: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="openai-whisper",
    ) as executor:
        for batch_index, batch in enumerate(batches, start=1):
            batch_started_at = time.perf_counter()
            batch_audio_seconds = sum(job.duration_seconds for job in batch)
            logging.info(
                "Início do batch %s/%s | arquivos=%s minutos=%.3f",
                batch_index,
                len(batches),
                len(batch),
                batch_audio_seconds / 60,
            )
            futures = {
                executor.submit(
                    _processar_job,
                    job,
                    input_dir,
                    model_source,
                    output_dir,
                    channel_names,
                    audio_preprocessing,
                ): job
                for job in batch
            }
            batch_successes = 0
            for future in as_completed(futures):
                job = futures[future]
                try:
                    result, destination = future.result()
                    total_audio_seconds += float(result["audio"]["duration_seconds"])
                    successes += 1
                    batch_successes += 1
                    logging.info(
                        "JSON salvo | áudio=%s json=%s",
                        job.source.name,
                        destination,
                    )
                except Exception as exc:
                    failures.append({"source": str(job.source), "error": str(exc)})
                    logging.error(
                        "Falha no audio | arquivo=%s erro=%s",
                        job.source,
                        exc,
                    )

            batch_processing_seconds = time.perf_counter() - batch_started_at
            metric = {
                "batch": batch_index,
                "files": len(batch),
                "successes": batch_successes,
                "failures": len(batch) - batch_successes,
                "audio_minutes": round(batch_audio_seconds / 60, 3),
                "processing_seconds": round(batch_processing_seconds, 3),
                "realtime_factor": (
                    round(batch_processing_seconds / batch_audio_seconds, 4)
                    if batch_audio_seconds
                    else None
                ),
            }
            batch_metrics.append(metric)
            logging.info("Fim do batch %s/%s | %s", batch_index, len(batches), metric)

    processing_seconds = time.perf_counter() - started_at
    summary = {
        "files_found": len(files),
        "files_pending": len(pending_files),
        "files_selected": len(selected_files),
        "pending_remaining": len(pending_files) - len(selected_files),
        "skipped_existing": len(skipped_files),
        "workers": workers,
        "batches": batch_metrics,
        "successes": successes,
        "failures": failures,
        "audio_minutes": round(total_audio_seconds / 60, 3),
        "processing_seconds": round(processing_seconds, 3),
        "aggregate_realtime_factor": (
            round(processing_seconds / total_audio_seconds, 4)
            if total_audio_seconds
            else None
        ),
    }
    (output_dir / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logging.info(
        "Fim do lote | sucessos=%s falhas=%s minutos=%.3f processamento=%.3fs rtf=%s",
        successes,
        len(failures),
        total_audio_seconds / 60,
        processing_seconds,
        summary["aggregate_realtime_factor"],
    )
    return summary
