# videos-subs

Scripts that make Brazilian Portuguese subtitles for the videos in a media library. They extract the subtitle tracks of each video, identify their languages, OCR English image subtitles (PGS and VobSub) into text subtitles with a local vision LLM, and translate English text subtitles to Brazilian Portuguese with a local LLM. A fix step corrects the translation mistakes that have a mechanical fix, such as misspelled sound descriptions, from the English source. Two check steps score the OCR outputs and the translations and write a report of the issues of each file.

The output is `<video>.<track>.pob.translated.srt` (`.pob.claude.translated.srt` when Claude made it) next to each video, where the media server loads it.

## Requirements

These tools on the `PATH`:

- [just](https://github.com/casey/just), [uv](https://github.com/astral-sh/uv) and PowerShell 7 (`pwsh`), which runs the recipes
- ffmpeg and ffprobe
- Tesseract with the `script/Latin` model (scoop `tesseract` and `tesseract-languages`)
- Java for LanguageTool (scoop `openjdk26`)
- llama.cpp with CUDA, and the Gemma 4 12B (vision) and Gemma 4 26B A4B GGUF models; paths are set in the `llama-ocr` and `llama-translate` recipes

## Usage

The `justfile` has a recipe for setup and for each step; `just` lists them. The docs:

- [docs/PIPELINE.md](docs/PIPELINE.md): each step.
- [docs/RULES.md](docs/RULES.md): the check rules.
- [docs/BENCH.md](docs/BENCH.md): the bench of translation models, and the models it covers.
- [docs/FAILURES.md](docs/FAILURES.md): the approaches that were tried and dropped, and why.
- [AGENTS.md](AGENTS.md): the principle, the terms, the media layout and the code layout.
