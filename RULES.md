# Rules

The rules each step of the pipeline follows: what it takes, what it makes, what it skips, and what counts as an issue in the checks.

## Principle

The only subtitle this project makes for viewers is the Brazilian Portuguese translation, `<name>.pob.translated.srt`. Its source is an English text subtitle, either extracted from a text track or made by OCR from an image subtitle. Viewers who want English watch the original track inside the video.

- An OCR output is only input for translation. An OCR issue matters when it changes the translation: wrong or missing text, lines in the wrong order, markup that can reach the translation. Punctuation that is faithful to the image, such as `....` for an ellipsis, does not matter.
- A translation issue matters when a viewer would notice it: wrong or missing meaning, English left untranslated, broken lines, markup or model debris on screen, grammar errors. Style that does not reach the viewer does not matter.

## Rules for every step

- The steps run in order: extract, identify-text, identify-ocr, ocr, check-ocr, translate, check-translate.
- Each step skips the files it already processed, so every step can run again safely.
- A step without paths processes `Z:/midias/filmes` and `Z:/midias/series`.
- A file passed by hand is processed wherever it is, if the step handles its type, and skips the step's language filter.
- A folder is searched recursively. Extract looks for videos and check-translate for `.pob.translated.srt` anywhere; the other steps look only inside `_subs` folders.
- Output is written to a temporary file first and renamed when complete, so a failed run never leaves a half-written output.
- A failure on one file is reported and the step goes on with the next file.
- Parallel work on the NAS uses at most 8 workers.

## 1. Extract

Takes every `.mkv` and `.mp4` video and writes each subtitle track to `_subs/<video>.embed.<stream index>.<ext>`.

- Text tracks (SubRip, ASS, SSA, MP4 text, WebVTT) become text subtitles (`.srt`).
- Blu-ray (PGS) tracks become image subtitles (`.sup`), copied as they are.
- DVD (VobSub) tracks become image subtitles (`.mks`), copied as they are.
- Other track types are skipped.
- A track is already extracted when a file for its stream index exists, with or without a language code in the name.
- All tracks of a video are extracted in one read of the video.
- An empty output is discarded.
- The subtitles stay in `_subs`, out of the movie folder, so the media server does not show them twice next to the tracks inside the video.

## 2. Identify text subtitles

Detects the language of each text subtitle in `_subs` and renames it to `<name>.<language code>.srt`.

- A file whose last name part is already a language code is skipped.
- The language is detected on the whole text, without tags.

## 3. Identify image subtitles

Detects the language of each image subtitle in `_subs` and renames it to `<name>.<language code>.sup` (or `.mks`).

- A file whose last name part is already a language code is skipped.
- The first 200 entries are read with Tesseract and its Latin script model, and the language is detected on that text.
- Nothing else is written. The Tesseract text is only good enough for language detection.
- Image subtitles in a non-Latin script get a wrong language code or `und`.
- An image subtitle without entries is reported as empty and keeps its name.

## Language detection

The same for both identify steps.

- Lingua detects the language. Undetected text gets `und`.
- Portuguese is Portugal Portuguese (`por`) when the text has at least 3 words or forms used only in Portugal (`autocarro`, `telemóvel`, `ecrã`, `casa de banho`, `estou a fazer`, ...), and Brazilian Portuguese (`pob`) otherwise.

## Reading an image subtitle

Identify-ocr, ocr and check-ocr read image subtitles the same way.

- Each subtitle set on screen becomes one entry: its pictures are painted together, cropped to the visible pixels and padded on a gray background.
- Pictures from small screens (narrower than 1280 pixels, such as DVD) are doubled in size.
- An entry lasts until the next subtitle set or its own end time, whichever comes first. The last entry without an end time lasts 5 seconds.
- A set that repeats the picture on screen at the same position does not start a new entry.
- An entry in the top half of the screen gets `{\an8}`, so the player shows it at the top.

## 4. OCR

Transcribes the English image subtitles in `_subs` with Gemma vision into `<name>.eng.srt`, next to the image subtitle.

- Only image subtitles identified as English are picked from folders, since only English is translated. A file passed by hand is transcribed in any language.
- An image subtitle with a text subtitle of the same name is skipped.
- Entries with identical pictures are transcribed once and get the same text.
- Entries are sent 10 pictures per request.
- The model copies the text exactly as written: no translation, correction, completion or summary. Capitalization, punctuation, digits and symbols stay as on the picture. A dialogue dash stays at the start of its line.
- The model reports for every line whether its letters are slanted, and the line is wrapped in `<i>` and `</i>`. When only some words are slanted, the model wraps those words in `<i>` tags inside the line.
- Italic tags written with attributes, such as `<i style="...">`, become `<i>`, and `</em>` becomes `</i>`.
- A reply is rejected when it is cut off, is not the expected JSON, has other ids than the request, or has an entry without text.
- A rejected request is retried at temperatures 0.0, 0.5 and 1.0, then split in halves down to single entries. An entry that still fails is written as `[OCR failed]`.
- Every request and reply goes to the trace, `_traces/<name>.ocr.json`.
- An image subtitle without entries is reported as empty and nothing is written.

## 5. Check OCR

Checks every text subtitle made by OCR (an `.eng.srt` with an image subtitle of the same name next to it) against its image subtitle, entry by entry, and writes a report to `_checks/<name>.ocr.txt`.

- Entries without duration are dropped from the image subtitle, as they are dropped when the OCR output is written.
- The text subtitle and the image subtitle must have the same number of entries and the same start times. If not, the check fails for that file.
- The precision of the check-ocr issues has not been measured.

### Issues

| Issue | Weight | Flags |
|---|---|---|
| ocr failed | 10 | an entry written as `[OCR failed]` |
| wrong order | 6 | lines that fit the picture only in the reverse order |
| inconsistent | 4 | an entry whose picture is identical to another entry's but whose text differs |
| artifact | 3 | markup the model added |
| glyph confusion | 2 | a character read as a similar one |
| unbalanced | 2 | a bracket without its pair |

**ocr failed**: every attempt to transcribe the entry failed.

**wrong order**: happens in SDH tracks, where two speakers' lines sit at opposite sides of the screen and Gemma reads the left one first.

- The rows of the picture with text form one band per line on screen. A short band, such as an accent, joins the nearest line.
- Each band's width in pixels is compared with the width of its text line in Arial.
- The scale of a file, pixels per em, is the median over its lines of at least 10 characters.
- A line fits when its width differs by at most 2.5 em or 30%.
- Only entries with at least two lines and as many bands as text lines are checked.
- An entry is flagged when its lines do not fit in order but fit in reverse order. Width differences alone are not flagged: a track may use a second, narrower font, such as for translated foreign dialogue.

**inconsistent**: the same picture must have the same text everywhere in the file. Spaces and curly quotes are ignored. The most common text among the entries with that picture is taken as right, and the others are flagged.

**artifact**: markdown emphasis (`*word*`, `_word_`), the characters `` ` ``, `{`, `}`, `\`, `<`, `>`, `�`, the text `Image N:`, and any tag other than `<i>`, `</i>` and `{\an8}`.

**glyph confusion**: `|`; `0`, `1` or `5` between letters; `l` before a capital letter at the start of a word; `l'll`, `l'm`, `l've`, `l'd`, `l're`; a capital `I` inside a lowercase word.

**unbalanced**: `(` or `[` without its pair within 3 entries, or `)` or `]` without an opening one. A caption may span entries, so a pair across up to three entries is accepted.

- Emoticons such as `:)` and `;-(` are not brackets.
- A digit with `)` after a space, as in `1) ...`, is a list number, not a bracket.

## 6. Translate

Translates every English text subtitle in `_subs` to Brazilian Portuguese with Gemma and writes `<name>.pob.translated.srt` in the movie folder, so the media server loads it.

- Only `.eng.srt` files are picked from folders, both the extracted ones and the ones made by OCR. A file passed by hand is translated in any language.
- A text subtitle with a translation in the movie folder is skipped.
- Entries are sent 50 per request, with the 20 earlier entries and their accepted translations, and the 10 next English entries, as read-only context.
- Empty entries are not sent and stay as they are.
- The reply has, for each entry, the English lines as the model read them and the translated lines. The English lines are not used; they make the model restate each line before translating it.
- Each entry keeps its number of lines. A line is not wrapped; the player wraps lines that do not fit the screen.

### Translation rules given to the model

- Natural spoken Brazilian Portuguese, with `você`, not `tu`.
- The meaning stays exact. Nothing is softened, censored or left out: profanity, insults, slurs, sexual content, violence and crude humor keep their strength (`fuck` is `porra`, `caralho` or `foder`, never `droga` or `nossa`). Sarcasm, threats and rudeness stay.
- No warnings, notes or explanations. No entry is refused.
- Translate, do not localize: units, currencies, dates and cultural references stay as in the original (`30 miles` is `30 milhas`). The unit word is translated; the digits stay.
- One translation per entry. Entries are never merged, split, moved or skipped. A sentence that continues into the next entry is split at the same point.
- Unchanged and in the same place: names of people, places, brands and titles; digits; the symbols `♪ ♫ … - – — " ¿ ¡ # * [ ] ( )`; formatting tags such as `<i>`, `<b>`, `<u>`, `<font>`; position tags such as `{\an8}`; any other tag; a leading `- ` dialogue dash.
- Two-speaker entries stay two lines, one speaker per line.
- Sound descriptions such as `[door creaks]` or `(sighs)` are translated and keep their brackets.
- The context is used only for gender, pronouns, formality and recurring terms, and is never translated or written out.

### Failures

- A reply is rejected when it is cut off, is not the expected JSON, has other ids than the request, or changes the number of lines of an entry.
- A rejected request is retried at temperatures 0.0, 0.5 and 1.0, then split in halves, at most twice.
- After that, the last reply is accepted: entries with the right number of lines keep their translation, the others keep the English text. The entries are recorded in the trace as accepted with a problem.
- A translation with accepted problems starts with a warning entry: `[Legenda traduzida automaticamente. Alguns trechos podem estar errados ou em inglês.]`.
- Every request and reply goes to the trace, `_traces/<name>.pob.translated.json`.

## 7. Check translate

Checks every `.pob.translated.srt` against its `_subs/<name>.eng.srt`, entry by entry, and writes a report to `_checks/<name>.pob.translated.txt`.

- Each translated entry is paired with the English entry of the same number. The warning entry has no English entry and is not checked.
- The check is deterministic: word frequency (wordfreq), the LibreOffice pt_BR dictionary, LanguageTool and comparisons with the English entry.

### Words

- A word is a run of letters, with apostrophes inside it.
- A word with a capital first letter that does not start a sentence is a name. Names are ignored by the word issues.
- A line **reads as** a language when most of its words are clearly more frequent in that language. A word votes only when it is common in one of the two languages (zipf 3.0 or more) and its frequencies differ by at least 1.0. English wins with at least the minimum votes and more than twice the Portuguese votes.

### Issues

Precision was measured on 69 older translations (113k entries, 1,376 issues). They were made by an older translate run, so some failures, such as reply debris, may already be fixed. Recheck on new translations.

| Issue | Weight | Issues | Real |
|---|---|---|---|
| accepted with problem | 4 | | not measured |
| untranslated | 6 | 79 | ~80% |
| added symbols | 6 | 148 | ~95% |
| duplicated line | 6 | 38 | 37/37 |
| broken line | 4 | 91 | 25/25 sampled |
| grammar | 3 | ~130 | per LanguageTool rule, below |
| unknown word | 3 | ~340 | ~35% |
| unknown word in caps | 3 | ~500 | ~95% |
| portugal portuguese | 3 | | not measured |

**accepted with problem**: the trace records the entry as accepted with a problem.

**untranslated**: a line that reads as English with at least 3 votes, or that is its English line unchanged with at least 2 votes. Lines with `♪` or `♫` pass the first test, since songs are often left in English. Misses are titles, names and finance jargon.

**added symbols**: symbols, tags or letters of another script that the translated entry has and the English entry does not: `{`, `}`, `[`, `]`, `_`, `<`, `>`, `` ` ``, `\`, formatting tags, and letters outside the Latin alphabets. Real flags are pieces of the model reply, such as `']},`, `pt_alt_...` names and JSON.

**duplicated line**: an entry with as many lines as the English one, where one line holds the translation of the whole entry and another line repeats part of it. A line is a repeat when it has at least 8 letters, is contained in the other line while their English lines are not, and the other line is at least 1.6 times as long as its English line.

**broken line**: a line without letters or digits (other than `♪`, `♫`), or starting with `,`, `;` or `:`, when its English line is not like that.

**grammar**: LanguageTool (`pt-BR`) on the whole translation, with only the rules below. Most errors it finds are broken at a line break: the model translates each line of an entry on its own.

| LanguageTool rule | Flags | Issues | Real |
|---|---|---|---|
| `AUXILIARY_VERB_INFINITIVE` | verb broken at a line break (`vão / mate-o`) | ~12 | ~10/12 |
| `CONTRACOES_OBRIGATORIAS` | missing contraction (`de / a esposa`) | ~10 | 10/10 |
| `QUE_QUÊ` | `que` that must be `quê` (`O que?`) | 16 | 16/16 |
| `GENERAL_GENDER_AGREEMENT_ERRORS`, `ERRO_DE_CONCORDNCIA_DO_GÉNERO_MASCULINO_O` | wrong gender (`um trincheira`) | ~100 | ~0% with `porra`, ~60% otherwise |
| `LINKING_VERB_PREDICATE_AGREEMENT` | linking-verb agreement | ~11 | ~45% |

The gender rules flag `seu porra` and `um porra`, which are correct slang, and feminine forms for women (`uma ciborgue`), names, `os relés`, `o maître`.

**unknown word**: a word of at least 3 letters that is rare (zipf below 2.0) and missing from the pt_BR dictionary, written in lowercase or as it is. Names and words that also appear in the English entry pass. The dictionary is only asked about rare words, so slang and profanity pass on frequency and rare correct words pass on the dictionary. Real flags are typos and words of another language (`gruñido`, `percece`). Noise comes from interjections (`uhuu`, 40), fiction terms and coinages (`wakandanos`, 19, `droide`, `criogênese`, `hipersono`, `heptápode`), names at the start of a sentence and diminutives. About 15 words cause about 40% of the noise.

**unknown word in caps**: the same, for words in capital letters, reported apart. Nearly all are misspelled sound descriptions: `GRUNNIDO` (81), `OFICIALGANDO` (36), `GRUNINDO`, `GRUÑIDO`, `RISINDO`, `ASSÓBIO`, `TRUPULANTES`. The fix belongs in translate.

**portugal portuguese**: words used in Portugal and not in Brazil (`autocarro`, `telemóvel`, `ecrã`, `casa de banho`, ...) and `estar a` with an infinitive (`estou a fazer`).

### Rules tried and dropped

No or almost no real flags on model translations:

- line or entry length against the English entry
- digits missing or added
- `?` or `!` lost
- the same line in consecutive entries
- a line repeated from the neighbour entry
- LanguageTool spelling, style, punctuation, `onde`/`aonde`, crase and diacritics rules: they flagged mostly correct Portuguese in subtitles

## Reports and scores

- A report starts with the subtitle name and `score · entries · issues`, then one line per issue found with its count and meaning, then every flagged entry with its start time, number, issues and text (`en` and `pt` lines for translations).
- The score is the sum of the weights of the issues per 100 entries, counting at least 100 entries.
- A score of 5 or more needs review; 15 or more is bad.
- A check skips a file whose report exists and prints the worst 20 scores at the end. Delete the reports to check again.
- Reports written before a rule changes keep their issues until they are deleted.

## Fixing

- Flags are fixed by hand, one entry at a time, with the `fix-ocr` and `fix-translate` skills. Each fix needs a decision about that entry; no scripts, no bulk replace.
- After a file is fixed, its report is deleted so the next check runs on it again.
- An OCR fix on an `.eng.srt` that already has a translation does not reach that translation; it was made from the old text.

## How a check rule is kept

1. It starts from real errors in real outputs, not imagined ones. A rule with no confirmed real flag is removed.
2. A sample of its flags (25 to 40) is read and the real ones counted.
3. It is kept when most of its flags are errors a viewer would see; otherwise it is removed, restricted or disabled.
4. Rules that compare with the English entry are not judged on human translations: human translators paraphrase and trip them far more often than the model.
5. When a pattern changes, the text it must not flag is tested too.
