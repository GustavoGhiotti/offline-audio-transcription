from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.infrastructure.audio.ffmpeg import extract_channel, inspect_audio
from src.infrastructure.config.settings import ROOT
from src.infrastructure.recognition.openai_whisper_recognizer import load_model, transcribe_wav


def agrupar_turnos(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for item in sorted(
        items,
        key=lambda value: (value["start_seconds"], value["channel"]),
    ):
        if turns and turns[-1]["speaker"] == item["speaker"]:
            current = turns[-1]
            current["end_seconds"] = max(
                current["end_seconds"],
                item["end_seconds"],
            )
            current["text"] = f"{current['text']} {item['text']}".strip()
            continue

        turns.append(
            {
                "id": "",
                "channel": item["channel"],
                "speaker": item["speaker"],
                "start_seconds": item["start_seconds"],
                "end_seconds": item["end_seconds"],
                "text": item["text"],
            }
        )

    for index, turn in enumerate(turns, start=1):
        turn["id"] = f"turn-{index:06d}"
    return turns


def corrigir_apresentacao_do_operador(
    timeline: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Corrige somente a confusão contextual entre 'eu sou o' e 'Sr.'."""
    corrections: list[dict[str, str]] = []
    for index, turn in enumerate(timeline):
        if turn["speaker"] != "operador" or index < 2:
            continue
        previous = timeline[index - 1]
        earlier = timeline[index - 2]
        customer_requested_identification = bool(
            re.search(
                r"\b(?:sim[, ]+sou eu|quem gostaria)\b",
                previous["text"],
                flags=re.IGNORECASE,
            )
        )
        operator_asked_for_woman = (
            earlier["speaker"] == "operador"
            and bool(re.search(r"\bSra\.?\b", earlier["text"], flags=re.IGNORECASE))
        )
        match = re.match(
            r"^(Prazer,?\s+)Sr\.?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][\wÀ-ÿ'-]*)(,?)",
            turn["text"],
            flags=re.IGNORECASE,
        )
        if not (customer_requested_identification and operator_asked_for_woman and match):
            continue

        original = turn["text"]
        prefix, name, comma = match.groups()
        corrected = f"{prefix}eu sou o {name}{comma}{original[match.end():]}"
        corrected = re.sub(
            r"\bsou a empresa\b",
            "sou da empresa",
            corrected,
            count=1,
            flags=re.IGNORECASE,
        )
        turn["text"] = corrected
        corrections.append(
            {
                "turn_id": turn["id"],
                "original": original,
                "corrected": corrected,
                "reason": "apresentacao_do_operador_confirmada_pelo_contexto",
            }
        )
    return corrections


def transcrever_chamada_estereo(
    source: Path,
    model_source: str,
    channel_names: tuple[str, str] = ("cliente", "operador"),
    loaded_model: Any | None = None,
    audio_preprocessing: str = "telephony",
    quality_retry: bool = True,
) -> dict[str, Any]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Áudio não encontrado: {source}")

    clock_started_at = datetime.now(timezone.utc)
    started_at = time.perf_counter()
    logging.info("Iniciando áudio | arquivo=%s", source.name)
    model = loaded_model if loaded_model is not None else load_model(model_source)
    info = inspect_audio(source)
    logging.info(
        "Áudio inspecionado | arquivo=%s duracao=%.3fs canais=%s amostragem=%sHz codec=%s",
        source.name,
        info.duration_seconds,
        info.channels,
        info.sample_rate,
        info.codec_name,
    )
    if info.channels != 2:
        raise ValueError(
            f"Esperado áudio estéreo com 2 canais; o arquivo possui {info.channels}."
        )

    transcripts = []
    temp_root = ROOT / ".tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    temporary_files: list[Path] = []
    try:
        for channel_index, speaker in enumerate(channel_names):
            normalized = temp_root / (
                f"transcricao-{uuid.uuid4().hex}-channel-{channel_index}.wav"
            )
            temporary_files.append(normalized)
            extract_channel(
                source,
                normalized,
                channel_index,
                preprocessing=audio_preprocessing,
            )
        for channel_index, speaker in enumerate(channel_names):
            transcripts.append(
                transcribe_wav(
                    temporary_files[channel_index],
                    model=model,
                    channel=channel_index,
                    speaker=speaker,
                    quality_retry=quality_retry,
                )
            )
    finally:
        for temporary_file in temporary_files:
            temporary_file.unlink(missing_ok=True)

    processing_seconds = time.perf_counter() - started_at
    completed_at = datetime.now(timezone.utc)
    timeline = agrupar_turnos(
        [
            item
            for transcript in transcripts
            for item in transcript.timeline_items()
        ]
    )
    contextual_corrections = corrigir_apresentacao_do_operador(timeline)
    for correction in contextual_corrections:
        logging.info(
            "Apresentacao do operador corrigida pelo contexto | turn=%s",
            correction["turn_id"],
        )
    channel_payloads = [transcript.to_dict() for transcript in transcripts]
    for correction in contextual_corrections:
        for channel_payload in channel_payloads:
            if channel_payload["speaker"] == "operador":
                channel_payload["text"] = channel_payload["text"].replace(
                    correction["original"],
                    correction["corrected"],
                    1,
                )
    realtime_factor = (
        round(processing_seconds / info.duration_seconds, 4)
        if info.duration_seconds
        else None
    )
    logging.info(
        "Áudio concluído | arquivo=%s duracao=%.3fs processamento=%.3fs rtf=%s palavras=%s",
        source.name,
        info.duration_seconds,
        processing_seconds,
        realtime_factor,
        sum(len(transcript.words) for transcript in transcripts),
    )
    return {
        "schema_version": "1.6",
        "transcription_id": str(uuid.uuid4()),
        "status": "completed",
        "created_at": completed_at.isoformat(),
        "audio": {
            "audio_id": source.stem,
            "file_name": source.name,
            "source_path": str(source),
            "storage_key": None,
            "playback_url": None,
            "format": info.format_name,
            "codec": info.codec_name,
            "bit_rate_bps": info.bit_rate,
            "size_bytes": info.size_bytes,
            "channels": info.channels,
            "sample_rate_hz": info.sample_rate,
            "duration_seconds": round(info.duration_seconds, 3),
        },
        "speakers": [
            {"channel": channel, "speaker": speaker}
            for channel, speaker in enumerate(channel_names)
        ],
        "channels": channel_payloads,
        "timeline": timeline,
        "full_text": "\n".join(
            f"{item['speaker']}: {item['text']}" for item in timeline
        ),
        "processing": {
            "engine": "openai-whisper",
            "model": model_source,
            "device": "cpu",
            "audio_preprocessing": audio_preprocessing,
            "recognition_strategy": (
                "contextual_with_quality_retry"
                if quality_retry
                else "contextual_fast_no_retry"
            ),
            "torch_threads": getattr(model, "torch_threads", None),
            "started_at": clock_started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round(processing_seconds, 3),
            "realtime_factor": realtime_factor,
            "contextual_corrections": contextual_corrections,
        },
    }
