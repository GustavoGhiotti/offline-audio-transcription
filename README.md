# Robô de transcrição offline de áudios

> **Versão de portfólio.** Código real de produção, anonimizado: nomes de empresa, clientes, pessoas, hosts, e-mails e credenciais foram trocados por valores fictícios (`Acme`, `Banco Alfa`, `10.0.0.x`, `empresa.example`). Toda configuração sensível vem do `.env` (modelo em `.env.example`). O código não aponta para nenhum sistema real.

Automação estruturada no padrão do robô Envio Ativo VOXI para comparar uma
solução baseada no OpenAI Whisper oficial executado localmente em CPU.

A integração assíncrona com o Atlas, os endpoints e o modelo de dados estão descritos
em [docs/api-atlas.md](docs/api-atlas.md).

O programa:

1. inspeciona o áudio com FFprobe;
2. valida que existem dois canais;
3. separa atendente e cliente;
4. aplica tratamento telefônico leve e converte cada canal para PCM mono,
   16 kHz e 16 bits;
5. transcreve localmente com OpenAI Whisper em português;
6. forma frases pelas pausas reais entre palavras;
7. gera JSON com frases por turno, timestamps em segundos e desempenho.

## Estrutura

```text
main.py                              entrada principal do robô
scripts/
  executar_transcricao.py            entrada alternativa por módulo
starts/
  start_transcricao.bat              execução pelo Agendador do Windows
src/
  application/
    use_cases/
      transcrever_chamada.py          fluxo de uma chamada estéreo
      transcrever_lote.py             processamento de diretório
  domain/
    models/
      transcricao.py                  modelos internos da transcrição
  infrastructure/
    audio/
      ffmpeg.py                       inspeção e separação dos canais
    config/
      settings.py                     configurações do .env
    recognition/
      openai_whisper_recognizer.py    integração oficial OpenAI Whisper/PyTorch
  runners/
    transcricao.py                    CLI e montagem das dependências
  shared/
    logging_config.py                 logs em terminal e arquivo
audios/                               entrada padrão
models/                               modelos locais, fora do Git
output/                               resultados JSON, fora do Git
logs/                                 logs diários
tests/                                testes automatizados
```

## Por que preservar os canais

Em gravações telefônicas estéreo, cada lado da conversa pode estar em um canal
separado. Preservar essa informação é mais confiável e barato que executar
diarização para tentar descobrir quem falou.

Antes do teste, confirme qual canal representa o atendente. Os nomes podem ser
invertidos pela linha de comando.

O padrão atualmente observado na base é:

```text
canal 0 = cliente
canal 1 = operador
```

Esse mapeamento deve ser validado em arquivos de origens diferentes antes da
execução em produção.

## Requisitos

- Python 3.11 ou superior;
- FFmpeg e FFprobe disponíveis no `PATH`;
- modelo OpenAI Whisper armazenado localmente;
- áudio com dois canais.

O áudio original pode estar em 8 kHz. A conversão para 16 kHz atende ao formato
esperado pelo pipeline, mas não recria frequências que não foram gravadas.

## Instalação

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

O modelo padrão é `turbo`. Baixe uma vez e valide a cópia offline:

```powershell
python -m scripts.baixar_modelo
```

## Execução

```powershell
python main.py `
  --audio chamada.wav `
  --model turbo `
  --audio-preprocessing none `
  --channel-0 cliente `
  --channel-1 operador `
  --output-dir output
```

Para comparar sem tratamento, use `--audio-preprocessing none`. O JSON registra
qual opção foi utilizada em `processing.audio_preprocessing`.

O padrão atual é `none`, pois ele preservou melhor os fonemas nos testes com os
áudios telefônicos reais. A opção `telephony` permanece disponível para
comparação. Nenhuma opção remove silêncios ou desloca o áudio. Portanto, os tempos das
frases continuam correspondendo à posição original no player.

Se os canais estiverem invertidos:

```powershell
python main.py `
  --audio chamada.wav `
  --channel-0 operador `
  --channel-1 cliente
```

## Execução em lote

O modelo é carregado uma única vez e reutilizado em todos os arquivos:

```powershell
python main.py `
  --input-dir audios `
  --model turbo `
  --output-dir output `
  --workers 2 `
  --batch-max-files 100 `
  --batch-max-minutes 60 `
  --recursive
```

Também pode ser executado com:

```powershell
python -m scripts.executar_transcricao --recursive
```

O arquivo `starts\start_transcricao.bat` utiliza as configurações do `.env` e
pode ser utilizado pelo Agendador de Tarefas do Windows.

Os logs ficam em `logs\transcricao_AAAAMMDD.log`.

Para executar os testes:

```powershell
python -m unittest discover -s tests -v
```

## Run and Debug no VS Code

Abra `Run and Debug` (`Ctrl+Shift+D`) e escolha:

- `1. TESTE - um áudio estéreo`: solicita o arquivo e o papel dos canais;
- `2. TESTE - todos os áudios da pasta`: processa a pasta escolhida;
- `3. TESTES AUTOMATIZADOS`: executa os testes sem modelo e sem FFmpeg.

Antes do primeiro teste de transcrição:

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

O modelo precisa ter sido baixado com `python -m scripts.baixar_modelo`. Depois
disso, as transcrições utilizam somente o cache local e não acessam a internet.
O áudio de teste pode ser colocado em `audios\chamada.wav`.

Cada áudio gera um JSON. O arquivo `_summary.json` registra sucessos, falhas,
minutos processados, tempo total, RTF agregado e métricas de cada batch.

### Escalabilidade em CPU

Os arquivos podem ser agrupados simultaneamente por dois limites:

- `--batch-max-minutes`: minutagem máxima somada do batch;
- `--batch-max-files`: quantidade máxima de arquivos do batch.

Dentro de cada batch, `--workers` define quantos áudios são processados em
paralelo. Como o OpenAI Whisper é pesado em CPU e memória, esta primeira versão limita
a execução a um worker e reutiliza a mesma instância nos próximos arquivos.

Para áudios com durações variadas, prefira controlar pela minutagem. O limite
por quantidade funciona como uma proteção adicional.

Comece com um worker:

```text
TRANSCRIPTION_WORKERS=1
BATCH_MAX_FILES=100
BATCH_MAX_MINUTES=60
```

Mais workers não significam necessariamente mais velocidade. Cada worker
consome memória e disputa os núcleos da CPU. Faça benchmarks com 1, 2 e 4
workers e mantenha a configuração que produzir o menor tempo total sem
esgotar memória ou deixar o servidor indisponível.

## JSON para a futura tela

O JSON foi preparado para permitir que uma API salve a transcrição no banco e
que a tela sincronize cada turno de fala com o player de áudio. Todos os tempos
são armazenados em segundos e podem ser utilizados diretamente em
`audio.currentTime`.

Estrutura resumida:

```json
{
  "schema_version": "1.0",
  "transcription_id": "uuid",
  "status": "completed",
  "audio": {
    "audio_id": "chamada-123",
    "file_name": "chamada-123.wav",
    "source_path": "C:\\audios\\chamada-123.wav",
    "storage_key": null,
    "playback_url": null,
    "sample_rate_hz": 8000,
    "channels": 2,
    "duration_seconds": 107.0
  },
  "speakers": [
    {"channel": 0, "speaker": "cliente"},
    {"channel": 1, "speaker": "operador"}
  ],
  "timeline": [
    {
      "id": "turn-000001",
      "channel": 0,
      "speaker": "cliente",
      "start_seconds": 0.32,
      "end_seconds": 2.41,
      "text": "bom dia falo com carlos"
    }
  ],
  "processing": {
    "engine": "openai-whisper",
    "device": "cpu",
    "duration_seconds": 18.2,
    "realtime_factor": 0.17
  }
}
```

`playback_url` e `storage_key` permanecem nulos nesta etapa. Futuramente serão
preenchidos pela API ou pelo serviço de armazenamento do sistema interno.

O campo `timeline` intercala operador e cliente por horário. Segmentos
consecutivos do mesmo participante são unidos até que o outro lado comece a
falar. Isso reduz bastante o JSON e representa melhor a conversa na tela.
`channels` mantém também o texto completo de cada lado da ligação.

## Logs

Para cada áudio, o log registra:

- nome e início do processamento;
- duração, canais, amostragem e codec;
- tempo total de processamento;
- RTF e quantidade de frases;
- caminho do JSON;
- exceção completa quando ocorrer uma falha.

O lote registra ainda quantidade de arquivos, sucessos, falhas, minutos totais
e RTF agregado. Nenhuma informação é gravada em banco nesta etapa.

## Métrica de velocidade

O JSON inclui `realtime_factor`:

- menor que `1.0`: processa mais rápido que a duração do áudio;
- igual a `1.0`: uma hora de processamento para uma hora de áudio;
- maior que `1.0`: mais lento que tempo real.

Para 1.500 minutos por dia:

- janela de 24 horas: RTF agregado precisa ficar abaixo de `0,96`;
- janela de 8 horas: RTF agregado precisa ficar abaixo de `0,32`.

Esses limites devem considerar concorrência, horários de pico e margem para
falhas. Não dimensione produção usando apenas um áudio.

## Critério de comparação

Use exatamente o mesmo conjunto de áudios nas duas frentes e compare:

- WER geral;
- valores monetários;
- datas;
- negativas e confirmações;
- termos de negociação;
- tempo de processamento;
- memória;
- taxa de falhas.
