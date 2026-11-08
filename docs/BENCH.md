# Bench

`bench` benchmarks translation models on English text subtitles from the library. It has its own `justfile`, loaded as the `bench` module: `just bench` lists its recipes. Only `just bench models` downloads models, and `just bench run --movie <text>` translates only the sources whose title contains the text, for quick numbers.

## Folders

- `1-sources/`: the copy of each source: its English subtitle, its `.nfo` and its second-language subtitles.
- `2-references/`: the reference translations. Only `2-references/*/<name>.claude.srt` is committed.
- `3-runs/`: one folder per run with its translations. The bench copies the `.nfo` of each source into the movie folder of each run, so translate finds it as in the library.
- `4-compare/`: the output of `just bench compare`.

## Runs

- A run of a model translates with `LLM_PROMPT` and the English alone, and scores the raw model output, without fix-translations.
- A run `gemma-4-26b+<language code>`, such as `gemma-4-26b+spa`, translates with `--second-languages <language code>`. Each run copies the second-language subtitles of every source into its `_subs`, as they are in the library; a source without one in that language is translated from the English alone.
- A run `<run>-fix` copies the translations of its base run, applies fix-translations and scores the result. It keeps the translation time of the base run.
- `catalog.py` splits the runs into `RUNS_ACTIVE` and `RUNS_DISABLED`. Only the `gemma-4-26b` runs are active. `just bench run` translates only active runs and refuses a disabled one; `just bench compare` and `just bench models` default to the active runs. A disabled run keeps its translations, reports and scores: `just bench score`, `just bench report` and `just bench runs` still cover every run.

## Code

- `bench.py`: the command line.
- `catalog.py`: the sources, models and runs.
- `models.py`: the model types and the llama server.
- `runs.py`: the translators, sources, runs and scoring.
- `report.py`: the report and compare tables.
- `nmt.py` (CTranslate2) and `score.py` (COMET and chrF++): standalone uv scripts with their own environments, since unbabel-comet needs numpy 1.

The bench justfile puts `src` on `PYTHONPATH` so these modules import `shared`.

Models are stored by type: GGUF in `D:/AI`, CTranslate2 in `D:/AI-Models-CTranslate2`, PyTorch in `D:/AI-Models-PyTorch`.

## Models

| run | model | maker | kind | arch | parameters | quant | size | fits 5080 | mode |
|---|---|---|---|---|---|---|---|---|---|
| madlad-400-10b | MADLAD-400 10B | Google | translation | enc-dec | 10B | int8 | 10.7 GB | yes | nmt |
| madlad-400-7b | MADLAD-400 7B | Google | translation | enc-dec | 7B | int8 | 8.3 GB | yes | nmt |
| madlad-400-3b | MADLAD-400 3B | Google | translation | enc-dec | 3B | int8 | 3.0 GB | yes | nmt |
| nllb-200-3.3b | NLLB-200 3.3B | Meta | translation | enc-dec | 3.3B | int8 | 3.4 GB | yes | nmt |
| nllb-200-1.3b | NLLB-200 1.3B distilled | Meta | translation | enc-dec | 1.3B | int8 | 1.4 GB | yes | nmt |
| nllb-200-600m | NLLB-200 600M distilled | Meta | translation | enc-dec | 600M | int8 | 0.6 GB | yes | nmt |
| opus-mt-en-pt | Opus-MT tc-big en-pt | Helsinki | translation | enc-dec | about 240M | float16 | 0.5 GB | yes | nmt |
| hy-mt2-7b | Hy-MT2 7B | Tencent | translation | dense | 7B | Q8_0 | 8.0 GB | yes | pipeline |
| command-r7b | Command R7B | Cohere | general | dense | 7B | Q8_0 | 8.5 GB | yes | pipeline |
| aya-expanse-8b | Aya Expanse 8B | Cohere | general | dense | 8B | Q8_0 | 8.5 GB | yes | pipeline |
| tower-plus-9b | Tower+ 9B | Unbabel | translation | dense | 9B | Q8_0 | 9.8 GB | yes | pipeline |
| gemma-4-12b | Gemma 4 12B | Google | general | dense | 12B | QAT Q4_0 | 7.0 GB | yes | pipeline |
| gemma-3-12b | Gemma 3 12B | Google | general | dense | 12B | Q4_K_M | 7.3 GB | yes | pipeline |
| translategemma-12b | TranslateGemma 12B | Google | translation | dense | 12B | Q6_K | 9.7 GB | yes | pipeline |
| ministral-3-14b | Ministral 3 14B | Mistral | general | dense | 14B | Q6_K | 11.1 GB | yes | pipeline |
| qwen-3-14b | Qwen3 14B | Alibaba | general | dense | 14B | Q6_K | 12.1 GB | yes | pipeline |
| gpt-oss-20b | gpt-oss 20B, low effort | OpenAI | general | MoE | 21B total, 4B active | MXFP4 | 12.1 GB | yes | pipeline |
| ernie-4.5-21b | ERNIE 4.5 21B A3B | Baidu | general | MoE | 21B total, 3B active | Q4_K_M | 13.5 GB | yes | pipeline |
| mistral-small-3.2-24b | Mistral Small 3.2 24B | Mistral | general | dense | 24B | IQ4_XS | 12.8 GB | yes | pipeline |
| magistral-small-24b | Magistral Small 1.2 | Mistral | reasoning | dense | 24B | IQ4_XS | 12.8 GB | yes | pipeline |
| gemma-4-26b | Gemma 4 26B A4B | Google | general | MoE | 26B total, 4B active | QAT Q4_0 | 14.4 GB | yes | pipeline |
| hy-mt2-30b-iq3 | Hy-MT2 30B A3B | Tencent | translation | MoE | 30B total, 3B active | IQ3_XS | 12.4 GB | yes | pipeline |
| gemma-4-31b-iq3 | Gemma 4 31B | Google | general | dense | 31B | IQ3_XS | 13.8 GB | yes | pipeline |

## Discarded models

| model | reason |
|---|---|
| Gemma 3 27B | does not fit the 5080 with context |
| Qwen3.6 35B A3B | smallest IQ3 is 15.8 GB, does not fit the 5080; IQ2 is too low for 3B active |
| Qwen3.5 9B | thinks until the token limit even with thinking off, never replies |
| TranslateGemma 27B | loops on junk tokens at IQ3_XS |
| Granite 4.0 H-small | fits the 5080 only at Q2 |
| EuroLLM 9B and 22B | trained for European Portuguese |
| SalamandraTA 7B | trained for European Portuguese |
| Nemotron Nano 12B v2 | Mamba2 hybrid, weak GPU support in llama.cpp |
| Tiny Aya 3B | too small |
| Phi-4 14B | weak outside English |
| Hunyuan-MT-7B | replaced by Hy-MT2 7B |
| Hunyuan-MT-Chimera | merges candidate translations, does not translate |
| Seed-X 7B | needs beam search, not in llama-server |
| Argos Translate | smaller Opus-MT models, already covered by Opus-MT |
| Firefox Translations | browser runtime only |
| Sabia | API only |
