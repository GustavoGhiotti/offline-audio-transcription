from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / ".env")


@dataclass(frozen=True, slots=True)
class Settings:
    model_source: str
    input_dir: Path
    output_dir: Path
    channel_0_role: str
    channel_1_role: str
    workers: int
    batch_max_files: int
    batch_max_minutes: float
    audio_preprocessing: str


def obter_configuracoes() -> Settings:
    return Settings(
        model_source=_model_source(),
        input_dir=_path("AUDIO_INPUT_DIR", "audios"),
        output_dir=_path("TRANSCRIPTION_OUTPUT_DIR", "output"),
        channel_0_role=os.getenv("CHANNEL_0_ROLE", "cliente").strip(),
        channel_1_role=os.getenv("CHANNEL_1_ROLE", "operador").strip(),
        workers=max(1, int(os.getenv("TRANSCRIPTION_WORKERS", "2"))),
        batch_max_files=max(1, int(os.getenv("BATCH_MAX_FILES", "100"))),
        batch_max_minutes=max(0.1, float(os.getenv("BATCH_MAX_MINUTES", "60"))),
        audio_preprocessing=os.getenv("AUDIO_PREPROCESSING", "none").strip().lower(),
    )


def _path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT / value


def _model_source() -> str:
    return os.getenv("OPENAI_WHISPER_MODEL", "turbo").strip()
