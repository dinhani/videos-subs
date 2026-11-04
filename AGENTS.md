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

Run the steps in order; each step skips files it already processed, so every step can be run again safely. Read [docs/PIPELINE.md](docs/PIPELINE.md) before changing a step, and [docs/RULES.md](docs/RULES.md) before changing a check rule.

1. `1-extract-subs.py`: extracts the tracks of each video into `_subs`, as text subtitles and image subtitles.
2. `2-identify-text-subs.py`: puts the language code and kind in the name of each text subtitle.
3. `3-identify-ocr-subs.py`: puts the language code and kind in the name of each image subtitle, from a Tesseract reading.
4. `4-ocr-subs.py`: transcribes the English image subtitles into text subtitles with Gemma vision.
5. `5-check-ocr-subs.py`: checks each OCR output against its image subtitle and writes a report.
6. `6-translate-subs.py`: translates each `.eng.srt` into `<name>.pob.translated.srt` in the movie folder.
7. `7-fix-translations.py`: applies the mechanical fixes to each translation.
8. `8-check-translations.py`: checks each translation against its English source and writes a report.
9. `9-warn-subs.py`: adds warning entries at the start of each translation.

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

All code is in `src`: the pipeline scripts, `crosscheck-subs.py`, `shared` and `fix-tools`. The data lists are in `data`, the tests in `tests`.

- `shared/`: code used by more than one script, one module per subject: `files.py` (library folders, `SubtitleFile`: the name of each subtitle and of its related files, trace and report, reading and writing subtitles), `subtitles.py` (language codes, kinds, tags, `ignore:` markers), `image_subtitles.py` (PGS and VobSub decoding), `languages.py` (language detection and word frequency), `descriptions.py` (the description tables of fix-translations and the names of a movie), `data_lists.py` (`DataList`, the one reader, checker and writer of the lists in `data`), `reports.py` (issues, reports and scores), `console.py` (logging and `Batch`), `llm.py` (the llama server and traces).
- `fix-tools/`: the tools of the fix-ocr and fix-translate skills, run through `just`.
- `crosscheck-subs.py`: not a pipeline step; it only reads, and reports the files of one track that do not match each other. The track index (`<video>.embed.<index>`) is the key.
- Each script has, in order: constants, its own types, then the execution loop. The check scripts put their rules between the constants and the types, and fix-translations its fixes. check-ocr and check-translate run their checks in `CHECK_WORKERS` processes.
- Every script follows the same per-file pattern: log the movie, skip when already done, do the work inside `try`, report failures through `Batch`, write output through a `.part` file and rename it.

`bench` benchmarks translation models, with its own `justfile`, loaded as the `bench` module. `bench.py` is the command line; `catalog.py` holds the sources, models and runs, `models.py` the model types and the llama server, `runs.py` the translators, sources, runs and scoring, `report.py` the report and compare tables. The bench justfile puts `src` on `PYTHONPATH` so these modules import `shared`. `nmt.py` (CTranslate2) and `score.py` (COMET and chrF++) are standalone uv scripts with their own environments, since unbabel-comet needs numpy 1. Each `prompts/<name>.txt` adds a run `gemma-4-26b+<name>`, which translates with that file as the system prompt instead of `LLM_PROMPT`. The bench copies the `.nfo` of each source next to its English copy and into the movie folder of each run, so translate finds it as in the library. `just bench run --movie <text>` translates only the sources whose title contains the text, for quick numbers. The bench scores raw model output, without fix-translations. Only `2-references/*/<name>.claude.srt` is committed. Models are stored by type: GGUF in `D:/AI-Models-LMStudio`, CTranslate2 in `D:/AI-Models-CTranslate2`, PyTorch in `D:/AI-Models-PyTorch`; only `just bench models` downloads them.

## Constraints

- The media library is on a Synology NAS with HDDs. Any parallel NAS I/O is capped at 8 workers.
- `just test` runs the unit tests in `tests` with pytest. They build entries in the test itself and read the real data lists, so a data list entry that breaks a rule fails a test; they never read the library. A test case is a rule the project depends on, such as an entry fix-translations must leave unchanged; tests are not written for coverage. Verify a change to a step also on a scratch copy of a few movie folders, never on the library: run the step before and after the change and compare the outputs file by file.

## Git

- Commit every major change on its own, as soon as it is done, with a Conventional Commits message. Push after committing.

## Collaboration

- Never tell the user that files written before a change, such as reports, traces or translations, keep the old format or the old results. The user knows.
- Never hand translation content or data lists (`data/*.txt`, translations, reports) to the user to review. Judging them against the purpose this file states is the agent's job: review them, decide, and fix them.
- The agent runs the bench (`just bench run`, `just bench score`) when the user asks for it, and never on its own initiative.
