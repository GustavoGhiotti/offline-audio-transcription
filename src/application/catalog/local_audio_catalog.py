from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from src.application.use_cases.transcrever_lote import SUPPORTED_EXTENSIONS
from src.infrastructure.audio.ffmpeg import inspect_audio


class LocalAudioCatalog:
    """Adaptador temporário até a tabela de ligações do Atlas estar definida."""

    def __init__(self, input_dir: Path) -> None:
        self.input_dir = input_dir.resolve()

    def select(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        minimum = float(filters["duracao_minima_segundos"])
        maximum = float(filters["duracao_maxima_segundos"])
        limit = int(filters.get("limite_total") or filters["quantidade_por_operador"])
        calls: list[dict[str, Any]] = []
        for path in self.input_dir.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            info = inspect_audio(path)
            if not minimum <= info.duration_seconds <= maximum:
                continue
            relative = path.relative_to(self.input_dir).as_posix()
            audio_key = hashlib.sha256(relative.encode("utf-8")).hexdigest()
            calls.append(
                {
                    "audio_key": audio_key,
                    "source_call_id": path.stem,
                    "audio_path": str(path.resolve()),
                    "file_name": path.name,
                    "duration_seconds": info.duration_seconds,
                }
            )
        calls.sort(key=lambda call: (call["duration_seconds"], call["file_name"]))
        warnings = [
            "catalogo_local_de_desenvolvimento",
            "tabulacao_carteira_periodo_e_operador_serao_aplicados_no_adaptador_do_atlas",
        ]
        return calls[:limit], warnings
