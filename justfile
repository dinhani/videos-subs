set windows-shell := ["pwsh.exe", "-NoLogo", "-NoProfile", "-Command"]
set script-interpreter := ["pwsh.exe", "-NoLogo", "-NoProfile"]

export UV_PROJECT_ENVIRONMENT := data_local_directory() / "_dev" / "uv" / "envs" / "videos-subs"
export UV_FROZEN := "1"
export PYTHONPATH := source_directory() / "src"
dictionary_dir := data_local_directory() / "hunspell"

[doc('Benchmark translation models')]
[group('modules')]
mod bench

[doc('Run helper tools')]
[group('modules')]
mod tools

[group('project')]
[doc('List recipes')]
default:
    @just --list --unsorted

[group('project')]
[doc('Install dependencies and Brazilian Portuguese dictionary')]
setup:
    Remove-Item Env:UV_FROZEN; uv sync
    New-Item -ItemType Directory -Force -Path '{{dictionary_dir}}' | Out-Null
    Invoke-WebRequest -Uri https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.dic -OutFile '{{dictionary_dir}}/pt_BR.dic'
    Invoke-WebRequest -Uri https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.aff -OutFile '{{dictionary_dir}}/pt_BR.aff'

[group('project/report')]
[doc('List line counts')]
cloc:
    cloc --exclude-dir=__pycache__ .

[group('run/extract')]
[doc('Extract subtitle tracks')]
[script]
[extension('.ps1')]
[positional-arguments]
extract-tracks *paths:
    uv run src/extract-tracks.py @args
    exit $LASTEXITCODE
alias extract := extract-tracks

[group('run/identify')]
[doc('Identify text subtitle languages')]
[script]
[extension('.ps1')]
[positional-arguments]
identify-text *paths:
    uv run src/identify-text.py @args
    exit $LASTEXITCODE

[group('run/identify')]
[doc('Identify image subtitle languages')]
[script]
[extension('.ps1')]
[positional-arguments]
identify-images *paths:
    uv run src/identify-images.py @args
    exit $LASTEXITCODE

[group('run/ocr')]
[doc('OCR image subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
ocr-transcribe *paths:
    uv run src/ocr-transcribe.py @args
    exit $LASTEXITCODE
alias ocr := ocr-transcribe

[group('run/ocr')]
[doc('Check OCR')]
[script]
[extension('.ps1')]
[positional-arguments]
ocr-check *paths:
    uv run src/ocr-check.py @args
    exit $LASTEXITCODE

[group('run/translate')]
[doc('Translate subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-llama *paths:
    uv run src/translate-llama.py @args
    exit $LASTEXITCODE
alias translate := translate-llama

[group('run/translate')]
[doc('Fix translations mechanically')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-fix *paths:
    uv run src/translate-fix.py @args
    exit $LASTEXITCODE
alias fix := translate-fix

[group('run/translate')]
[doc('Check translations')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-check *paths:
    uv run src/translate-check.py @args
    exit $LASTEXITCODE
alias check := translate-check

[group('run/translate')]
[doc('Add warnings to translations')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-warn *paths:
    uv run src/translate-warn.py @args
    exit $LASTEXITCODE
alias warn := translate-warn

[group('run/translate')]
[doc('Write translations without SDH')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-remove-sdh *paths:
    uv run src/translate-remove-sdh.py @args
    exit $LASTEXITCODE
alias nosdh := translate-remove-sdh

[group('llm')]
[doc('Start OCR llama server')]
[env('GGML_CUDA_PDL', '1')]
[env('GGML_CUDA_GRAPH_OPT', '1')]
llama-ocr:
    C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe \
        --model D:/AI/lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf \
        --mmproj D:/AI/lmstudio-community/gemma-4-12B-it-QAT-GGUF/mmproj-gemma-4-12B-it-QAT-BF16.gguf \
        --device CUDA0 --n-gpu-layers all --fit off --flash-attn on --jinja --reasoning-budget 0 \
        --ctx-size 32768 --parallel 8 --top-p 0.95 --top-k 64 --port 9931

[group('llm')]
[doc('Start translation llama server')]
[env('GGML_CUDA_PDL', '1')]
[env('GGML_CUDA_GRAPH_OPT', '1')]
llama-translate:
    C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe \
        --model D:/AI/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF/gemma-4-26B-A4B-it-QAT-Q4_0.gguf \
        --device CUDA0 --n-gpu-layers all --fit off --flash-attn on --jinja --reasoning-budget 0 --no-mmproj \
        --ctx-size 16384 --parallel 1 --top-p 0.95 --top-k 64 --port 9931

[group('test')]
[doc('Run unit tests')]
[script]
[extension('.ps1')]
[positional-arguments]
test *args:
    uv run pytest @args
    exit $LASTEXITCODE

[group('debug')]
[doc('Print system prompt and first chunk of each English subtitle')]
[script]
[extension('.ps1')]
[positional-arguments]
print-prompt *paths:
    uv run src/translate-llama.py --print-prompt @args
    exit $LASTEXITCODE
alias pp := print-prompt
