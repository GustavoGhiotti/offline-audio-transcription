from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from src.application.use_cases.transcrever_chamada import transcrever_chamada_estereo
from src.application.use_cases.transcrever_lote import transcrever_lote
from src.infrastructure.config.settings import ROOT, obter_configuracoes
from src.infrastructure.recognition.openai_whisper_recognizer import load_model
from src.shared.logging_config import configurar_logging


def executar(argv: list[str] | None = None) -> int:
    args = _argumentos(argv)
    settings = obter_configuracoes()
    configurar_logging(ROOT, args.debug)

    model_source = args.model or settings.model_source
    output_dir = args.output_dir or settings.output_dir
    roles = (
        args.channel_0 or settings.channel_0_role,
        args.channel_1 or settings.channel_1_role,
    )
    audio_preprocessing = args.audio_preprocessing or settings.audio_preprocessing

    try:
        logging.info("Carregando modelo OpenAI Whisper: %s", model_source)
        if args.audio:
            model = load_model(model_source)
            result = transcrever_chamada_estereo(
                args.audio,
                model_source,
                channel_names=roles,
                loaded_model=model,
                audio_preprocessing=audio_preprocessing,
            )
            output_dir.mkdir(parents=True, exist_ok=True)
            destination = output_dir / f"{args.audio.stem}.json"
            destination.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            logging.info(
                "JSON salvo | áudio=%s json=%s",
                args.audio.name,
                destination,
            )
            return 0

        summary = transcrever_lote(
            args.input_dir or settings.input_dir,
            model_source,
            output_dir,
            roles,
            args.recursive,
            args.workers or settings.workers,
            args.batch_max_files or settings.batch_max_files,
            args.batch_max_minutes or settings.batch_max_minutes,
            audio_preprocessing,
            args.max_audios,
        )
        logging.info("Resumo: %s", summary)
        return 1 if summary["failures"] else 0
    except Exception as exc:
        logging.error("Falha na transcricao | erro=%s", exc)
        return 1


def _argumentos(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Robô de transcrição offline com OpenAI Whisper")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--audio", type=Path, help="processa somente um áudio")
    source.add_argument("--input-dir", type=Path, help="diretório de entrada")
    parser.add_argument(
        "--model",
        help="nome do modelo OpenAI Whisper (ex.: turbo, medium, large-v3)",
    )
    parser.add_argument("--output-dir", type=Path, help="diretório dos resultados")
    parser.add_argument("--channel-0", help="papel do canal 0")
    parser.add_argument("--channel-1", help="papel do canal 1")
    parser.add_argument(
        "--audio-preprocessing",
        choices=("telephony", "none"),
        help="tratamento aplicado antes da transcrição (padrão: none)",
    )
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--workers", type=int, help="arquivos processados simultaneamente")
    parser.add_argument("--batch-max-files", type=int, help="limite de arquivos por batch")
    parser.add_argument(
        "--batch-max-minutes",
        type=float,
        help="limite de minutos de áudio por batch",
    )
    parser.add_argument(
        "--max-audios",
        type=_inteiro_positivo,
        help="quantidade maxima de audios pendentes nesta execucao",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def _inteiro_positivo(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("informe uma quantidade maior que zero")
    return number
