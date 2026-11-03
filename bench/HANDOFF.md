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

## Open problem: CTranslate2 is very slow on the 5080

- MADLAD 10B and NLLB 600M both leave the GPU at 0-25% use and 40-80 W; 200 entries with NLLB 600M took more than 5 minutes (should be seconds). No VRAM spill to shared memory, no CPU fallback.
- Suspect: the CTranslate2 Windows wheel has no kernels for Blackwell (`sm_120`), so it runs a slow path.
- Next: time model load and repeated single translations with `float16` and `int8_float16` (scratch script idea: load, translate one sentence 4 times, print times). If the first call is slow and later calls are fast, it is a one-time kernel compile; otherwise CTranslate2 is unusable here and the NMT models should run on PyTorch (transformers, `torch` cu128) in `nmt.py` instead, or be dropped.
- Until fixed, `just bench all` gets stuck on the NMT runs, which run first. Either fix `nmt.py` or move the NMT runs to the end of `RUNS` in `bench.py`.

## Other open points

- Downloads still needed for some LLM runs: `just bench models` (mistral-small-3.2-24b, ministral-3-14b, qwen-3-14b, qwen-3.5-9b).
- gemma-4-26b-thinking (reasoning budget 2048) may fail if llama.cpp applies the JSON schema to the thinking; check its trace.
- xCOMET check (`qe.py`): needs the license accepted at https://huggingface.co/Unbabel/XCOMET-XL and `hf auth login`; then wire it into `bench.py` and judge whether its spans can become a check-translate rule (Claude references should get almost no major spans).
- `HF_HOME` user variable still points to the removed `D:\AI-Models-HuggingFace`; the Hugging Face library recreates it for its `xet` download cache. Proposed: set `HF_XET_CACHE=D:/Cache/huggingface-xet` in the bench, then remove `HF_HOME`.
- After the runs: `just bench report`, then read `bench/4-compare` on a fixed set of entries per category (gender, você/o senhor, profanity, units, descriptions and labels, songs, wordplay). Decision rule: a model replaces gemma-4-26b only if it beats it clearly on COMET against the Claude reference in both movies, is not worse on check-translate and in the reading, and fits the 5080.
