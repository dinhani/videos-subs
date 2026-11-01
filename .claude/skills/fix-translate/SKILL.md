---
name: fix-translate
description: Fix by hand, one entry at a time and never with a script, the entries that check-translate flagged in the Brazilian Portuguese translations. Every fix needs a decision about that entry first. Use after `just translate` and `just check-translate`, when asked to fix translation errors or check-translate issues.
---

# fix-translate

## Hard rule: judge every entry, never fix in bulk

This skill exists only because the fixes need judgment. If the fixes could be mechanical, there would be a script instead of this skill. Applying the rules below mechanically defeats its purpose and breaks entries the check flagged by mistake.

Forbidden, with no exception and no matter how many entries there are:

- writing or running any script (Python, PowerShell, shell) that changes a `.srt`;
- `sed`, regex replace, find-and-replace across entries or files, `replace_all`;
- a loop over entries or files, or several entries fixed in one edit.

For every flagged entry: read its issue and its `en` and `pt` lines, decide what the Brazilian Portuguese subtitle should say, then make one edit to that entry only, or none when it is already right.

The rules per issue kind below are the usual fixes, not substitutions. When a rule gives a text that does not make sense, write the text that does. The check is a heuristic and can flag a correct entry: leave it and list it at the end.

## Procedure

Fix each entry flagged in the check-translate reports directly in its translation, from the report alone. Never run `just translate` again and never change the English `.eng.srt`.

Reports with issues, unless the user names files:

```
grep -L "· 0 issues" Z:/midias/filmes/*/_checks/*.pob.translated.txt Z:/midias/series/*/_checks/*.pob.translated.txt Z:/midias/series/*/*/_checks/*.pob.translated.txt
```

The first line of a report names its translation, the `.pob.translated.srt` in the movie folder, next to `_checks`. Each flagged entry quotes its exact `en` and `pt` lines from the `.srt`, with tags. Do not read the rest of the file. When the `pt` text of an entry is not unique in the file, find the entry by its number with Grep and include its number and time lines in the edit.

Every fix keeps the entry shape: the same number of lines as the `en` lines, each `pt` line the translation of the `en` line at the same position, and the tags of the `en` lines (`<i>`, `</i>`, `{\an8}`, `<font ...>`) in the same places. Write natural spoken Brazilian Portuguese with "você", and keep profanity as strong as in the English.

- **untranslated**: translate the English line. Skip when the line is a name, a title of a work, a brand, or words the movie itself says in a language other than English (Latin, Na'vi, Spanish); it stays as it is.
- **broken line**: replace the line that is only `,` or a symbol with the translation of its `en` line.
- **duplicated line**: one line holds the translation of the whole entry. Split it at the `en` line break, so each line has the translation of its own `en` line, and remove the repeated part.
- **added symbols**: delete the pieces of the model reply (`"]`, `']},`, `</div>`, `pt_alt_...`, JSON, numbers like `22-23-24-...`); when a whole line is debris, write the translation of its `en` line instead. A tag the `en` lines do not have is deleted.
- **unknown word**: write the correct Brazilian Portuguese word, such as `grunhido` for `grunnido`, `gruñido` or `GRUNT`, `assobio` for `assóbio`, `tripulantes` for `trupulantes`, keeping the case of the original. Skip when the word is correct: an interjection (`uhuu`), a name, a term of the movie's world (`wakandanos`, `tulkun`), or a valid coinage or diminutive (`criogênese`, `homemzinho`).
- **grammar**: apply the fix LanguageTool suggests when it is right, keeping the line break: `vão / mate-o` becomes `vão / matá-lo`, `de / a esposa` becomes `da / esposa` or `de / sua esposa`, `O que?` becomes `O quê?`, `um trincheira` becomes `uma trincheira`. Skip when the original is right, such as a feminine form for a woman (`uma ciborgue`) or slang (`o porra` for a person).
- **portugal portuguese**: replace the word with the Brazilian one.
- **accepted with problem**: the translation failed every retry. Read the entry: fix what is wrong with the rules above, or leave it when it is right.

When a translation has the warning entry `[Legenda traduzida automaticamente...]` at the start, leave it.

After fixing a file, delete its report so the next check-translate run checks it again. Never edit a report and never run check-translate; the user does. At the end, list the skipped entries and the flags left alone because the text was already right.
