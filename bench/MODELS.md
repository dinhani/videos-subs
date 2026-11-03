# Bench models

The models and runs of the benchmark. `bench.py` holds the same list in `MODELS` and `RUNS`; update this file when it changes.

## Runs

| Run | Model | Maker | Kind | Architecture | Parameters | Quant | File | Fits 16 GB | Mode |
|---|---|---|---|---|---|---|---|---|---|
| gemma-4-26b | Gemma 4 26B A4B (baseline, the model `just llama-translate` uses) | Google | general | MoE | 26B total, 4B active | QAT Q4_0 | 14.4 GB | yes | pipeline |
| gemma-4-31b | Gemma 4 31B | Google | general | dense | 31B | QAT Q4_0 | 17.7 GB | no, part on CPU (`--fit on`) | pipeline |
| gemma-4-12b | Gemma 4 12B | Google | general | dense | 12B | QAT Q4_0 | 7.0 GB | yes | pipeline |
| qwen-3.6-35b | Qwen3.6 35B A3B | Alibaba | general | MoE | 35B total, 3B active | Q4_K_M | 21.2 GB | no, part on CPU (`--fit on`) | pipeline |
| hy-mt2-7b | Hy-MT2 7B (May 2026) | Tencent | translation | dense (HunYuan) | 7B | Q8_0 | 8.0 GB | yes | native |
| tower-plus-9b | Tower+ 9B (2025), on Gemma 2 9B | Unbabel | translation | dense | 9B | Q8_0 | 9.8 GB | yes | native |
| translategemma-12b | TranslateGemma 12B (January 2026), on Gemma 3 12B | Google | translation | dense | 12B | Q6_K | 9.7 GB | yes | native |

Files are in `D:/AI-Models-LMStudio/<repo>/<file>`:

| Run | Repo | File |
|---|---|---|
| gemma-4-26b | lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF | gemma-4-26B-A4B-it-QAT-Q4_0.gguf |
| gemma-4-31b | lmstudio-community/gemma-4-31B-it-QAT-GGUF | gemma-4-31B-it-QAT-Q4_0.gguf |
| gemma-4-12b | lmstudio-community/gemma-4-12B-it-QAT-GGUF | gemma-4-12B-it-QAT-Q4_0.gguf |
| qwen-3.6-35b | lmstudio-community/Qwen3.6-35B-A3B-GGUF | Qwen3.6-35B-A3B-Q4_K_M.gguf |
| hy-mt2-7b | tencent/Hy-MT2-7B-GGUF | HY-MT2-7B-Q8_0.gguf |
| tower-plus-9b | mradermacher/Tower-Plus-9B-GGUF | Tower-Plus-9B.Q8_0.gguf |
| translategemma-12b | mradermacher/translategemma-12b-it-GGUF | translategemma-12b-it.Q6_K.gguf |

## Modes

- **pipeline**: the real translate step (`src/6-translate-subs.py`), unchanged: chunks of 50 entries with context before and after, a JSON schema reply, the project prompt. Thinking is off (`--reasoning-budget 0`), context 16384, one slot.
- **native**: each entry alone, with the prompt of the model card, through the model's own chat template; 8 parallel slots, context 8192. A reply with another number of lines than the English entry is split by words to the English line count, and the trace counts these entries.

## Prompts and sampling of the native runs

| Run | Prompt | Sampling |
|---|---|---|
| hy-mt2-7b | `Translate the following text into Brazilian Portuguese. Note that you should only output the translated result without any additional explanation:` followed by a blank line and the entry | temperature 0.7, top_p 0.6, top_k 20, repeat penalty 1.05 (model card) |
| tower-plus-9b | `Translate the following English source text to Portuguese (Brazilian):` / `English: <entry>` / `Portuguese (Brazilian): ` | greedy (model card) |
| translategemma-12b | structured content: source language `en`, target language `pt-BR`, text | greedy (model card) |

## Notes

- **gemma-4-26b**: the current model of the pipeline; every other run is compared with it.
- **gemma-4-31b** and **qwen-3.6-35b**: do not fit the GPU, so some layers run on the CPU and translation is several times slower.
- **hy-mt2-7b**: the newest translation model of the list, with official GGUF files. The model card does not distinguish Brazilian from European Portuguese.
- **tower-plus-9b**: the only model whose card names Brazilian Portuguese as its own target.
- **translategemma-12b**: its chat template has broken in llama.cpp more than once (language codes dropped, template rejected). A failed run or a reply in the wrong language most likely comes from that.

## Left out

| Model | Reason |
|---|---|
| TranslateGemma 27B | Q4_K_M is 16.5 GB: no room left for context in 16 GB |
| Hunyuan-MT-7B, HY-MT1.5-7B | replaced by Hy-MT2 7B |
| Hunyuan-MT-Chimera-7B | not a translator: it merges 6 candidate translations into one |
| Seed-X 7B (ByteDance) | the card asks for beam search, which llama-server does not do, and has no Brazilian Portuguese |
| Sabiá (Maritaca) | the good versions are API only, not open weights |
