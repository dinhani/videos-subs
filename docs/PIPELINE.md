# Pipeline

Run the steps in order. Each step skips files it already processed, so every step can be run again safely. The file names and folders are in the media layout of [AGENTS.md](../AGENTS.md).

## Shared by several steps

### Language detection

Both identify steps detect the language with lingua (`shared/languages.py`), and write `und` when nothing is detected. Portuguese is split into `por` (Portugal) and `pob` (Brazil) by counting the forms used only in Portugal against the forms used only in Brazil, not by the Portugal forms alone: a Brazilian subtitle of a period film may use `tu` and `vós`.

### Kind detection

Both identify steps detect the kind the same way (`shared/subtitles.py`): a subtitle is SDH when enough of its entries have a description and its most common descriptions are not most of them. The second condition keeps out commentaries that put the speakers' names in brackets, `(Avi)`, `(Lauren)`, since the same few names repeat. A commentary counts as base, unless it also describes sounds.

### Image subtitles

identify-ocr, ocr and check-ocr read image subtitles with PyAV (`shared/image_subtitles.py`). Entries in the top half of the screen get `{\an8}`.

### Check steps

A check step skips a file whose report exists; delete the reports to check again.

A flag judged wrong is marked in the subtitle itself, with `ignore:<kinds>` at the end of the entry's timing line; the format is in [RULES.md](RULES.md). Marked flags are left out of the score. translate removes the markers from the source, so a marker in an OCR output does not reach the translation.

Each check script lists its rules in `RULES`, the one place to switch a rule on or off or change its settings. [RULES.md](RULES.md) describes every rule for whoever fixes its flags. Update it when a rule is added, removed or changed.

## 1. extract

Extracts the tracks of each video into `_subs/<video>.embed.<stream index>.<ext>`: text tracks become `.srt`, PGS tracks `.sup`, VobSub tracks `.mks`.

## 2. identify-text

Detects the language and kind of each text subtitle and puts them in its name. A text subtitle with an image subtitle of the same name next to it was made by ocr and is skipped; identify-ocr names it with its image subtitle.

## 3. identify-ocr

Reads the first entries of each image subtitle with Tesseract, detects the language and kind of that text, and renames the image subtitle, with its OCR output and OCR trace when they exist. The Tesseract text is only good enough for language detection and is not kept; non-Latin image subtitles get a wrong language code or `und`.

## 4. ocr

Transcribes the English image subtitles with Gemma 4 12B vision into `<name>.eng.srt`, next to the image subtitle. Only English is transcribed, since only English is translated. Entries with identical images are transcribed once. A failed chunk is retried, then split down to single entries; an entry that still fails is written as `[OCR failed]`.

## 5. check-ocr

Checks every text subtitle made by ocr against its image subtitle and writes a report. Its rules are in [RULES.md](RULES.md), section 1.

check-ocr compares the width of each line on the image with the width of its text. An entry is in the wrong order when its lines fit the image only in reverse order; this happens in SDH tracks, where two speakers' lines sit at opposite sides of the screen and Gemma reads the left one first. Width differences alone are not flagged: a track may use a second, narrower font, such as for translated foreign dialogue.

## 6. translate

Translates every `.eng.srt` with Gemma 4 26B A4B, thinking disabled, into `<name>.pob.translated.srt` in the movie folder, where the media server loads it.

- Entries are translated in chunks, with earlier entries (and their accepted translations) and upcoming entries as read-only context.
- The reply is constrained by a JSON schema that fixes the ids and the number of lines per entry. The `en` field in the reply is not used by the code; it makes the model restate each line before translating it.
- A failed chunk is retried at other temperatures, then split in halves. After that, the last reply is accepted and recorded in the trace as accepted with a problem; warn adds a warning entry for it.
- The system prompt gets the title of the movie or episode, and what the tinyMediaManager `.nfo` says that can change a translation: the plot, and the credited characters named in the subtitle with their actors. Without an `.nfo`, the prompt has the title only.
- Translated lines keep the source line breaks and are not wrapped; the player wraps lines that do not fit the screen.
- The translation is written without renumbering, so its entries keep the numbers of the source.

## 7. fix-translations

Fixes every `.pob.translated.srt` where the fix is mechanical, by comparing each entry with its English source, and deletes the report of every translation it changes.

fix-translations is deterministic and changes only what the English source decides; a second run changes nothing. Before writing, it shows each changed entry and asks, and backs up the translation. A changed entry loses its `ignore:` marker, so check-translate judges it again. An entry that looks broken, such as one with a blank line or a number its English entry does not have, is not changed at all, so check-translate still sees it whole.

Its fixes, in the order they run, are in `FIXES`:

- tags: deletes the tags the English entry does not have and restores the English `{\anN}`, and restores whole-line italics when the translation has no italics at all.
- leftovers: deletes a JSON ending of the model reply, such as `"]},`, at the end of a line whose English line does not end that way.
- brackets: completes or swaps the brackets of a description to match the English line, and removes brackets the English line does not have.
- quotes: removes a quote the English line does not have, when it is the only extra one.
- descriptions: writes each description from the description lists, keeping a name only when the English shows it is a name and the model kept it. A form the model already wrote that the table lists as right is left as it is.
- interjections: replaces an interjection the model kept in English, from `data/fix-interjections.txt` (`wow = uau`), on a line whose English has it and has no `♪` or `♫`.
- words: replaces the misspellings the model repeats, from `data/fix-words.txt`, only when the English entry has the English word.

The names of a movie come from its speaker labels, spaCy NER and `data/check-words.txt`. A name NER misses goes to `check-words` only when it stays unchanged in Brazilian Portuguese; a world term with a Portuguese translation, such as `skimwing`, stays out, so check-translate still flags it when it is left in English.

The description lists are the phrases of `data/fix-sounds.txt` (people), `data/fix-music.txt` and `data/fix-objects.txt` (objects, machines and nature), a phrase in one of them only; the subjects of `data/fix-subjects.txt`; the moods of `data/fix-music-moods.txt` (`grim = sombria`) and `data/fix-voice-moods.txt` (`deep = grave`); the languages of `data/fix-languages.txt` (`swedish = sueco`) and the channels a voice comes through of `data/fix-channels.txt` (`on tv = na TV`). The table builds descriptions from them:

- a mood before music or a voice: `grim music playing` is `música sombria tocando`, `in a deep voice` is `com uma voz grave`;
- a language after `speaking`, `speaks` or `in`: `speaking swedish` is `falando sueco`;
- a phrase after `continues`, in the gerund: `continues laughing` is `continua rindo`;
- a phrase after `distant`: `distant explosion` is `explosão ao longe`;
- a phrase followed by a phrase led by `in`, `on` or `over`: `shouting in german`, `laughing on tv`, `bell ringing in the distance`;
- a subject with a number: `man 2` is `homem 2`.

The tables are written by hand: each phrase is translated once, with the verb in the third person for -s and in the gerund for -ing, so a subject in front of it keeps the sentence right. A phrase or a subject whose Portuguese depends on the scene is not in the tables: a sound that is different for a person, an animal or a machine (`cries`, `clicks`), a word with several meanings (`choking`, `blows`), and a subject whose gender the English does not tell (`both`, `everyone`). Nothing that needs reading the sentence is fixed here: duplicated lines, gender and grammar are left to check-translate.

## 8. check-translate

Checks every translation against its English source and writes a report. Its rules are in [RULES.md](RULES.md), section 2.

check-translate is deterministic: word frequency, the pt_BR hunspell dictionary, a LanguageTool server, and comparisons with the English source. A rule is kept only when most of its flags on real translations are errors a viewer would see. A rule may skip a word or a flag only when the skip cannot hide an error; anything else goes to the data lists in `data`. Most grammar errors it finds are broken at a line break, since the model translates each line of an entry on its own. An entry **reads as** a language when most of its words are clearly more frequent in that language.

The main process starts one LanguageTool server, only when a file needs checking, and the worker processes connect to it.

## 9. warn

Adds warning entries at the start of every translation: one when it is SDH, and one when its trace records an entry accepted with a problem. Each warning is entry 0, so the other entries keep the numbers of the English source.
