set windows-shell := ["pwsh.exe", "-NoLogo", "-NoProfile", "-Command"]
set script-interpreter := ["pwsh.exe", "-NoLogo", "-NoProfile"]

export UV_PROJECT_ENVIRONMENT := data_local_directory() / "_dev" / "uv" / "envs" / "videos-subs"
export UV_FROZEN := "1"
export PYTHONPATH := source_directory() / "src"
dictionary_dir := data_local_directory() / "hunspell"

[doc('Benchmark translation models')]
[group('modules')]
mod bench

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
translate-gemma *paths:
    uv run src/translate-gemma.py @args
    exit $LASTEXITCODE

[group('run/translate')]
[doc('Fix translations mechanically')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-fix *paths:
    uv run src/translate-fix.py @args
    exit $LASTEXITCODE

[group('run/translate')]
[doc('Check translations')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-check *paths:
    uv run src/translate-check.py @args
    exit $LASTEXITCODE

[group('run/translate')]
[doc('Add warnings to translations')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-warn *paths:
    uv run src/translate-warn.py @args
    exit $LASTEXITCODE

[group('run/translate')]
[doc('Write translations without SDH')]
[script]
[extension('.ps1')]
[positional-arguments]
translate-remove-sdh *paths:
    uv run src/translate-remove-sdh.py @args
    exit $LASTEXITCODE

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
[confirm('Remove the por, por.sdh, pob and pob.sdh codes from every subtitle in _subs and every OCR trace in _traces under the given paths, or under Z:/midias/filmes and Z:/midias/series without paths? (y/N)')]
[script]
[extension('.ps1')]
[positional-arguments]
reset-identifier-pt *paths:
    $folders = if ($args) { $args } else { 'Z:/midias/filmes', 'Z:/midias/series' }
    $patterns = @{ '_subs' = '\.(por|pob)(\.sdh)?(\.(srt|sup|mks))$'; '_traces' = '\.(por|pob)(\.sdh)?(\.ocr\.json)$' }
    Get-ChildItem -LiteralPath $folders -File -Recurse | Where-Object { $patterns.ContainsKey($_.Directory.Name) -and $_.Name -match $patterns[$_.Directory.Name] } | ForEach-Object {
        $name = $_.Name -replace $patterns[$_.Directory.Name], '$3'
        Rename-Item -LiteralPath $_.FullName -NewName $name
        Write-Output $name
    }

[group('data/fix')]
[doc('Apply fixes decided by hand to subtitles')]
[script]
[extension('.ps1')]
[positional-arguments]
apply-fixes fixes *options:
    uv run tools/apply-fixes.py @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add right words to translate-check')]
[script]
[extension('.ps1')]
[positional-arguments]
add-check-words +words:
    uv run tools/add-to-dictionary.py check-words @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add right expressions to translate-check')]
[script]
[extension('.ps1')]
[positional-arguments]
add-check-expressions +expressions:
    uv run tools/add-to-dictionary.py check-expressions @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add description translations to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-sounds +pairs:
    uv run tools/add-to-dictionary.py fix-sounds @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add music descriptions to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-music +pairs:
    uv run tools/add-to-dictionary.py fix-music @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add language descriptions to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-languages +pairs:
    uv run tools/add-to-dictionary.py fix-languages @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add channel descriptions to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-channels +pairs:
    uv run tools/add-to-dictionary.py fix-channels @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add object sounds to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-objects +pairs:
    uv run tools/add-to-dictionary.py fix-objects @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add description manners to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-manners +pairs:
    uv run tools/add-to-dictionary.py fix-manners @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add music moods to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-music-moods +pairs:
    uv run tools/add-to-dictionary.py fix-music-moods @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add voice moods to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-voice-moods +pairs:
    uv run tools/add-to-dictionary.py fix-voice-moods @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add description subjects to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-subjects +pairs:
    uv run tools/add-to-dictionary.py fix-subjects @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add interjections to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-interjections +pairs:
    uv run tools/add-to-dictionary.py fix-interjections @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add ranks to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-ranks +pairs:
    uv run tools/add-to-dictionary.py fix-ranks @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add forms of address to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-addresses +pairs:
    uv run tools/add-to-dictionary.py fix-addresses @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add units to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-units +pairs:
    uv run tools/add-to-dictionary.py fix-units @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add wrong expression translations to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-expressions +triples:
    uv run tools/add-to-dictionary.py fix-expressions @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add Portugal words to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-portugal +triples:
    uv run tools/add-to-dictionary.py fix-portugal @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add bare verbs to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-verbs +triples:
    uv run tools/add-to-dictionary.py fix-verbs @args
    exit $LASTEXITCODE

[group('data/fix')]
[doc('Add misspellings to translate-fix')]
[script]
[extension('.ps1')]
[positional-arguments]
add-fix-words +triples:
    uv run tools/add-to-dictionary.py fix-words @args
    exit $LASTEXITCODE

[group('debug')]
[doc('Crosscheck subtitles, translations and reports')]
[script]
[extension('.ps1')]
[positional-arguments]
check-subs *paths:
    uv run tools/check-subs.py @args
    exit $LASTEXITCODE

[group('debug')]
[doc('Collect English subtitle statistics')]
[script]
[extension('.ps1')]
[positional-arguments]
analyse-subs *paths:
    uv run tools/analyse-subs.py @args
    exit $LASTEXITCODE

[group('debug')]
[doc('Print system prompt and first chunk of each English subtitle')]
[script]
[extension('.ps1')]
[positional-arguments]
print-prompt *paths:
    uv run src/translate-gemma.py --print-prompt @args
    exit $LASTEXITCODE
alias pp := print-prompt
