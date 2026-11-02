set windows-shell := ["pwsh.exe", "-NoLogo", "-NoProfile", "-Command"]
set script-interpreter := ["pwsh.exe", "-NoLogo", "-NoProfile"]

export UV_PROJECT_ENVIRONMENT := data_local_directory() / "uv" / "envs" / "videos-subs"
dictionary_dir := data_local_directory() / "hunspell"

[group('project')]
[doc('List recipes')]
default:
    @just --list --unsorted

[group('project')]
[doc('Install dependencies')]
setup: dictionary
    uv sync

[group('project')]
[doc('Download Brazilian Portuguese dictionary')]
dictionary:
    New-Item -ItemType Directory -Force -Path '{{dictionary_dir}}' | Out-Null
    Invoke-WebRequest -Uri https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.dic -OutFile '{{dictionary_dir}}/pt_BR.dic'
    Invoke-WebRequest -Uri https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.aff -OutFile '{{dictionary_dir}}/pt_BR.aff'

[group('project')]
[doc('List line counts')]
cloc:
    cloc --exclude-dir=__pycache__ .

[group('run')]
[doc('Extract subtitle tracks')]
[script]
[extension('.ps1')]
[positional-arguments]
extract *paths:
    uv run 1-extract-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Identify text subtitle languages')]
[script]
[extension('.ps1')]
[positional-arguments]
identify-text *paths:
    uv run 2-identify-text-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Identify image subtitle languages')]
[script]
[extension('.ps1')]
[positional-arguments]
identify-ocr *paths:
    uv run 3-identify-ocr-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('OCR image subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
ocr *paths:
    uv run 4-ocr-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Check OCR')]
[script]
[extension('.ps1')]
[positional-arguments]
check-ocr *paths:
    uv run 5-check-ocr-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Translate subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
translate *paths:
    uv run 6-translate-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Check translations')]
[script]
[extension('.ps1')]
[positional-arguments]
check-translate *paths:
    uv run 7-check-translations.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Add warnings to translations')]
[script]
[extension('.ps1')]
[positional-arguments]
warn *paths:
    uv run 8-warn-subs.py @args
    exit $LASTEXITCODE

[group('llm')]
[doc('Start OCR llama server')]
[env('GGML_CUDA_PDL', '1')]
[env('GGML_CUDA_GRAPH_OPT', '1')]
llama-ocr:
    C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe \
        --model D:/AI-Models-LMStudio/lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf \
        --mmproj D:/AI-Models-LMStudio/lmstudio-community/gemma-4-12B-it-QAT-GGUF/mmproj-gemma-4-12B-it-QAT-BF16.gguf \
        --device CUDA0 --n-gpu-layers all --fit off --flash-attn on --jinja --reasoning-budget 0 \
        --ctx-size 32768 --parallel 8 --top-p 0.95 --top-k 64 --port 9931

[group('llm')]
[doc('Start translation llama server')]
[env('GGML_CUDA_PDL', '1')]
[env('GGML_CUDA_GRAPH_OPT', '1')]
llama-translate:
    C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe \
        --model D:/AI-Models-LMStudio/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF/gemma-4-26B-A4B-it-QAT-Q4_0.gguf \
        --device CUDA0 --n-gpu-layers all --fit off --flash-attn on --jinja --reasoning-budget 0 --no-mmproj \
        --ctx-size 16384 --parallel 1 --top-p 0.95 --top-k 64 --port 9931

[group('data')]
[doc('Crosscheck subtitles, translations and reports')]
[script]
[extension('.ps1')]
[positional-arguments]
crosscheck *paths:
    uv run crosscheck-subs.py @args
    exit $LASTEXITCODE

[group('data')]
[doc('Delete check reports')]
[confirm('Delete every _checks folder under the given paths, or under Z:/midias/filmes and Z:/midias/series without paths? (y/N)')]
[script]
[extension('.ps1')]
[positional-arguments]
reset-checks *paths:
    $folders = if ($args) { $args } else { 'Z:/midias/filmes', 'Z:/midias/series' }
    Get-ChildItem -Path $folders -Directory -Recurse -Filter _checks | Remove-Item -Recurse -Force

[group('data')]
[doc('Remove English language codes from subtitles')]
[confirm('Remove the eng and eng.sdh codes from every subtitle in _subs and every OCR trace in _traces under the given paths, or under Z:/midias/filmes and Z:/midias/series without paths? (y/N)')]
[script]
[extension('.ps1')]
[positional-arguments]
reset-en *paths:
    $folders = if ($args) { $args } else { 'Z:/midias/filmes', 'Z:/midias/series' }
    $patterns = @{ '_subs' = '\.eng(\.sdh)?(\.(srt|sup|mks))$'; '_traces' = '\.eng(\.sdh)?(\.ocr\.json)$' }
    Get-ChildItem -LiteralPath $folders -File -Recurse | Where-Object { $patterns.ContainsKey($_.Directory.Name) -and $_.Name -match $patterns[$_.Directory.Name] } | ForEach-Object {
        $name = $_.Name -replace $patterns[$_.Directory.Name], '$2'
        Rename-Item -LiteralPath $_.FullName -NewName $name
        Write-Output $name
    }

[group('data')]
[doc('Remove Portuguese language codes from subtitles')]
[confirm('Remove the por and pob language codes from every subtitle in _subs under the given paths, or under Z:/midias/filmes and Z:/midias/series without paths? (y/N)')]
[script]
[extension('.ps1')]
[positional-arguments]
reset-pt *paths:
    $folders = if ($args) { $args } else { 'Z:/midias/filmes', 'Z:/midias/series' }
    Get-ChildItem -LiteralPath $folders -File -Recurse | Where-Object { $_.Directory.Name -eq '_subs' -and $_.Name -match '\.(por|pob)\.(srt|sup|mks)$' } | ForEach-Object {
        $name = $_.Name -replace '\.(por|pob)\.(srt|sup|mks)$', '.$2'
        Rename-Item -LiteralPath $_.FullName -NewName $name
        Write-Output $name
    }
