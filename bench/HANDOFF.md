# Bench handoff

Goal: choose the best raw model for EN → pt-BR subtitle translation on the RTX 5080 (16 GB). Any model that runs partly on the CPU is rejected. fix-translations is not part of the bench: the bench scores raw model output.

## State

- Code: `bench/bench.py`, `bench/score.py` (COMET + chrF++, own uv env), `bench/nmt.py` (CTranslate2, own uv env), `bench/qe.py` (xCOMET, written, never run, not wired), `bench/justfile` (`all`, `report`, `compare`, `runs`, `models`), `bench/MODELS.txt` (models table and discarded models).
- Run with `just bench all` from the project root. It skips evaluated runs, starts and stops each llama server, translates, runs check-translate, scores. NMT runs come first.
- Sources: Fall (2022) SDH, 1587 entries; The Silence of the Lambs base, 1264 entries.
- References in `bench/2-references`: Claude translations of both (committed), official pt-BR of The Silence of the Lambs (copied, not committed). Claude vs official: COMET 85.67, chrF++ 61.78.
- Models by type: GGUF in `D:/AI-Models-LMStudio`, CTranslate2 in `D:/AI-Models-CTranslate2`, PyTorch (COMET) in `D:/AI-Models-PyTorch` (Hugging Face cache layout, passed through `HF_HUB_CACHE`).

## Results so far

| run | movie | check score | COMET official | chrF++ official | COMET claude | chrF++ claude |
|---|---|---|---|---|---|---|
| gemma-4-26b (baseline) | Fall | 3.2 (13 issues) | - | - | 89.47 | 69.93 |
| gemma-4-26b (baseline) | Silence | 1.2 (4 issues) | 85.31 | 59.47 | 91.10 | 74.31 |

- Reading of gemma-4-26b on Fall: gender errors when the speaker or listener is a woman (seguro, orgulhoso, maluco); profanity and units right.
- gemma-4-12b: Fall translated, Silence not finished when the bench was stopped.
- check-translate on the Claude Fall reference: score 1.8, all 6 flags false positives. That is the floor of check-translate.

## CTranslate2 on the 5080 (solved)

- The slow NMT runs came from a cuBLAS without Blackwell (`sm_120`) kernels. There is no CUDA toolkit on the machine; `nmt.py` now depends on `nvidia-cublas-cu12>=12.8` and puts its `bin` folder on `PATH`. With it, NLLB 1.3B takes 15 s on the first call (one-time PTX compile by the driver), then 0.2 s per sentence and 0.5 s for a batch of 64, in both `float16` and `int8_float16`.
- The transformers warning about an incorrect regex pattern in the tokenizer of `JustFrederik/nllb-200-distilled-600M-ct2-int8` is a false alarm: it tokenizes the same as the OpenNMT NLLB repos.
- NLLB has only `por_Latn`, and its output is European Portuguese ("Disse-te para não escalares aquela torre, mas nunca me ouves."). Expect it to lose to the LLMs on that alone.

## Other open points

- Downloads still needed for some LLM runs: `just bench models` (mistral-small-3.2-24b, ministral-3-14b, qwen-3-14b, qwen-3.5-9b).
- gemma-4-26b-thinking (reasoning budget 2048) may fail if llama.cpp applies the JSON schema to the thinking; check its trace.
- xCOMET check (`qe.py`): needs the license accepted at https://huggingface.co/Unbabel/XCOMET-XL and `hf auth login`; then wire it into `bench.py` and judge whether its spans can become a check-translate rule (Claude references should get almost no major spans).
- `HF_HOME` user variable still points to the removed `D:\AI-Models-HuggingFace`; the Hugging Face library recreates it for its `xet` download cache. Proposed: set `HF_XET_CACHE=D:/Cache/huggingface-xet` in the bench, then remove `HF_HOME`.
- After the runs: `just bench report`, then read `bench/4-compare` on a fixed set of entries per category (gender, você/o senhor, profanity, units, descriptions and labels, songs, wordplay). Decision rule: a model replaces gemma-4-26b only if it beats it clearly on COMET against the Claude reference in both movies, is not worse on check-translate and in the reading, and fits the 5080.
