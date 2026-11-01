# videos-subs

Personal scripts that extract the tracks of the videos in the media library, identify the language of the text subtitles and image subtitles, OCR the image subtitles in the wanted languages, and translate English text subtitles to Brazilian Portuguese with a local LLM.

## Principle

The only subtitle this project makes for viewers is the Brazilian Portuguese translation, `<name>.pob.translated.srt`. Its source is an English text subtitle, either extracted from a text track or made by OCR from an image subtitle. Viewers who want English watch the original track inside the video, which the media server shows as it is; nobody watches an extracted or OCR text subtitle.

So an OCR output is only input for translation. Judge an OCR issue, a check-ocr rule or a fix by whether it changes the translation: wrong or missing text, lines in the wrong order, and markup that can reach the translated `.srt` matter; punctuation style that is faithful to the image, such as `....` for an ellipsis, does not.

## Terms

Use these terms, and only these, in code, logs, docs and conversation. Do not rotate synonyms.

- **video**: a `.mkv` or `.mp4` file in the media library.
- **movie folder**: the folder that holds a video (`movie_dir` in code); a series episode has one too.
- **track**: a subtitle stream inside a video, before extraction. Only for what is still inside the video.
- **text subtitle**: a `.srt` file.
- **image subtitle**: a `.sup` (PGS, Blu-ray) or `.mks` (VobSub, DVD) file: subtitles stored as pictures, which need OCR to become a text subtitle.
- **entry**: one subtitle on screen, with a start and an end time; one numbered block in a `.srt`. `SubtitleEntry` is an entry of an image subtitle.
- **language code**: the ISO 639-3 code that identify-text and identify-ocr put in a file name (`eng`, `por`, ...), plus `pob` for Brazilian Portuguese and `und` for undetected. A file whose last name part before the extension is a language code counts as identified.
- **trace**: the JSON record in `_traces` of every LLM request and reply for one OCR or translation.
- **issue**: one problem a check step flags in an entry, with a kind (`wrong order`, `untranslated`, ...) and a weight.
- **report**: the text file in `_checks` with the issues of one OCR output or translation, and its score: weighted issues per 100 entries.

## Pipeline

Run the steps in order. Each step skips files it already processed, so every step can be run again safely.

1. `1-extract-subs.py`: extracts the tracks of each video into `_subs/<video>.embed.<stream index>.<ext>`: text tracks become text subtitles (`.srt`), PGS tracks become image subtitles (`.sup`), DVD (VobSub) tracks become image subtitles (`.mks`). Reads the video once with ffmpeg for all tracks.
2. `2-identify-text-subs.py`: detects the language of each text subtitle and renames it to `<name>.<language code>.srt`.
3. `3-identify-ocr-subs.py`: reads the first `SAMPLE_ENTRIES` entries of each image subtitle with Tesseract and its `script/Latin` model, detects the language of that text and renames the image subtitle to `<name>.<language code>.sup` (or `.mks`). Nothing else is written; the Tesseract text is only good enough for language detection, and non-Latin image subtitles get a wrong language code or `und`. An image subtitle without entries is logged as empty and keeps its name. Runs `SAMPLE_WORKERS` (8) image subtitles in parallel across movies, with one progress bar per movie over its image subtitles; Tesseract runs in `TESSERACT_WORKERS` parallel parts per image subtitle.
4. `4-ocr-subs.py`: transcribes the image subtitles whose language code is in `LANGUAGES` (`eng` only, since only English is translated) with Gemma vision into `<name>.eng.srt`, next to the image subtitle. Entries with identical images are transcribed once and get the same text. Entries are sent in chunks of `CHUNK_SIZE` images per request, with one request in flight per llama server slot (`total_slots` from `/props`); every line in the reply carries an `italic` flag and the script adds the `<i>` tags; when only some words of a line are italic, the model wraps them in `<i>` tags inside the line. The model sometimes writes those tags with attributes, such as `<i style="...">`, or closes them with `</em>`; the script turns them into plain `<i>` and `</i>` after the reply, without changing the prompt. A failed chunk is retried at temperatures 0.0, 0.5 and 1.0, then split in halves down to single entries; an entry that still fails is written as `[OCR failed]`. An image subtitle without entries is logged as empty and nothing is written.
5. `5-translate-subs.py`: translates every `.eng.srt` and writes `<name>.pob.translated.srt` in the movie folder, so the media server loads it.
6. `6-check-ocr-subs.py`: checks every text subtitle made by ocr (an `.eng.srt` with an image subtitle of the same name next to it) against its image subtitle, entry by entry, and writes a report to `_checks/<name>.ocr.txt`. Issues: `[OCR failed]` entries; wrong order, an entry whose lines fit the image only in the reverse order; inconsistent, an entry whose image is identical to another entry's but whose text differs, ignoring spaces and curly quotes; artifacts such as markdown emphasis or `{`, `<`, `` ` ``; glyph confusion such as `|` for `I`, `0` inside a word, `l'll`; and `()`/`[]` without their pair within the `entries_max` entries of `UnbalancedRule` (a caption may span entries; a digit with `)` after a space, as in `1) ...`, is a list number, not a bracket).
7. `7-check-translations.py`: checks every `.pob.translated.srt` against its `_subs/<name>.eng.srt`, entry by entry, and writes a report to `_checks/<name>.pob.translated.txt`. Issues: entries the translation accepted with a problem (from the trace); untranslated, a line that reads as English, or is its English line unchanged and has at least `unchanged_votes_min` clearly English words; lines without words or starting with a comma; duplicated line, an entry with one line that holds the translation of the whole entry and another line that repeats part of it; Portugal Portuguese words; symbols, tags or letters of another script that the English entry does not have (pieces of the model reply such as JSON or `pt_alt_` names); unknown words, rare in wordfreq and missing from the Brazilian Portuguese hunspell dictionary (`gruñido`, `percece`), reported apart when written in capital letters (`GRUNNIDO`, `ASSÓBIO`), since those are mostly sound descriptions the model misspelled; and grammar errors from the LanguageTool rules enabled in the `languagetool_rules` of `GrammarRule` (`vão / mate-o`, `de / a esposa`, `O que?`, `um trincheira`).

check-ocr measures the image: the rows with text fill form one band per line on screen (a short band, such as an accent, joins the nearest line), and each band's width in pixels is compared with the width of its text line in Arial, in em. The pixels per em of a file is the median over its long lines. A line fits when it differs by at most `tolerance_em` or `tolerance_share` (settings of `WrongOrderRule`). An entry with as many text lines as bands is in the wrong order when its lines do not fit in order but fit in reverse order; this happens in SDH tracks, where two speakers' lines sit at opposite sides of the screen and Gemma reads the left one first. Width differences alone are not flagged: a track may use a second, narrower font, such as for translated foreign dialogue. Entries without duration are dropped from the image subtitle, as `srt` drops them when the OCR output is written.

check-translate is deterministic: wordfreq (word frequency, with simplemma lemmas), the LibreOffice pt_BR hunspell dictionary read with spylls, a LanguageTool server, and comparisons with the source. A rule is kept only when most of its flags on real translations are errors a viewer would see. The dictionary is only asked about words that are also rare in wordfreq, so slang and profanity pass on frequency and rare correct words pass on the dictionary. LanguageTool runs only the rules enabled in `GrammarRule`; its spelling, style, punctuation, `onde`/`aonde`, crase and diacritics rules flagged mostly correct Portuguese in subtitles. Most grammar errors it finds are broken at a line break: the model translates each line of an entry on its own. An entry **reads as** a language when most of its words are clearly more frequent in that language.

A check step skips a file whose report exists, logs its score, and prints the worst scores at the end. Delete the reports to check again.

An `IssueKind` (in `checks.py`) defines what is wrong: its label, weight and description in the report. A rule (one frozen dataclass per rule, in the `Rules` section of each check script) defines how it is found: its issue kind, its `enabled` switch, its settings, and `find_issues`, which scans the whole subtitle and yields the flagged entries with their detail. `RULES` at the end of that section lists every rule of the script with its settings; it is the one place to switch a rule on or off or change its settings. The grammar rule of check-translate also switches each LanguageTool rule in `languagetool_rules`. A disabled rule flags nothing, and check-translate skips the dictionary or the LanguageTool server when no enabled rule needs them. Reports written before a change keep their issues until they are deleted.

Language detection (`SubtitleLanguage.from_text` in `shared.py`) is the same for both identify steps: lingua, with Portuguese split into `por` (Portugal, from a word list) and `pob` (Brazil), and `und` when nothing is detected.

identify-ocr, ocr and check-ocr read the image subtitle with PyAV (`parse_image_subtitle` in `shared.py`): libavcodec decodes each subtitle set into bitmaps with their position and display times, and the palette comes from the packets (`PgsScreen` reads the PGS palette segments, `VobSubScreen` reads the VobSub header palette and the color and alpha commands of each packet). The bitmaps of a set are painted on one canvas, cropped to their visible pixels and padded on a gray background. An entry lasts until the next subtitle set or its own end time. A whole image subtitle reads in under a second. Entries in the top half of the screen get `{\an8}`.

## Media layout

```
Z:/midias/filmes/<movie>/                 (series use the same layout under Z:/midias/series/)
  <video>.mkv
  <video>.embed.2.pob.translated.srt      translation output
  _subs/<video>.embed.2.eng.srt           text subtitle, extracted and identified
  _subs/<video>.embed.3.eng.sup           image subtitle, extracted and identified
  _subs/<video>.embed.3.eng.srt           text subtitle made from that image subtitle by ocr
  _traces/<video>.embed.2.pob.translated.json   translation trace
  _traces/<video>.embed.3.eng.ocr.json          OCR trace
  _checks/<video>.embed.2.pob.translated.txt    translation report
  _checks/<video>.embed.3.eng.ocr.txt           OCR report
```

- `_subs` holds the extracted text subtitles and image subtitles, and the text subtitles made from image subtitles. They stay out of the movie folder because the media server already reads the tracks inside the video and would show them twice.
- `_traces` holds one trace per OCR and per translation: every request, reply, retry and accepted problem, as one indented JSON array. The file is rewritten after every record, so it is valid and current during a run. Traces are never deleted.
- `_checks` holds one report per checked OCR output and translation. Each report starts with the subtitle name and `score · entries · issues`, then one line per issue kind found with its count and what it means, then every flagged entry with its start time and number, each issue in plain words, and its text as it is in the `.srt`, with tags (`en`/`pt` lines for translations).

## Running

Use the `justfile`; it is the source of truth for how to run things.

- `just setup`: installs the dependencies (`uv sync`) and downloads the pt_BR hunspell dictionary to `hunspell` under the local data directory (`just dictionary`).
- `just extract`, `just identify-text`, `just identify-ocr`, `just ocr`, `just translate`, each with optional `[paths...]`: run the steps in this order.
- `just check-ocr`, `just check-translate`, each with optional `[paths...]`: check the outputs of `ocr` and `translate`. They need no llama server; check-translate starts its own LanguageTool server.
- `just delete-checks [paths...]`: deletes every `_checks` folder under the paths, or under the media folders without paths, after a confirmation, so the check steps run again on those files.
- `just llama-ocr` / `just llama-translate`: start the llama.cpp server that `ocr` / `translate` need at `http://127.0.0.1:9931/v1`. Both use port 9931, so run one at a time.

Without paths, a step processes both `Z:/midias/filmes` and `Z:/midias/series`. Every step accepts any folder or file as a path:

- a file is processed wherever it is, as long as the step can process its type: `.mkv`/`.mp4` for extract, `.srt` for identify-text, translate and check-ocr, `.sup`/`.mks` for identify-ocr and ocr, `.pob.translated.srt` for check-translate. A file passed by hand skips the step's folder filter, so ocr accepts an image subtitle in any language and translate accepts a text subtitle in any language. check-ocr still needs the image subtitle next to the file;
- a folder is searched recursively: extract finds every video, check-translate finds every `.pob.translated.srt`, the other steps find files inside `_subs` folders only (passing a `_subs` folder itself also works), and ocr, translate and check-ocr apply their filter there: ocr only picks `.eng` image subtitles, translate only `.eng.srt`, check-ocr only `.eng.srt`.

The virtual environment lives in `uv/envs/videos-subs` under the local data directory (`%LOCALAPPDATA%` on Windows). The justfile sets `UV_PROJECT_ENVIRONMENT` from just's `data_local_directory()`. Running `uv run` or `uv sync` directly, outside `just`, always creates a `.venv` in the project folder; set `UV_PROJECT_ENVIRONMENT` first or go through `just`.

The justfile runs recipes in PowerShell (`pwsh -Command`). Recipes that take paths are PowerShell scripts (`[script]`, `[extension('.ps1')]`, `[positional-arguments]`) that pass them with `@args`, so a path with spaces stays one argument, and end with `exit $LASTEXITCODE` so the script exit code reaches just. Environment variables for one recipe go in `[env(...)]` attributes. Do not add `sh` syntax.

## Code layout

- `shared.py`: code used by more than one script: media folder constants, `find_files` (argument parsing and file discovery), `parse_movie_dir`, `parse_srt`, `find_language_code`, `SubtitleLanguage` (language detection for both identify steps), `parse_image_subtitle` with `SubtitleEntry`, `PgsScreen` and `VobSubScreen` (reads an image subtitle into entries for identify-ocr, ocr and check-ocr), `write_text_replacing` (write through a `.part` file; the rename is retried for `REPLACE_ATTEMPTS` while the NAS denies it, which happens now and then when the target file was just rewritten), `connect_llama`, `log_action` with the `Color` enum, `progress_bar`, `Trace` (the `_traces` JSON file), and `Batch` (per-movie logging, failure collection, exit code).
- `checks.py`: code used by the check scripts: `IssueKind` with its weights, `TextLanguage` (word frequency and the reads-as vote), `parse_words` with `Word`, `Report` and `ReportScore` (the `_checks` file and its header), and `log_ranking`.
- Each script is a top-level script, not a module: constants, then its own types, then the execution loop. The check scripts put their rules between the constants and the types they check. check-ocr and check-translate run their checks in `CHECK_WORKERS` processes, since they are CPU work in Python; the worker processes import the script, so its execution loop is under `if __name__ == "__main__":`.
- Every script follows the same per-file pattern: log the movie, skip when already done, do the work inside `try`, report failures through `Batch`, write output through a `.part` file and rename it.

## Translation

- Model: Gemma 4 26B A4B QAT Q4_0 on llama.cpp, with thinking disabled (`llama-translate` recipe). OCR uses Gemma 4 12B with its vision projector (`llama-ocr` recipe), which fits the GPU with room to spare. `llama-ocr` runs 4 slots (`--parallel 4`) so the GPU batches the concurrent OCR requests; `--ctx-size` is split across the slots, 4096 tokens each; a chunk of 10 entries measured at most about 1200 prompt tokens (about 70 per image) and 600 reply tokens.
- Subtitles are translated in chunks of `CHUNK_SIZE` entries, with `CHUNK_CONTEXT_BEFORE` earlier entries (with their accepted translations) and `CHUNK_CONTEXT_AFTER` upcoming entries as read-only context.
- The reply is constrained by a JSON schema that fixes the ids and the number of lines per entry. The `en` field in the reply is not used by the code; it makes the model restate each line before translating it.
- A failed chunk is retried at temperatures 0.0, 0.5 and 1.0, then split in halves up to `LLM_SPLITS` times. After that, the last reply is accepted and a warning entry is added at the start of the subtitle.
- Translated lines keep the source line breaks. Lines are not wrapped; the player wraps lines that do not fit the screen.

## Constraints

- The media library is on a Synology NAS with HDDs. Any parallel NAS I/O is capped at 8 workers.
- Tesseract and its models come from scoop (`tesseract`, `tesseract-languages`); `tesseract-languages` sets `TESSDATA_PREFIX`.
- LanguageTool is run by `language_tool_python`, which downloads it to its cache on the first check-translate run (about 260 MB) and runs it on the scoop `openjdk26`. check-translate starts one server in the main process, only when a file needs checking, and the worker processes connect to it.
- No test suite. Verify changes manually, using a scratch copy of a movie folder rather than the library.

## Git

- Commit every major change on its own, as soon as it is done, with a Conventional Commits message. Push after committing.
- The project folder is on the NAS, so git refuses it as dubious ownership until the folder is added to `safe.directory`.
