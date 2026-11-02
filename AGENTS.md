# videos-subs

Personal scripts that extract the tracks of the videos in the media library, identify the language of the text subtitles and image subtitles, OCR the image subtitles in the wanted languages, and translate English text subtitles to Brazilian Portuguese with a local LLM.

## Principle

The only subtitle this project makes for viewers is the Brazilian Portuguese translation, `<name>.pob.translated.srt`. Its source is an English text subtitle, either extracted from a text track or made by OCR from an image subtitle. Viewers who want English watch the original track inside the video, which the media server shows as it is; nobody watches an extracted or OCR text subtitle.

So an OCR output is only input for translation. Judge an OCR issue, a check-ocr rule or a fix by whether it changes the translation: wrong or missing text, lines in the wrong order, and markup that can reach the translated `.srt` matter; punctuation style that is faithful to the image, such as `....` for an ellipsis, does not.

Subtitles are written by people, and `♪` follows no fixed format: it may mark every line of a song, only some, or none. A rule judges a line by the `♪` on that line and does not infer it from other lines or entries.

## Terms

Use these terms, and only these, in code, logs, docs and conversation. Do not rotate synonyms.

- **video**: a `.mkv` or `.mp4` file in the media library.
- **movie folder**: the folder that holds a video (`movie_dir` in code); a series episode has one too.
- **track**: a subtitle stream inside a video, before extraction. Only for what is still inside the video.
- **text subtitle**: a `.srt` file.
- **image subtitle**: a `.sup` (PGS, Blu-ray) or `.mks` (VobSub, DVD) file: subtitles stored as pictures, which need OCR to become a text subtitle.
- **entry**: one subtitle on screen, with a start and an end time; one numbered block in a `.srt`. `SubtitleEntry` is an entry of an image subtitle.
- **language code**: the ISO 639-3 code that identify-text and identify-ocr put in a file name (`eng`, `por`, ...), plus `pob` for Brazilian Portuguese and `und` for undetected. A file whose last name part before the extension, or before `sdh`, is a language code counts as identified.
- **kind**: base or SDH (subtitles for the deaf and hard of hearing, which describe sounds). identify-text and identify-ocr put `sdh` after the language code of an SDH subtitle: `<name>.eng.sdh.srt`. The translation of an SDH subtitle keeps the marker: `<name>.pob.sdh.translated.srt`. In this file, `.eng.srt`, `.eng.sup` and `.pob.translated.srt` also stand for `.eng.sdh.srt`, `.eng.sdh.sup` and `.pob.sdh.translated.srt`.
- **trace**: the JSON record in `_traces` of every LLM request and reply for one OCR or translation.
- **issue**: one problem a check step flags in an entry, with a kind (`wrong order`, `untranslated`, ...) and a weight.
- **report**: the text file in `_checks` with the issues of one OCR output or translation, and its score: weighted issues per 100 entries.

## Pipeline

Run the steps in order. Each step skips files it already processed, so every step can be run again safely.

1. `1-extract-subs.py`: extracts the tracks of each video into `_subs/<video>.embed.<stream index>.<ext>`: text tracks become text subtitles (`.srt`), PGS tracks become image subtitles (`.sup`), DVD (VobSub) tracks become image subtitles (`.mks`). Reads the video once with ffmpeg for all tracks.
2. `2-identify-text-subs.py`: detects the language and kind of each text subtitle and renames it to `<name>.<language code>.srt`, or `<name>.<language code>.sdh.srt`. A text subtitle with an image subtitle of the same name next to it was made by ocr and is skipped; identify-ocr names it with its image subtitle.
3. `3-identify-ocr-subs.py`: reads the first `SAMPLE_ENTRIES` entries of each image subtitle with Tesseract and its `script/Latin` model, detects the language and kind of that text and renames the image subtitle to `<name>.<language code>.sup` or `<name>.<language code>.sdh.sup` (or `.mks`), with its OCR output and OCR trace when they exist. Nothing else is written; the Tesseract text is only good enough for language detection, and non-Latin image subtitles get a wrong language code or `und`. An image subtitle without entries is logged as empty and keeps its name. Runs `SAMPLE_WORKERS` (8) image subtitles in parallel across movies, with one progress bar per movie over its image subtitles; Tesseract runs in `TESSERACT_WORKERS` parallel parts per image subtitle.
4. `4-ocr-subs.py`: transcribes the image subtitles whose language code is in `LANGUAGES` (`eng` only, since only English is translated) with Gemma vision into `<name>.eng.srt`, next to the image subtitle. Entries with identical images are transcribed once and get the same text. Entries are sent in chunks of `CHUNK_SIZE` images per request, with one request in flight per llama server slot (`total_slots` from `/props`); every line in the reply carries an `italic` flag and the script adds the `<i>` tags; when only some words of a line are italic, the model wraps them in `<i>` tags inside the line. The model sometimes writes those tags with attributes, such as `<i style="...">`, or closes them with `</em>`; the script turns them into plain `<i>` and `</i>` after the reply, without changing the prompt. A failed chunk is retried at temperatures 0.0, 0.5 and 1.0, then split in halves down to single entries; an entry that still fails is written as `[OCR failed]`. An image subtitle without entries is logged as empty and nothing is written.
5. `5-check-ocr-subs.py`: checks every text subtitle made by ocr (an `.eng.srt` with an image subtitle of the same name next to it) against its image subtitle, entry by entry, and writes a report to `_checks/<name>.ocr.txt`. Its rules are in [RULES.md](RULES.md), section 1.
6. `6-translate-subs.py`: translates every `.eng.srt` and writes `<name>.pob.translated.srt` in the movie folder, so the media server loads it, or `<name>.pob.sdh.translated.srt` when the source is SDH. `<name>` is the name of the source without its language code and kind.
7. `7-check-translations.py`: checks every `.pob.translated.srt` against its `_subs/<name>.eng.srt`, entry by entry, and writes a report to `_checks/<name>.pob.translated.txt`. Its rules are in [RULES.md](RULES.md), section 2.
8. `8-warn-subs.py`: adds warning entries at the start of every translation: `WARNING_SDH` when it is SDH, and `WARNING_PROBLEM` (`[Legenda traduzida automaticamente...]`) when its trace records an entry accepted with a problem. Each warning is entry 0 from 0:00 to `WARNING_DURATION`, and the file is written without renumbering, so the other entries keep the numbers of the English source. A warning already in the file is not added again.

check-ocr measures the image: the rows with text fill form one band per line on screen (a short band, such as an accent, joins the nearest line), and each band's width in pixels is compared with the width of its text line in Arial, in em. The pixels per em of a file is the median over its long lines. A line fits when it differs by at most `tolerance_em` or `tolerance_share` (settings of `WrongOrderRule`). An entry with as many text lines as bands is in the wrong order when its lines do not fit in order but fit in reverse order; this happens in SDH tracks, where two speakers' lines sit at opposite sides of the screen and Gemma reads the left one first. Width differences alone are not flagged: a track may use a second, narrower font, such as for translated foreign dialogue. Entries without duration are dropped from the image subtitle, as `srt` drops them when the OCR output is written.

check-translate is deterministic: wordfreq (word frequency, with simplemma lemmas), the LibreOffice pt_BR hunspell dictionary read with spylls, a LanguageTool server, and comparisons with the source. A rule is kept only when most of its flags on real translations are errors a viewer would see. A rule may skip a word or a flag only when the skip cannot hide an error; anything else goes to the data lists. The dictionary is only asked about words that are also rare in wordfreq, so slang and profanity pass on frequency and rare correct words pass on the dictionary. `data/dict-words.txt` holds the right words the pt_BR dictionary lacks (interjections, terms of a movie's world, coinages), one per line, lowercase, with accents; a word is compared in lowercase, so a line also covers the word in capitals. The fix-translate skill adds to it. LanguageTool runs only the rules enabled in `GrammarRule`. A grammar flag is not reported when it lies inside an expression of `data/dict-expressions.txt` in the entry (correct Portuguese that LanguageTool flags, such as `os relés` or `uma ciborgue`, in the same format), or when it contains a name not written in capitals. Most grammar errors it finds are broken at a line break: the model translates each line of an entry on its own. An entry **reads as** a language when most of its words are clearly more frequent in that language.

A check step skips a file whose report exists, logs its score, and prints the worst scores at the end. Delete the reports to check again.

A flag judged wrong is marked in the subtitle itself: `ignore:<kinds>` at the end of the entry's timing line (`IGNORE_MARKER` in `shared.py`), where `srt` keeps it as `proprietary`. `IssueKind.is_ignored_in` drops a flag whose kind is in its entry's marker; the report counts the dropped flags on an `ignored` line and leaves them out of the score. translate removes the marker from the source's timing line, so a marker in an OCR output does not reach the translation. A new OCR or translation writes a new file, so its markers are gone. The format is in [RULES.md](RULES.md).

An `IssueKind` (in `checks.py`) defines what is wrong: its label, weight, rule number in RULES.md and description in the report; its `marker` is the label with a hyphen for each space. A rule (one frozen dataclass per rule, in the `Rules` section of each check script) defines how it is found: its issue kind, its `enabled` switch, its settings, and `find_issues`, which scans the whole subtitle and yields the flagged entries with their detail. `RULES` at the end of that section lists every rule of the script with its settings; it is the one place to switch a rule on or off or change its settings. check-translate has one grammar rule per RULES.md rule (2.6 to 2.9), each with its issue kind and the LanguageTool rules it reports; LanguageTool checks each translation once, with the rules of every enabled grammar rule (`LANGUAGETOOL_RULES`), and each grammar rule takes its own matches. A disabled rule flags nothing, and check-translate skips the dictionary or the LanguageTool server when no enabled rule needs them. Reports written before a change keep their issues until they are deleted. [RULES.md](RULES.md) describes every rule for whoever fixes its flags: what it flags, examples, correction and caution. Update it when a rule is added, removed or changed.

Language detection (`SubtitleLanguage.from_text` in `shared.py`) is the same for both identify steps: lingua, and `und` when nothing is detected. Portuguese is split into `por` (Portugal) and `pob` (Brazil) by counting the forms that mark each one: `MARKERS_PT_PT` (`tu`, `teu`, `vós`, the `tu` verb forms, Portugal spellings such as `facto` and `acção`) plus the words of `WORDS_PT_PT` (`autocarro`, `estar a fazer`), against `MARKERS_PT_BR` (`você`, `estar fazendo`, Brazil spellings such as `gênio`). A text is `por` when it has at least `MARKERS_PT_PT_MIN` (3) Portugal markers and more of them than Brazil markers. A Brazilian subtitle of a period film may use `tu` and `vós`, so the count is compared with the Brazil markers, not used alone.

Kind detection (`SubtitleKind.from_texts` in `shared.py`) is the same for both identify steps: a subtitle is SDH when at least `SDH_ENTRIES_DESCRIBED_MIN` (5%) of its entries have a description in `[...]` or `(...)`, and its `SDH_DESCRIPTIONS_TOP` (3) most common descriptions make up less than `SDH_DESCRIPTIONS_TOP_MAX` (60%) of all of them. Otherwise it is base. The second condition keeps out commentaries that put the speakers' names in brackets, `(Avi)`, `(Lauren)`, since the same few names repeat. A commentary counts as base, unless it also describes sounds. On the library, the first 200 entries give the same kind as the whole subtitle for 98% of the English subtitles, and Tesseract reads the brackets as Gemma does.

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
- `_checks` holds one report per checked OCR output and translation. Each report starts with the full path of its subtitle and `score · entries · issues`, then one line per issue kind found with its count and what it means, and an `ignored` line with the flags dropped by markers. Then every flagged entry: `entry <number> · line <line> · ` (the line of the entry number in the `.srt`) and its timing line as it is in the `.srt`, then indented by 2 spaces each issue as `<kind> · rule <number> · ignore:<marker> · <detail>`, and its text as it is in the `.srt`, with tags, marked `>>` with its number, between the `QUOTE_CONTEXT_ENTRIES` entries before and after it (2 in a translation report, 1 in an OCR report). A translation report has `en`/`pt` lines.

## Running

Use the `justfile`; it is the source of truth for how to run things.

ocr and translate need their llama server, `just llama-ocr` and `just llama-translate`. Both servers use port 9931, so run one at a time. The check steps need no llama server; check-translate starts its own LanguageTool server.

Without paths, a step processes both `Z:/midias/filmes` and `Z:/midias/series`. Every step accepts any folder or file as a path:

- a file is processed wherever it is, as long as the step can process its type: `.mkv`/`.mp4` for extract, `.srt` for identify-text, translate and check-ocr, `.sup`/`.mks` for identify-ocr and ocr, `.pob.translated.srt` for check-translate and warn. A file passed by hand skips the step's folder filter, so ocr accepts an image subtitle in any language and translate accepts a text subtitle in any language. check-ocr still needs the image subtitle next to the file;
- a folder is searched recursively: extract finds every video, check-translate finds every `.pob.translated.srt`, the other steps find files inside `_subs` folders only (passing a `_subs` folder itself also works), and ocr, translate and check-ocr apply their filter there: ocr only picks `.eng` image subtitles, translate only `.eng.srt`, check-ocr only `.eng.srt`.

The virtual environment lives in `uv/envs/videos-subs` under the local data directory (`%LOCALAPPDATA%` on Windows). The justfile sets `UV_PROJECT_ENVIRONMENT` from just's `data_local_directory()`. Running `uv run` or `uv sync` directly, outside `just`, always creates a `.venv` in the project folder; set `UV_PROJECT_ENVIRONMENT` first or go through `just`.

The justfile runs recipes in PowerShell (`pwsh -Command`). Recipes that take paths are PowerShell scripts (`[script]`, `[extension('.ps1')]`, `[positional-arguments]`) that pass them with `@args`, so a path with spaces stays one argument, and end with `exit $LASTEXITCODE` so the script exit code reaches just. Environment variables for one recipe go in `[env(...)]` attributes. Do not add `sh` syntax.

## Code layout

- `shared.py`: code used by more than one script: media folder constants, `find_files` (argument parsing and file discovery), `parse_movie_dir`, `parse_srt`, `find_language_code`, `parse_subtitle_name`, `SubtitleLanguage` (language detection for both identify steps), `SubtitleKind` (base or SDH, for both identify steps), `parse_image_subtitle` with `SubtitleEntry`, `PgsScreen` and `VobSubScreen` (reads an image subtitle into entries for identify-ocr, ocr and check-ocr), `write_text_replacing` (write through a `.part` file; the rename is retried for `REPLACE_ATTEMPTS` while the NAS denies it, which happens now and then when the target file was just rewritten), `connect_llama`, `log_action` with the `Color` enum, `progress_bar`, `Trace` (the `_traces` JSON file), and `Batch` (per-movie logging, failure collection, exit code).
- `checks.py`: code used by the check scripts: `IssueKind` with its weights, `TextLanguage` (word frequency and the reads-as vote), `parse_words` with `Word`, `Report` and `ReportScore` (the `_checks` file and its header), and `log_ranking`.
- `crosscheck-subs.py`: not a pipeline step; it only reads and reports, per movie folder, a text subtitle made by ocr whose language code or kind differs from its image subtitle, a track with more than one text subtitle, a translation without an English source of its kind, a report without its subtitle, and a `.part` file left by a write that did not finish. The track index (`<video>.embed.<index>`) is the key that matches the files of one track.
- Each script is a top-level script, not a module: constants, then its own types, then the execution loop. The check scripts put their rules between the constants and the types they check. check-ocr and check-translate run their checks in `CHECK_WORKERS` processes, since they are CPU work in Python; the worker processes import the script, so its execution loop is under `if __name__ == "__main__":`.
- Every script follows the same per-file pattern: log the movie, skip when already done, do the work inside `try`, report failures through `Batch`, write output through a `.part` file and rename it.

## Translation

- Model: Gemma 4 26B A4B QAT Q4_0 on llama.cpp, with thinking disabled (`llama-translate` recipe). OCR uses Gemma 4 12B with its vision projector (`llama-ocr` recipe), which fits the GPU with room to spare. `llama-ocr` runs 8 slots (`--parallel 8`) so the GPU batches the concurrent OCR requests; `--ctx-size` is split across the slots, 4096 tokens each; a chunk of 10 entries measured at most about 1200 prompt tokens (about 70 per image) and 600 reply tokens.
- Subtitles are translated in chunks of `CHUNK_SIZE` entries, with `CHUNK_CONTEXT_BEFORE` earlier entries (with their accepted translations) and `CHUNK_CONTEXT_AFTER` upcoming entries as read-only context.
- The reply is constrained by a JSON schema that fixes the ids and the number of lines per entry. The `en` field in the reply is not used by the code; it makes the model restate each line before translating it.
- A failed chunk is retried at temperatures 0.0, 0.5 and 1.0, then split in halves up to `LLM_SPLITS` times. After that, the last reply is accepted and recorded in the trace as accepted with a problem; warn adds the warning entry. The translation is written without renumbering, so its entries keep the numbers of the source.
- Translated lines keep the source line breaks. Lines are not wrapped; the player wraps lines that do not fit the screen.

## Constraints

- The media library is on a Synology NAS with HDDs. Any parallel NAS I/O is capped at 8 workers.
- Tesseract and its models come from scoop (`tesseract`, `tesseract-languages`); `tesseract-languages` sets `TESSDATA_PREFIX`.
- LanguageTool is run by `language_tool_python`, which downloads it to its cache on the first check-translate run (about 260 MB) and runs it on the scoop `openjdk26`. check-translate starts one server in the main process, only when a file needs checking, and the worker processes connect to it.
- No test suite. Verify changes manually, using a scratch copy of a movie folder rather than the library.

## Git

- Commit every major change on its own, as soon as it is done, with a Conventional Commits message. Push after committing.
- The project folder is on the NAS, so git refuses it as dubious ownership until the folder is added to `safe.directory`.

## Collaboration

- Never tell the user that files written before a change, such as reports, traces or translations, keep the old format or the old results. The user knows.
