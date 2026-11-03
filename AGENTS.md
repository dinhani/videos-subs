# videos-subs

Personal scripts that extract the tracks of the videos in the media library, identify the language of the text subtitles and image subtitles, OCR the image subtitles in the wanted languages, and translate English text subtitles to Brazilian Portuguese with a local LLM.

## Principle

The only subtitle this project makes for viewers is the Brazilian Portuguese translation, `<name>.pob.translated.srt`. Its source is an English text subtitle, either extracted from a text track or made by OCR from an image subtitle. Viewers who want English watch the original track inside the video, which the media server shows as it is; nobody watches an extracted or OCR text subtitle.

So an OCR output is only input for translation. Judge an OCR issue, a check-ocr rule or a fix by whether it changes the translation: wrong or missing text, lines in the wrong order, and markup that can reach the translated `.srt` matter; punctuation style that is faithful to the image, such as `....` for an ellipsis, does not.

Subtitles are written by people, and `♪` follows no fixed format: it may mark every line of a song, only some, or none. A rule judges a line by the `♪` on that line and does not infer it from other lines or entries.

The English source is the source of truth for every fix. fix-translations changes an entry only when the change cannot be wrong: it never changes an entry that is already right, and never writes text that is right only in some contexts. Everything that needs judgment is left to check-translate and the fix-translate skill, which always run after it and read each flagged entry. So a mechanical fix, or an entry of its data lists, that can be wrong in some case is removed, even when it is right most of the time; a missed fix costs nothing, since the skill handles it, and a wrong fix reaches the viewer unflagged.

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
- **description**: text in `[...]` or `(...)` that describes a sound or names who speaks, such as `[GRUNTS]` or `(Avi)`; most of them are in SDH subtitles.
- **trace**: the JSON record in `_traces` of every LLM request and reply for one OCR or translation.
- **issue**: one problem a check step flags in an entry, with a kind (`wrong order`, `untranslated`, ...) and a weight.
- **report**: the text file in `_checks` with the issues of one OCR output or translation, and its score: weighted issues per 100 entries.

## Pipeline

Run the steps in order. Each step skips files it already processed, so every step can be run again safely.

1. `1-extract-subs.py`: extracts the tracks of each video into `_subs/<video>.embed.<stream index>.<ext>`: text tracks become text subtitles (`.srt`), PGS tracks become image subtitles (`.sup`), DVD (VobSub) tracks become image subtitles (`.mks`). Reads the video once with ffmpeg for all tracks.
2. `2-identify-text-subs.py`: detects the language and kind of each text subtitle and renames it to `<name>.<language code>.srt`, or `<name>.<language code>.sdh.srt`. A text subtitle with an image subtitle of the same name next to it was made by ocr and is skipped; identify-ocr names it with its image subtitle.
3. `3-identify-ocr-subs.py`: reads the first entries of each image subtitle with Tesseract and its `script/Latin` model, detects the language and kind of that text and renames the image subtitle to `<name>.<language code>.sup` or `<name>.<language code>.sdh.sup` (or `.mks`), with its OCR output and OCR trace when they exist. Nothing else is written; the Tesseract text is only good enough for language detection, and non-Latin image subtitles get a wrong language code or `und`. An image subtitle without entries keeps its name.
4. `4-ocr-subs.py`: transcribes the image subtitles whose language code is in `LANGUAGES` (`eng` only, since only English is translated) with Gemma vision into `<name>.eng.srt`, next to the image subtitle. Entries with identical images are transcribed once and get the same text. A failed chunk is retried, then split down to single entries; an entry that still fails is written as `[OCR failed]`. An image subtitle without entries writes nothing.
5. `5-check-ocr-subs.py`: checks every text subtitle made by ocr (an `.eng.srt` with an image subtitle of the same name next to it) against its image subtitle, entry by entry, and writes a report to `_checks/<name>.ocr.txt`. Its rules are in [RULES.md](RULES.md), section 1.
6. `6-translate-subs.py`: translates every `.eng.srt` and writes `<name>.pob.translated.srt` in the movie folder, so the media server loads it, or `<name>.pob.sdh.translated.srt` when the source is SDH. `<name>` is the name of the source without its language code and kind.
7. `7-fix-translations.py`: fixes every `.pob.translated.srt` where the fix is mechanical, by comparing each entry with its `_subs/<name>.eng.srt`, and deletes the report of every translation it changes. Its fixes are described below.
8. `8-check-translations.py`: checks every `.pob.translated.srt` against its `_subs/<name>.eng.srt`, entry by entry, and writes a report to `_checks/<name>.pob.translated.txt`. Its rules are in [RULES.md](RULES.md), section 2.
9. `9-warn-subs.py`: adds warning entries at the start of every translation: `WARNING_SDH` when it is SDH, and `WARNING_PROBLEM` (`[Legenda traduzida automaticamente...]`) when its trace records an entry accepted with a problem. Each warning is entry 0, and the file is written without renumbering, so the other entries keep the numbers of the English source. A warning already in the file is not added again.

fix-translations is deterministic and changes only what the English source decides. It runs again on every translation, writes only when an entry changes, and a second run changes nothing. Before writing a translation it shows each changed entry, English, old and new, and asks; `--yes` writes without showing or asking. It backs up every translation before writing it, as the media layout says; `--backups <folder>` puts the backups in another folder, for a run on a scratch copy. A changed entry loses its `ignore:` marker, so check-translate judges it again. An entry with a blank line, a line without words, or a number its English entry does not have, such as `27` or `"11": {` left by the model reply, is not changed at all, so check-translate still sees it whole. Its fixes, in the order they run, are in `FIXES`:

- tags: deletes the tags the English entry does not have, restores the English `{\anN}`, and restores `<i>...</i>` on a line the English has whole in italics when the translated entry has no italics at all.
- leftovers: deletes a JSON ending of the model reply, such as `"]},`, at the end of a line with text, when the English line does not end with `]` or `}`. A quote that ends the English line stays. An entry that would be left with another number of quotes than the English entry keeps its leftovers, so check-translate still flags it.
- brackets: adds the closer or opener of a description that the English line has and the translated line misses, swaps a closer of the wrong kind, as in `(...]`, and removes the brackets around a translated line whose English line has none.
- quotes: removes a quote at the start or end of a line whose English line has none there, when the translated entry has one quote more than the English entry and that line is the only one; `"`, `“` and `”` all count as quotes.
- descriptions: writes each description from `data/fix-sounds.txt` and `data/fix-subjects.txt`, ignoring the model's text. A description matches when its whole text is a phrase of `fix-sounds`, or a subject of `fix-subjects` or a name followed by a phrase that starts with a verb in -ing or -s (`(JAKE GRUNTS)`). A list such as `(GRUNTS, GASPS)` matches when its first part matches and every other part is a phrase. A phrase lists its right forms, `chuckles = ri | risos | risada`: the first is the one written, and a description the model already wrote in one of the forms, with or without an article, is left as it is. A name is kept only when the English shows it is a name and the model kept it in its own translation, which is not the English description unchanged. The names of a movie are the words of its English subtitles that are a speaker label (`SHURI:`), a person or organization found by spaCy NER (`en_core_web_sm`) in the entries not in capitals only, or a word of `data/check-words.txt`. A name that NER misses, or that appears only inside descriptions, such as `ikran`, goes to `check-words` only when it stays unchanged in Brazilian Portuguese; a world term with a Portuguese translation, such as `skimwing`, stays out, so check-translate still flags it when it is left in English. An object the model kept, such as `(METAL STRAINING)` or `(SENSOR BEEPING)`, is not a name, so its description keeps the model's text. A line that is only descriptions becomes the English line with its descriptions translated; elsewhere a description replaces the one at the same place in the translated line, when both lines have as many. The case follows the English description.
- words: replaces the misspellings the model repeats, from `data/fix-words.txt`, keeping their case. Each line is `english = misspelled = right`: a whole misspelled word is replaced only when the English entry has its English word, and only when the entry gives it one right word. A word the English entry also has is left as it is.

The tables are written by hand: each phrase is translated once, with the verb in the third person for -s and in the gerund for -ing, so a subject in front of it keeps the sentence right. A phrase or a subject whose Portuguese depends on the scene is not in the tables: a sound that is different for a person, an animal or a machine (`cries`, `whimpers`, `clicks`, `squeaks`), a word with several meanings (`choking`, `blows`, `bell ringing`, `bellows`, `whooping`), and a subject whose Portuguese has a gender the English does not tell (`both`, `all`, `everyone`). A line, a description or an entry whose lines do not match the English is left to check-translate. Nothing that needs reading the sentence is fixed here: a line that is only `,` or only reply leftovers, the JSON of later entries, a duplicated line, gender and grammar.

check-ocr compares the width of each line on the image with the width of its text. An entry is in the wrong order when its lines fit the image only in reverse order; this happens in SDH tracks, where two speakers' lines sit at opposite sides of the screen and Gemma reads the left one first. Width differences alone are not flagged: a track may use a second, narrower font, such as for translated foreign dialogue.

check-translate is deterministic: word frequency, the pt_BR hunspell dictionary, a LanguageTool server, and comparisons with the English source. A rule is kept only when most of its flags on real translations are errors a viewer would see. A rule may skip a word or a flag only when the skip cannot hide an error; anything else goes to the data lists in `data`. Most grammar errors it finds are broken at a line break: the model translates each line of an entry on its own. An entry **reads as** a language when most of its words are clearly more frequent in that language.

A check step skips a file whose report exists, logs its score, and prints the worst scores at the end. Delete the reports to check again.

A flag judged wrong is marked in the subtitle itself, with `ignore:<kinds>` at the end of the entry's timing line; the format is in [RULES.md](RULES.md). Marked flags are left out of the score. translate removes the marker from the source, so a marker in an OCR output does not reach the translation, and a new OCR or translation has no markers.

Each check script lists its rules in `RULES`, the one place to switch a rule on or off or change its settings. [RULES.md](RULES.md) describes every rule for whoever fixes its flags: what it flags, examples, correction and caution. Update it when a rule is added, removed or changed.

Language detection (`SubtitleLanguage` in `shared/languages.py`) is the same for both identify steps: lingua, and `und` when nothing is detected. Portuguese is split into `por` (Portugal) and `pob` (Brazil) by counting the forms used only in Portugal against the forms used only in Brazil, not by the Portugal forms alone: a Brazilian subtitle of a period film may use `tu` and `vós`.

Kind detection (`SubtitleKind` in `shared/subtitles.py`) is the same for both identify steps: a subtitle is SDH when enough of its entries have a description in `[...]` or `(...)` and its most common descriptions are not most of them. The second condition keeps out commentaries that put the speakers' names in brackets, `(Avi)`, `(Lauren)`, since the same few names repeat. A commentary counts as base, unless it also describes sounds. On the library, the first 200 entries give the same kind as the whole subtitle for 98% of the English subtitles, and Tesseract reads the brackets as Gemma does.

identify-ocr, ocr and check-ocr read the image subtitle with PyAV (`parse_image_subtitle` in `shared/image_subtitles.py`). A whole image subtitle reads in under a second. Entries in the top half of the screen get `{\an8}`.

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
- `_traces` holds one trace per OCR and per translation: every request, reply, retry and accepted problem. The file is rewritten after every record, so it is valid and current during a run. Traces are never deleted.
- `_checks` holds one report per checked OCR output and translation.
- `Z:/midias/_backups/<run>/` holds a copy of every file that fix-translations, warn and `just apply-fixes` overwrite, made before writing it, under its path from `Z:/midias`; their `--backups <folder>` option puts it elsewhere. translate and the other steps write only new files. Each run has its own folder, named by its start time, so no backup is replaced. Backups are never deleted.

## Running

Use the `justfile`; it is the source of truth for how to run things.

ocr and translate need their llama server, `just llama-ocr` and `just llama-translate`. Both servers use port 9931, so run one at a time. The check steps need no llama server; check-translate starts its own LanguageTool server.

Without paths, a step processes both `Z:/midias/filmes` and `Z:/midias/series`. Every step accepts any folder or file as a path:

- a file is processed wherever it is, as long as the step can process its type: `.mkv`/`.mp4` for extract, `.srt` for identify-text, translate and check-ocr, `.sup`/`.mks` for identify-ocr and ocr, `.pob.translated.srt` for fix-translations, check-translate and warn. A file passed by hand skips the step's folder filter, so ocr accepts an image subtitle in any language and translate accepts a text subtitle in any language. check-ocr still needs the image subtitle next to the file;
- a folder is searched recursively: extract finds every video, fix-translations and check-translate find every `.pob.translated.srt`, the other steps find files inside `_subs` folders only (passing a `_subs` folder itself also works), and ocr, translate and check-ocr apply their filter there: ocr only picks `.eng` image subtitles, translate only `.eng.srt`, check-ocr only `.eng.srt`.

The virtual environment lives in `uv/envs/videos-subs` under the local data directory (`%LOCALAPPDATA%` on Windows). Running `uv run` or `uv sync` directly, outside `just`, always creates a `.venv` in the project folder; set `UV_PROJECT_ENVIRONMENT` first or go through `just`.

## Code layout

All code is in `src`: the pipeline scripts, `crosscheck-subs.py`, `shared` and `fix-tools`. The data lists stay in `data` at the project root, and the tests are in `tests`. `bench` holds the benchmark of translation models, with its own `justfile`: `bench.py` lists the models and runs, translates the same English sources with each run, through the translate step or the translation prompt of the model card, and reports the check-translate scores and times side by side; its outputs in `bench/runs`, `bench/sources` and `bench/compare` are not committed.

- `shared/`: code used by more than one script, one module per subject: `files.py` (library folders, file names, reading and writing subtitles), `subtitles.py` (kinds, tags, `ignore:` markers), `image_subtitles.py` (PGS and VobSub decoding), `languages.py` (language detection and word frequency), `descriptions.py` (the description tables of fix-translations), `data_lists.py` (`DataList`, the one reader, checker and writer of the lists in `data`), `reports.py` (issues, reports and scores), `console.py` (logging and `Batch`), `llm.py` (the llama server and traces). A script imports each name from its module: `from shared.files import parse_srt`.
- `fix-tools/`: the tools of the fix-ocr and fix-translate skills, run through `just`. `apply-fixes.py` (`just apply-fixes`) applies fixes decided by hand from a JSON file: each fix names an entry by its number, gives its exact current text, and its new text or the issue kinds to ignore; it checks every fix before writing any subtitle, and backs up each subtitle before writing it. `add-to-dictionary.py` (`just add-check-words`, `just add-check-expressions`, `just add-fix-sounds`, `just add-fix-subjects`, `just add-fix-words`) adds right words and expressions, and the pairs of fix-translations, to the data lists in `data`, in lowercase and in alphabetical order; it refuses a misspelling that the pt_BR dictionary knows, and a misspelling without its English word. `list-missing-descriptions.py` (`just descriptions-missing`) lists the descriptions of the English sources that fix-translations cannot translate, the most common first, for growing its tables.
- `crosscheck-subs.py`: not a pipeline step; it only reads and reports, per movie folder, a text subtitle made by ocr whose language code or kind differs from its image subtitle, a track with more than one text subtitle, a translation without an English source of its kind, a report without its subtitle, and a `.part` file left by a write that did not finish. The track index (`<video>.embed.<index>`) is the key that matches the files of one track.
- Each script is a top-level script, run by its path under `src`, not a module: constants, then its own types, then the execution loop, which is always under `if __name__ == "__main__":` so importing a script never runs it. The check scripts put their rules between the constants and the types they check, and fix-translations its fixes. check-ocr and check-translate run their checks in `CHECK_WORKERS` processes, since they are CPU work in Python; the worker processes import the script.
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
- `just test` runs the unit tests in `tests` with pytest. They build entries in the test itself and read the real data lists, so a data list entry that breaks a rule fails a test; they never read the library. A test case is a rule the project depends on, such as an entry fix-translations must leave unchanged; tests are not written for coverage. Verify a change to a step also on a scratch copy of a few movie folders, never on the library: run the step before and after the change and compare the outputs file by file.

## Git

- Commit every major change on its own, as soon as it is done, with a Conventional Commits message. Push after committing.
- The project folder is on the NAS, so git refuses it as dubious ownership until the folder is added to `safe.directory`.

## Collaboration

- Never tell the user that files written before a change, such as reports, traces or translations, keep the old format or the old results. The user knows.
- Never hand translation content or data lists (`data/*.txt`, translations, reports) to the user to review. Judging them against the purpose this file states is the agent's job: review them, decide, and fix them.
