# videos-subs

Scripts that make Brazilian Portuguese subtitles for the videos in a media library. They extract the subtitle tracks of each video, identify their languages, OCR English image subtitles (PGS and VobSub) into text subtitles with a local vision LLM, and translate English text subtitles to Brazilian Portuguese with a local LLM. Two check steps score the OCR outputs and the translations and write a report of the issues of each file.

The output is `<video>.<track>.pob.translated.srt` next to each video, where the media server loads it.

## Requirements

These tools on the `PATH`:

- [just](https://github.com/casey/just), [uv](https://github.com/astral-sh/uv) and PowerShell 7 (`pwsh`), which runs the recipes
- ffmpeg and ffprobe
- Tesseract with the `script/Latin` model (scoop `tesseract` and `tesseract-languages`)
- Java for LanguageTool (scoop `openjdk26`)
- llama.cpp with CUDA, and the Gemma 4 12B (vision) and Gemma 4 26B A4B GGUF models; paths are set in the `llama-ocr` and `llama-translate` recipes

## Setup

```
just setup
```

Installs the Python dependencies and downloads the pt_BR hunspell dictionary.

## Usage

Run the steps in order. Each step skips files it already processed. Without paths, a step processes the whole media library; with paths, only those folders or files.

```
just extract [paths...]
just identify-text [paths...]
just identify-ocr [paths...]
just llama-ocr            # in another terminal
just ocr [paths...]
just llama-translate      # in another terminal, after stopping llama-ocr
just translate [paths...]
just check-ocr [paths...]
just check-translate [paths...]
```

`just` without arguments lists every recipe. [AGENTS.md](AGENTS.md) describes each step, the file layout and the check rules in detail.
