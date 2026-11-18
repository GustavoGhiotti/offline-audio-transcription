"""Baixa o modelo configurado no cache local para uso totalmente offline."""

from src.infrastructure.config.settings import obter_configuracoes
from src.infrastructure.recognition.openai_whisper_recognizer import load_model


def main() -> None:
    settings = obter_configuracoes()
    print(f"Baixando e validando: {settings.model_source}")
    load_model(settings.model_source)
    print("Modelo disponível para execução offline.")


if __name__ == "__main__":
    main()
