from __future__ import annotations

import logging
import os
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
import unicodedata

import numpy as np

from src.domain.models.transcricao import ChannelTranscript, Utterance, Word
from src.infrastructure.config.settings import ROOT

UTTERANCE_SILENCE_SECONDS = 0.8
UTTERANCE_MAX_SECONDS = 15.0
PORTUGUESE_CALL_PROMPT = (
    "Transcrição literal de uma ligação telefônica de cobrança em português do Brasil. "
    "Não corrija nem reformule o que foi dito. Preserve com atenção nomes próprios, "
    "códigos, números, valores e datas exatamente como forem falados. Diferencie "
    "literalmente 'meu nome é' de tratamentos como 'senhor' ou 'senhora'."
)


@lru_cache(maxsize=1)
def _load_domain_prompt() -> str:
    configured = Path(os.getenv("WHISPER_CONTEXT_FILE", "config/contexto_cobranca.txt"))
    prompt_path = configured if configured.is_absolute() else ROOT / configured
    try:
        prompt = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        logging.warning("Contexto de cobranca nao encontrado | arquivo=%s", prompt_path)
        return PORTUGUESE_CALL_PROMPT
    return prompt or PORTUGUESE_CALL_PROMPT


@dataclass(frozen=True, slots=True)
class OpenAIWhisperBundle:
    model: Any
    name: str
    torch_threads: int


def load_model(model_name: str) -> OpenAIWhisperBundle:
    try:
        import torch
        import whisper
    except ImportError as exc:
        raise RuntimeError(
            "OpenAI Whisper não está instalado. Execute: pip install -r requirements.txt"
        ) from exc

    torch_threads = max(1, int(os.getenv("TORCH_THREADS", "12")))
    torch_interop_threads = max(1, int(os.getenv("TORCH_INTEROP_THREADS", "1")))
    torch.set_num_threads(torch_threads)
    try:
        torch.set_num_interop_threads(torch_interop_threads)
    except RuntimeError:
        # O PyTorch só permite definir esse valor antes da primeira inferência.
        pass
    logging.info(
        "CPU configurada para o Whisper | torch_threads=%s interop_threads=%s",
        torch_threads,
        torch_interop_threads,
    )

    download_root = ROOT / "models" / "openai-whisper"
    download_root.mkdir(parents=True, exist_ok=True)
    model = whisper.load_model(
        model_name,
        device="cpu",
        download_root=str(download_root),
    )
    return OpenAIWhisperBundle(
        model=model,
        name=model_name,
        torch_threads=torch_threads,
    )


def transcribe_wav(
    wav_path: Path,
    model: OpenAIWhisperBundle,
    channel: int,
    speaker: str,
    quality_retry: bool = True,
) -> ChannelTranscript:
    started_at = time.perf_counter()
    audio = _read_pcm16_mono(wav_path)
    words = _transcribe_words(model, audio, condition_on_previous_text=True)
    quality_issues = _quality_issues(words)
    if quality_retry and _requires_retry(quality_issues):
        logging.info(
            "Canal com sinais de alucinacao; iniciando tentativa de recuperacao | canal=%s speaker=%s problemas=%s",
            channel,
            speaker,
            quality_issues,
        )
        retry_words = _transcribe_words(model, audio, condition_on_previous_text=False)
        retry_issues = _quality_issues(retry_words)
        if _quality_score(retry_words, retry_issues) < _quality_score(words, quality_issues):
            words = retry_words
            quality_issues = retry_issues
            logging.info(
                "Tentativa de recuperacao selecionada | canal=%s speaker=%s problemas=%s",
                channel,
                speaker,
                quality_issues,
            )
    utterances = _group_words_into_utterances(words)
    return ChannelTranscript(
        channel=channel,
        speaker=speaker,
        text=" ".join(word.text for word in words).strip(),
        words=words,
        utterances=utterances,
        processing_seconds=time.perf_counter() - started_at,
        quality_status="review" if quality_issues else "ok",
        quality_issues=tuple(quality_issues),
    )


def _transcribe_words(
    model: OpenAIWhisperBundle,
    audio: np.ndarray,
    condition_on_previous_text: bool,
) -> list[Word]:
    common_options = {
        "language": "pt",
        "task": "transcribe",
        "fp16": False,
        "verbose": None,
        "word_timestamps": True,
        "condition_on_previous_text": condition_on_previous_text,
    }
    if condition_on_previous_text:
        # Configuração original do Whisper: preservou melhor sentido, flexões e
        # concordância nos áudios reais que já estavam bons.
        result = model.model.transcribe(audio, **common_options)
    else:
        # Recuperação usada apenas quando a primeira tentativa apresenta sinais
        # fortes de alucinação ou repetição.
        result = model.model.transcribe(
            audio,
            **common_options,
            initial_prompt=_load_domain_prompt(),
            carry_initial_prompt=True,
            temperature=0.0,
            beam_size=5,
            patience=1.0,
            compression_ratio_threshold=2.2,
            logprob_threshold=-0.8,
            no_speech_threshold=0.5,
            hallucination_silence_threshold=1.0,
        )
    return _extract_words(result)


def _read_pcm16_mono(wav_path: Path) -> np.ndarray:
    with wave.open(str(wav_path), "rb") as audio:
        if (
            audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getframerate() != 16000
        ):
            raise ValueError("O Whisper exige WAV mono PCM 16 bits em 16 kHz.")
        raw = audio.readframes(audio.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def _extract_words(result: dict[str, Any]) -> list[Word]:
    words: list[Word] = []
    for segment in result.get("segments", []):
        segment_words = segment.get("words") or []
        if segment_words:
            for item in segment_words:
                text = str(item.get("word", "")).strip()
                if not text:
                    continue
                words.append(
                    Word(
                        text=text,
                        start=float(item["start"]),
                        end=float(item["end"]),
                        confidence=float(item.get("probability", 0.0)),
                    )
                )
            continue

        text = str(segment.get("text", "")).strip()
        if text:
            words.append(
                Word(
                    text=text,
                    start=float(segment["start"]),
                    end=float(segment["end"]),
                    confidence=0.0,
                )
            )
    return words


def _quality_issues(words: list[Word]) -> list[str]:
    if not words:
        return ["nenhuma_fala_reconhecida"]

    issues: list[str] = []
    text = " ".join(word.text for word in words)
    if "�" in text or any(
        character.isalpha()
        and "LATIN" not in unicodedata.name(character, "")
        for character in text
    ):
        issues.append("caracteres_fora_do_portugues")

    maximum_run = 1
    current_run = 0
    previous = ""
    for word in words:
        normalized = word.text.casefold().strip(".,!?;:-…")
        if normalized and normalized == previous:
            current_run += 1
        else:
            previous = normalized
            current_run = 1
        maximum_run = max(maximum_run, current_run)
    normalized_words = [word.text.casefold().strip(".,!?;:-…") for word in words]
    repeated_phrases = _has_consecutive_repeated_phrase(normalized_words)
    if maximum_run >= 5 or repeated_phrases:
        issues.append("repeticao_artificial_detectada")

    low_confidence = sum(word.confidence < 0.35 for word in words)
    if len(words) >= 10 and low_confidence / len(words) >= 0.25:
        issues.append("audio_ou_fala_pouco_clara")
    return issues


def _has_consecutive_repeated_phrase(words: list[str]) -> bool:
    """Detecta três repetições seguidas, evitando falsos positivos distantes."""
    for phrase_size in range(3, min(13, len(words) // 3 + 1)):
        for start in range(0, len(words) - phrase_size * 3 + 1):
            first = words[start : start + phrase_size]
            second = words[start + phrase_size : start + phrase_size * 2]
            third = words[start + phrase_size * 2 : start + phrase_size * 3]
            if first == second == third:
                return True
    return False


def _requires_retry(issues: list[str]) -> bool:
    return bool(
        {"caracteres_fora_do_portugues", "repeticao_artificial_detectada"}
        & set(issues)
    )


def _quality_score(words: list[Word], issues: list[str]) -> float:
    weights = {
        "caracteres_fora_do_portugues": 5.0,
        "repeticao_artificial_detectada": 4.0,
        "audio_ou_fala_pouco_clara": 2.0,
        "nenhuma_fala_reconhecida": 10.0,
    }
    average_confidence = (
        sum(word.confidence for word in words) / len(words)
        if words
        else 0.0
    )
    return sum(weights.get(issue, 1.0) for issue in issues) + (1.0 - average_confidence)


def _group_words_into_utterances(words: list[Word]) -> list[Utterance]:
    """Forma frases por pausas, preservando os tempos absolutos do áudio."""
    if not words:
        return []

    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        silence = word.start - current[-1].end if current else 0.0
        duration = word.end - current[0].start if current else 0.0
        if current and (
            silence >= UTTERANCE_SILENCE_SECONDS
            or duration >= UTTERANCE_MAX_SECONDS
        ):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)

    return [
        Utterance(
            text=" ".join(word.text for word in group),
            start=group[0].start,
            end=group[-1].end,
            confidence=sum(word.confidence for word in group) / len(group),
            words=group,
        )
        for group in groups
    ]
