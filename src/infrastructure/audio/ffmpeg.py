from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class AudioProcessingError(RuntimeError):
    """Falha ao inspecionar ou normalizar um arquivo de áudio."""


@dataclass(frozen=True)
class AudioInfo:
    channels: int
    sample_rate: int
    duration_seconds: float
    codec_name: str
    bit_rate: int | None
    format_name: str
    size_bytes: int


@lru_cache(maxsize=None)
def _require_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable:
        return executable

    configured_dir = os.getenv("FFMPEG_BIN_DIR", "").strip()
    if configured_dir:
        configured = Path(configured_dir)
        if not configured.is_absolute():
            configured = ROOT / configured
        candidate = configured / f"{name}.exe"
        if candidate.is_file():
            return str(candidate)

    bundled = sorted((ROOT / "tools").glob(f"ffmpeg*/bin/{name}.exe"))
    if bundled:
        return str(bundled[-1])

    raise AudioProcessingError(
        f"{name} não foi encontrado no PATH, FFMPEG_BIN_DIR ou tools/ffmpeg*/bin."
    )


def inspect_audio(source: Path) -> AudioInfo:
    ffprobe = _require_executable("ffprobe")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=channels,sample_rate,codec_name,bit_rate:format=duration,format_name,size",
        "-of",
        "json",
        str(source),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AudioProcessingError(completed.stderr.strip() or "Não foi possível ler o áudio.")

    try:
        payload = json.loads(completed.stdout)
        stream = payload["streams"][0]
        return AudioInfo(
            channels=int(stream["channels"]),
            sample_rate=int(stream["sample_rate"]),
            duration_seconds=float(payload["format"]["duration"]),
            codec_name=str(stream.get("codec_name", "")),
            bit_rate=int(stream["bit_rate"]) if stream.get("bit_rate") else None,
            format_name=str(payload["format"].get("format_name", "")),
            size_bytes=int(payload["format"].get("size", source.stat().st_size)),
        )
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AudioProcessingError("O FFprobe retornou metadados inválidos.") from exc


def extract_channel(
    source: Path,
    destination: Path,
    channel_index: int,
    preprocessing: str = "telephony",
) -> None:
    ffmpeg = _require_executable("ffmpeg")
    destination.parent.mkdir(parents=True, exist_ok=True)
    filters = [f"beta=mono|c0=c{channel_index}"]
    if preprocessing == "telephony":
        # Tratamento conservador para voz telefônica. O áudio original de 8 kHz
        # só possui frequências até 4 kHz; o filtro remove ruído fora da faixa
        # útil e nivela diferenças grandes de volume sem cortar silêncios.
        filters.extend(
            [
                "highpass=f=100",
                "lowpass=f=3800",
                "afftdn=nr=6:nf=-45",
                "dynaudnorm=f=250:g=7:p=0.9:m=6",
            ]
        )
    elif preprocessing != "none":
        raise ValueError(
            f"Tratamento de áudio inválido: {preprocessing}. Use 'telephony' ou 'none'."
        )

    channel_filter = ",".join(filters)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-af",
        channel_filter,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise AudioProcessingError(
            completed.stderr.strip() or f"Falha ao extrair o canal {channel_index}."
        )
