set windows-shell := ["pwsh.exe", "-NoLogo", "-NoProfile", "-Command"]
set script-interpreter := ["pwsh.exe", "-NoLogo", "-NoProfile"]

export UV_PROJECT_ENVIRONMENT := data_local_directory() / "uv" / "envs" / "videos-subs"
export PYTHONPATH := source_directory() / "src"
dictionary_dir := data_local_directory() / "hunspell"

mod bench

[group('project')]
[doc('List recipes')]
default:
    @just --list --unsorted

[group('project')]
[doc('Install dependencies and Brazilian Portuguese dictionary')]
setup:
    uv sync
    New-Item -ItemType Directory -Force -Path '{{dictionary_dir}}' | Out-Null
    Invoke-WebRequest -Uri https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.dic -OutFile '{{dictionary_dir}}/pt_BR.dic'
    Invoke-WebRequest -Uri https://raw.githubusercontent.com/LibreOffice/dictionaries/master/pt_BR/pt_BR.aff -OutFile '{{dictionary_dir}}/pt_BR.aff'

[group('project/report')]
[doc('List line counts')]
cloc:
    cloc --exclude-dir=__pycache__ .

[group('run')]
[doc('Extract subtitle tracks')]
[script]
[extension('.ps1')]
[positional-arguments]
extract *paths:
    uv run src/1-extract-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Identify text subtitle languages')]
[script]
[extension('.ps1')]
[positional-arguments]
identify-text *paths:
    uv run src/2-identify-text-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Identify image subtitle languages')]
[script]
[extension('.ps1')]
[positional-arguments]
identify-ocr *paths:
    uv run src/3-identify-ocr-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('OCR image subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
ocr *paths:
    uv run src/4-ocr-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Check OCR')]
[script]
[extension('.ps1')]
[positional-arguments]
check-ocr *paths:
    uv run src/5-check-ocr-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Translate subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
translate *paths:
    uv run src/6-translate-subs.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Fix translations mechanically')]
[script]
[extension('.ps1')]
[positional-arguments]
fix-translations *paths:
    uv run src/7-fix-translations.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Check translations')]
[script]
[extension('.ps1')]
[positional-arguments]
check-translate *paths:
    uv run src/8-check-translations.py @args
    exit $LASTEXITCODE

[group('run')]
[doc('Add warnings to translations')]
[script]
[extension('.ps1')]
[positional-arguments]
warn *paths:
    uv run src/9-warn-subs.py @args
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

[group('test')]
[doc('Run unit tests')]
[script]
[extension('.ps1')]
[positional-arguments]
test *args:
    uv run pytest @args
    exit $LASTEXITCODE

[group('data')]
[doc('Delete OCR reports')]
[confirm('Delete every OCR report in _checks under the given paths, or under Z:/midias/filmes and Z:/midias/series without paths? (y/N)')]
[script]
[extension('.ps1')]
[positional-arguments]
reset-checks-ocr *paths:
    $folders = if ($args) { $args } else { 'Z:/midias/filmes', 'Z:/midias/series' }
    Get-ChildItem -LiteralPath $folders -File -Recurse -Filter '*.ocr.txt' | Where-Object { $_.Directory.Name -eq '_checks' } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName
        Write-Output $_.Name
    }

[group('data')]
[doc('Delete translation reports')]
[confirm('Delete every translation report in _checks under the given paths, or under Z:/midias/filmes and Z:/midias/series without paths? (y/N)')]
[script]
[extension('.ps1')]
[positional-arguments]
reset-checks-translate *paths:
    $folders = if ($args) { $args } else { 'Z:/midias/filmes', 'Z:/midias/series' }
    Get-ChildItem -LiteralPath $folders -File -Recurse -Filter '*.translated.txt' | Where-Object { $_.Directory.Name -eq '_checks' } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName
        Write-Output $_.Name
    }

[group('data')]
[doc('Remove English language codes from subtitles')]
[confirm('Remove the eng and eng.sdh codes from every subtitle in _subs and every OCR trace in _traces under the given paths, or under Z:/midias/filmes and Z:/midias/series without paths? (y/N)')]
[script]
[extension('.ps1')]
[positional-arguments]
reset-identifier-en *paths:
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
reset-identifier-pt *paths:
    $folders = if ($args) { $args } else { 'Z:/midias/filmes', 'Z:/midias/series' }
    Get-ChildItem -LiteralPath $folders -File -Recurse | Where-Object { $_.Directory.Name -eq '_subs' -and $_.Name -match '\.(por|pob)\.(srt|sup|mks)$' } | ForEach-Object {
        $name = $_.Name -replace '\.(por|pob)\.(srt|sup|mks)$', '.$2'
        Rename-Item -LiteralPath $_.FullName -NewName $name
        Write-Output $name
    }

[group('data/fix')]
[doc('Apply fixes decided by hand to subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
apply-fixes fixes *options:
    uv run src/fix-tools/apply-fixes.py @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add right words to check-translate')]
[script]
[extension('.ps1')]
[positional-arguments]
add-check-words +words:
    uv run src/fix-tools/add-to-dictionary.py check-words @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add right expressions to check-translate')]
[script]
[extension('.ps1')]
[positional-arguments]
add-check-expressions +expressions:
    uv run src/fix-tools/add-to-dictionary.py check-expressions @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add description translations to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-sounds +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-sounds @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add music descriptions to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-music +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-music @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add language descriptions to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-languages +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-languages @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add channel descriptions to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-channels +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-channels @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add object sounds to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-objects +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-objects @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add description manners to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-manners +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-manners @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add music moods to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-music-moods +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-music-moods @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add voice moods to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-voice-moods +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-voice-moods @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add description subjects to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-subjects +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-subjects @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add interjections to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-interjections +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-interjections @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add ranks to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-ranks +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-ranks @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add forms of address to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-addresses +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-addresses @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add units to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-units +pairs:
    uv run src/fix-tools/add-to-dictionary.py fix-units @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add wrong expression translations to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-expressions +triples:
    uv run src/fix-tools/add-to-dictionary.py fix-expressions @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add Portugal words to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-portugal +triples:
    uv run src/fix-tools/add-to-dictionary.py fix-portugal @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add misspellings to fix-translations')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-words +triples:
    uv run src/fix-tools/add-to-dictionary.py fix-words @args
    exit $LASTEXITCODE

[group('debug')]
[doc('Crosscheck subtitles, translations and reports')]
[script]
[extension('.ps1')]
[positional-arguments]
crosscheck *paths:
    uv run src/crosscheck-subs.py @args
    exit $LASTEXITCODE

[group('debug')]
[doc('Collect English subtitle statistics')]
[script]
[extension('.ps1')]
[positional-arguments]
stats *paths:
    uv run src/stats-subs.py @args
    exit $LASTEXITCODE

[group('debug')]
[doc('Print system prompt and first chunk of each English subtitle')]
[script]
[extension('.ps1')]
[positional-arguments]
print-prompt *paths:
    uv run src/6-translate-subs.py --print-prompt @args
    exit $LASTEXITCODE
alias pp := print-prompt
