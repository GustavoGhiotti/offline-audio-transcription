from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ProcessingProfile(str, Enum):
    RAPIDO = "rapido"
    DETALHADO = "detalhado"


class CreateTranscriptionRequest(BaseModel):
    perfil: ProcessingProfile
    duracao_minima_segundos: float = Field(ge=0)
    duracao_maxima_segundos: float = Field(gt=0)
    tabulacoes: list[str] = Field(default_factory=list)
    quantidade_por_operador: int = Field(ge=1, le=10000)
    data_inicial: date
    data_final: date
    carteiras: list[str] = Field(min_length=1)
    limite_total: int | None = Field(default=None, ge=1, le=100000)
    client_request_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_ranges(self) -> "CreateTranscriptionRequest":
        if self.duracao_maxima_segundos < self.duracao_minima_segundos:
            raise ValueError("duracao_maxima_segundos deve ser maior ou igual a minima")
        if self.data_final < self.data_inicial:
            raise ValueError("data_final deve ser maior ou igual a data_inicial")
        return self


class ManualRevisionRequest(BaseModel):
    edited_by: str = Field(min_length=1, max_length=150)
    reason: str | None = Field(default=None, max_length=500)
    transcription: dict[str, Any]
