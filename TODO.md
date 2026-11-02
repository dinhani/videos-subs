# TODO

Work for the next agent: tests, refactoring, and the open items left by the work on `7-fix-translations.py`. Read `AGENTS.md` first, its Principle section above all: fix-translations changes an entry only when the change cannot be wrong, and everything that needs judgment is left to check-translate and the fix-translate skill.

Rules for this work:

- A refactoring keeps the behavior. Run fix-translations on a scratch copy before and after each change and compare the outputs file by file; they must be the same.
- Never run a step against `Z:/midias`. Copy translations, their `_subs` sources and their `_checks` reports to a scratch folder and run there. fix-translations writes a backup of each file it changes under `BACKUPS_FOLDER`; point it somewhere else when testing (see "Backups folder" below).
- Commit each change on its own, with a Conventional Commits message, and push.

## Tests

The project has no tests. Add them with pytest, run through a `just test` recipe in the `test` group. Tests use synthetic entries only, never the library.

- **Each fix of `FIXES`**, one test per case: a `TranslationEntry` built from English and translated lines, the lines the fix returns. Cover what each fix changes and what it must leave alone.
- **The cases found by the two independent reviews**, as regression tests. Each one must stay unchanged or come out as stated:
  - `THANOS: / <i>You should be grateful.</i>` with `THANOS: <i>Você deveria ser grata.</i>']}, / 27`: unchanged (a line without words, and a number the English does not have).
  - `<i>BEN: / It's the condenser tray.</i>` with a second line of JSON (`39": {"en": [...`): unchanged.
  - `(BOTH LAUGHING)` with `(AMBAS RINDO)`: unchanged.
  - `(DOG WHIMPERING)` with `(cachorro ganindo)`: unchanged.
  - `[ Clicks, Hums ]` with `[ Cliques, Zumbidos ]`: unchanged.
  - `(PAYAKAN BELLOWS)` with `(BRAMIDO DO PAYAKAN)`: unchanged.
  - `They'd say, "keep on steppin'.”` with `Eles diziam: "continue andando."`: unchanged (curly quotes count).
  - `"and preserve knowledge.` with `e preservar o conhecimento."]},`: unchanged (the quotes would not match the English).
  - `What? / (LAUGHTER)` with `O quê? (RISOS)` and a blank line: unchanged.
  - `(METAL STRAINING)` with `(METAL FORÇANDO)`: unchanged (`METAL` is not a name of the movie).
  - `(CHUCKLES)` with `(RISOS)`: unchanged (an accepted form).
  - `(GRUNTS)` with `(GRUNNIDO)`: `(GRUNHE)`.
  - `(JAKE GRUNTS)` with `(JAKE GRUNNINDO)`, `JAKE` in the movie names: `(JAKE GRUNHE)`.
  - `[ CRYING ]`-style spaces inside brackets are kept.
  - `MAYBE A CONCUSSION.` with `TALVE UMA CONCUSSÃO.`: `TALVEZ UMA CONCUSSÃO.`; `I DON'T KNOW.` with `TALVE ...`: unchanged.
  - `Fim."]` for `The end."`: `Fim."`.
  - `(SCOTT GASPS SOFTLY)` with `(SCOTT ARFA COM O SUSTO]`: the closer is swapped.
- **`DescriptionTable.find_translation`**: exact phrases, a subject from `fix-subjects.txt`, a name, a list (`GRUNTS, GASPS`), possessives refused, a plural subject before an -s verb refused, the case of the English kept (caps, first letter, lowercase, names).
- **`DescriptionTranslation.is_confirmed_by` and `is_already_in`**: a name not in the movie names, a name the model did not keep, the English left unchanged, an accepted form with an article.
- **`load_movie_names`**: speaker labels, NER names from mixed-case entries, entries in capitals only left out, words of `check-words.txt`.
- **The words fix**: a triple applied only when its English word is in the entry, two triples giving different right words leave the word, a word the English entry also has is left, the case kept.
- **Idempotence**: a second run over the output of a first run changes nothing.
- **`fix-tools/add-to-dictionary.py`**: pairs and triples validated, a misspelling known to hunspell refused, a key already listed with another value refused, the sort order.
- **check-translate's broken line rule**: a line of digits only is flagged when its English line has letters, and not when the English line is digits only too.

## Refactoring

- **`find_files`** in `shared.py` builds its own parser or takes the caller's (`parser=`). Pick one way; every step calls it.
- **`TagsFix.fix`**: the tag-removal lambda, with its nested conditional, is hard to read.
- **`BracketsFix.fix`** holds five cases in one method: unwrap a bracketed line, a wrong closer, a missing closer, a missing opener, an added quote.
- **Two parsers for the data lists**: `parse_pairs` in `shared.py` for `key = value` and `load_words` in the fix step for `english = misspelled = right`. Make one reader for the `data` lists.
- **`TranslationEntry.names`**: the movie's names are carried by every entry only so `DescriptionsFix` can read them. Find a better place for movie-level data.
- **The execution loop of `7-fix-translations.py`** is long: it finds the source, fixes the entries, shows the preview, asks, backs up and writes. Keep its phases legible.
- **Backups folder**: tests and scratch runs had to copy the script with `BACKUPS_FOLDER` replaced by `sed`. Make the folder settable for a run without copying the script.
- **The quote set** `QUOTES` and the bracket pairs are defined in the fix step; check-translate's unbalanced rule has its own pairs. Share them where both steps mean the same thing.
- **Line endings**: files written by Python on Windows came out with CRLF in the working copy (`AGENTS.md`, `shared.py`, `7-fix-translations.py`, `justfile` and others); git turns them into LF on commit. Add a `.gitattributes` and normalize the working copy.

## Open items

- **`just descriptions-missing`** lists speaker names (`lynch`, `marschall`) among the missing descriptions. Leave out descriptions that are a name of the movie, as `load_movie_names` finds them.
- **Descriptions split across two lines** (`[WHISTLING` / `"SPIDER-MAN THEME"]`) and descriptions with a tag inside are never fixed.
- **check-translate's broken line rule** now flags a few right lines of digits only, such as `20?` for `Twenty?` and `100.000.`. Most of its flags are debris; mark the right ones with `ignore:broken-line`, or find a test that tells them apart without hiding debris.
- **Only fix-translations backs up before writing.** `just apply-fixes`, `9-warn-subs.py` and `6-translate-subs.py` overwrite files without a backup.
- **NER names in check-translate**: the gender and unknown word rules skip names by their capital letter, which does not work in entries in capitals only. The movie names of `load_movie_names` could cover them; add it only when the reports show false flags of that kind.
- **Names NER misses** (`GROOT`, `BATMAN`, `NAMORA`, `K`) and world terms (`skimwing`, `direhorse`) keep the model's text in descriptions. World terms can go to `data/check-words.txt`.
