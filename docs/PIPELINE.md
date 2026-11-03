# Pipeline

Run the steps in order. Each step skips files it already processed, so every step can be run again safely. The file names and folders are in the media layout of [AGENTS.md](../AGENTS.md).

## Shared by several steps

### Language detection

Language detection (`SubtitleLanguage` in `shared/languages.py`) is the same for both identify steps: lingua, and `und` when nothing is detected. Portuguese is split into `por` (Portugal) and `pob` (Brazil) by counting the forms used only in Portugal against the forms used only in Brazil, not by the Portugal forms alone: a Brazilian subtitle of a period film may use `tu` and `vós`.

### Kind detection

Kind detection (`SubtitleKind` in `shared/subtitles.py`) is the same for both identify steps: a subtitle is SDH when enough of its entries have a description in `[...]` or `(...)` and its most common descriptions are not most of them. The second condition keeps out commentaries that put the speakers' names in brackets, `(Avi)`, `(Lauren)`, since the same few names repeat. A commentary counts as base, unless it also describes sounds. On the library, the first 200 entries give the same kind as the whole subtitle for 98% of the English subtitles, and Tesseract reads the brackets as Gemma does.

### Image subtitles

identify-ocr, ocr and check-ocr read the image subtitle with PyAV (`parse_image_subtitle` in `shared/image_subtitles.py`). A whole image subtitle reads in under a second. Entries in the top half of the screen get `{\an8}`.

### Check steps

A check step skips a file whose report exists, logs its score, and prints the worst scores at the end. Delete the reports to check again.

A flag judged wrong is marked in the subtitle itself, with `ignore:<kinds>` at the end of the entry's timing line; the format is in [RULES.md](RULES.md). Marked flags are left out of the score. translate removes the marker from the source, so a marker in an OCR output does not reach the translation, and a new OCR or translation has no markers.

Each check script lists its rules in `RULES`, the one place to switch a rule on or off or change its settings. [RULES.md](RULES.md) describes every rule for whoever fixes its flags: what it flags, examples, correction and caution. Update it when a rule is added, removed or changed.

## 1. extract

`1-extract-subs.py` extracts the tracks of each video into `_subs/<video>.embed.<stream index>.<ext>`: text tracks become text subtitles (`.srt`), PGS tracks become image subtitles (`.sup`), DVD (VobSub) tracks become image subtitles (`.mks`). It reads the video once with ffmpeg for all tracks.

## 2. identify-text

`2-identify-text-subs.py` detects the language and kind of each text subtitle and renames it to `<name>.<language code>.srt`, or `<name>.<language code>.sdh.srt`. A text subtitle with an image subtitle of the same name next to it was made by ocr and is skipped; identify-ocr names it with its image subtitle.

## 3. identify-ocr

`3-identify-ocr-subs.py` reads the first entries of each image subtitle with Tesseract and its `script/Latin` model, detects the language and kind of that text and renames the image subtitle to `<name>.<language code>.sup` or `<name>.<language code>.sdh.sup` (or `.mks`), with its OCR output and OCR trace when they exist. Nothing else is written; the Tesseract text is only good enough for language detection, and non-Latin image subtitles get a wrong language code or `und`. An image subtitle without entries keeps its name.

Tesseract and its models come from scoop (`tesseract`, `tesseract-languages`); `tesseract-languages` sets `TESSDATA_PREFIX`.

## 4. ocr

`4-ocr-subs.py` transcribes the image subtitles whose language code is in `LANGUAGES` (`eng` only, since only English is translated) with Gemma vision into `<name>.eng.srt`, next to the image subtitle. Entries with identical images are transcribed once and get the same text. A failed chunk is retried, then split down to single entries; an entry that still fails is written as `[OCR failed]`. An image subtitle without entries writes nothing.

OCR uses Gemma 4 12B with its vision projector (`llama-ocr` recipe), which fits the GPU with room to spare. `llama-ocr` runs 8 slots (`--parallel 8`) so the GPU batches the concurrent OCR requests; `--ctx-size` is split across the slots, 4096 tokens each; a chunk of 10 entries measured at most about 1200 prompt tokens (about 70 per image) and 600 reply tokens.

## 5. check-ocr

`5-check-ocr-subs.py` checks every text subtitle made by ocr (an `.eng.srt` with an image subtitle of the same name next to it) against its image subtitle, entry by entry, and writes a report to `_checks/<name>.ocr.txt`. Its rules are in [RULES.md](RULES.md), section 1.

check-ocr compares the width of each line on the image with the width of its text. An entry is in the wrong order when its lines fit the image only in reverse order; this happens in SDH tracks, where two speakers' lines sit at opposite sides of the screen and Gemma reads the left one first. Width differences alone are not flagged: a track may use a second, narrower font, such as for translated foreign dialogue.

## 6. translate

`6-translate-subs.py` translates every `.eng.srt` and writes `<name>.pob.translated.srt` in the movie folder, so the media server loads it, or `<name>.pob.sdh.translated.srt` when the source is SDH. `<name>` is the name of the source without its language code and kind.

- Model: Gemma 4 26B A4B QAT Q4_0 on llama.cpp, with thinking disabled (`llama-translate` recipe).
- Subtitles are translated in chunks of `CHUNK_SIZE` entries, with `CHUNK_CONTEXT_BEFORE` earlier entries (with their accepted translations) and `CHUNK_CONTEXT_AFTER` upcoming entries as read-only context.
- The reply is constrained by a JSON schema that fixes the ids and the number of lines per entry. The `en` field in the reply is not used by the code; it makes the model restate each line before translating it.
- A failed chunk is retried at temperatures 0.0, 0.5 and 1.0 (`--temperatures` sets others, one attempt each), then split in halves up to `LLM_SPLITS` times. After that, the last reply is accepted and recorded in the trace as accepted with a problem; warn adds the warning entry. The translation is written without renumbering, so its entries keep the numbers of the source.
- The system prompt is `LLM_PROMPT`; `--prompt <file>` reads another one from a text file, for the bench; `{movie}` in it is replaced by the title and year of the movie, or by the series folder and the episode title.
- Translated lines keep the source line breaks. Lines are not wrapped; the player wraps lines that do not fit the screen.

## 7. fix-translations

`7-fix-translations.py` fixes every `.pob.translated.srt` where the fix is mechanical, by comparing each entry with its `_subs/<name>.eng.srt`, and deletes the report of every translation it changes.

fix-translations is deterministic and changes only what the English source decides. It runs again on every translation, writes only when an entry changes, and a second run changes nothing. Before writing a translation it shows each changed entry, English, old and new, and asks; `--yes` writes without showing or asking. It backs up every translation before writing it, as the media layout says; `--backups <folder>` puts the backups in another folder, for a run on a scratch copy. A changed entry loses its `ignore:` marker, so check-translate judges it again. An entry with a blank line, a line without words, or a number its English entry does not have, such as `27` or `"11": {` left by the model reply, is not changed at all, so check-translate still sees it whole. Its fixes, in the order they run, are in `FIXES`:

- tags: deletes the tags the English entry does not have, restores the English `{\anN}`, and restores `<i>...</i>` on a line the English has whole in italics when the translated entry has no italics at all.
- leftovers: deletes a JSON ending of the model reply, such as `"]},`, at the end of a line with text, when the English line does not end with `]` or `}`. A quote that ends the English line stays. An entry that would be left with another number of quotes than the English entry keeps its leftovers, so check-translate still flags it.
- brackets: adds the closer or opener of a description that the English line has and the translated line misses, swaps a closer of the wrong kind, as in `(...]`, and removes the brackets around a translated line whose English line has none.
- quotes: removes a quote at the start or end of a line whose English line has none there, when the translated entry has one quote more than the English entry and that line is the only one; `"`, `“` and `”` all count as quotes.
- descriptions: writes each description from `data/fix-sounds.txt` and `data/fix-subjects.txt`, ignoring the model's text. A description matches when its whole text is a phrase of `fix-sounds`, or a subject of `fix-subjects` or a name followed by a phrase that starts with a verb in -ing or -s (`(JAKE GRUNTS)`). A list such as `(GRUNTS, GASPS)` matches when its first part matches and every other part is a phrase. A phrase lists its right forms, `chuckles = ri | risos | risada`: the first is the one written, and a description the model already wrote in one of the forms, with or without an article, is left as it is. A name is kept only when the English shows it is a name and the model kept it in its own translation, which is not the English description unchanged. The names of a movie are the words of its English subtitles that are a speaker label (`SHURI:`), a person or organization found by spaCy NER (`en_core_web_sm`) in the entries not in capitals only, or a word of `data/check-words.txt`. A name that NER misses, or that appears only inside descriptions, such as `ikran`, goes to `check-words` only when it stays unchanged in Brazilian Portuguese; a world term with a Portuguese translation, such as `skimwing`, stays out, so check-translate still flags it when it is left in English. An object the model kept, such as `(METAL STRAINING)` or `(SENSOR BEEPING)`, is not a name, so its description keeps the model's text. A line that is only descriptions becomes the English line with its descriptions translated; elsewhere a description replaces the one at the same place in the translated line, when both lines have as many. The case follows the English description.
- words: replaces the misspellings the model repeats, from `data/fix-words.txt`, keeping their case. Each line is `english = misspelled = right`: a whole misspelled word is replaced only when the English entry has its English word, and only when the entry gives it one right word. A word the English entry also has is left as it is.

The tables are written by hand: each phrase is translated once, with the verb in the third person for -s and in the gerund for -ing, so a subject in front of it keeps the sentence right. A phrase or a subject whose Portuguese depends on the scene is not in the tables: a sound that is different for a person, an animal or a machine (`cries`, `whimpers`, `clicks`, `squeaks`), a word with several meanings (`choking`, `blows`, `bell ringing`, `bellows`, `whooping`), and a subject whose Portuguese has a gender the English does not tell (`both`, `all`, `everyone`). A line, a description or an entry whose lines do not match the English is left to check-translate. Nothing that needs reading the sentence is fixed here: a line that is only `,` or only reply leftovers, the JSON of later entries, a duplicated line, gender and grammar.

## 8. check-translate

`8-check-translations.py` checks every `.pob.translated.srt` against its `_subs/<name>.eng.srt`, entry by entry, and writes a report to `_checks/<name>.pob.translated.txt`. Its rules are in [RULES.md](RULES.md), section 2.

check-translate is deterministic: word frequency, the pt_BR hunspell dictionary, a LanguageTool server, and comparisons with the English source. A rule is kept only when most of its flags on real translations are errors a viewer would see. A rule may skip a word or a flag only when the skip cannot hide an error; anything else goes to the data lists in `data`. Most grammar errors it finds are broken at a line break: the model translates each line of an entry on its own. An entry **reads as** a language when most of its words are clearly more frequent in that language.

LanguageTool is run by `language_tool_python`, which downloads it to its cache on the first check-translate run (about 260 MB) and runs it on the scoop `openjdk26`. check-translate starts one server in the main process, only when a file needs checking, and the worker processes connect to it.

## 9. warn

`9-warn-subs.py` adds warning entries at the start of every translation: `WARNING_SDH` when it is SDH, and `WARNING_PROBLEM` (`[Legenda traduzida automaticamente...]`) when its trace records an entry accepted with a problem. Each warning is entry 0, and the file is written without renumbering, so the other entries keep the numbers of the English source. A warning already in the file is not added again.
