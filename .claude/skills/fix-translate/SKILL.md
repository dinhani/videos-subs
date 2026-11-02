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

The corrections in `RULES.md` are the usual fixes, not substitutions. When a correction gives a text that does not make sense, write the text that does. The check is a heuristic and can flag a correct entry: leave its text as it is and mark it, as the procedure says.

## Procedure

Fix each entry flagged in the check-translate reports directly in its translation, from the report alone. Never run `just translate` again and never change the English `.eng.srt`.

Reports with issues, unless the user names files:

```
grep -L "· 0 issues" Z:/midias/filmes/*/_checks/*.translated.txt Z:/midias/series/*/_checks/*.translated.txt Z:/midias/series/*/*/_checks/*.translated.txt
```

The first line of a report names its translation, the `.pob.translated.srt` or `.pob.sdh.translated.srt` in the movie folder, next to `_checks`. Each flagged entry quotes its exact `en` and `pt` lines from the `.srt`, with tags, marked with `>>` and its number, between the 2 entries before and after it. Those entries are context, to read only: use them for a sentence that continues across entries, gender, and the form of address between the characters. Edit only the entry marked `>>`. Do not read the rest of the file. When the `pt` text of an entry is not unique in the file, find the entry by its number with Grep and include its number and time lines in the edit.

Read `RULES.md` at the project root first. Fix each entry with the rules for every correction at the start of section 2 (Translation), and the Correction and Caution of its rule in that section. The report does not name the LanguageTool rule of a grammar issue; find its rule (2.6 to 2.9) by the flagged text.

After fixing a file, delete its report so the next check-translate run checks it again. Never edit a report and never run check-translate; the user does. When a flag is wrong because the text is already right, mark the entry with `ignore:` on its timing line, as `RULES.md` says, in its own edit. The report does not show the timing line: find the entry by its number with Grep, then edit its timing line. When you change the text of an entry with a marker, remove the marker. At the end, list the skipped entries and the entries you marked.

When an unknown word flag is wrong because the word is right, add the word to `data/dict-words.txt`: one word per line, lowercase, with its accents, in alphabetical order, each form on its own line (`wakandano`, `wakandanos`). When a gender flag is wrong because the expression is right, add the whole expression as it is in the entry to `data/dict-expressions.txt`, in the same format (`os relés`, `luta corpo a corpo`); never add a wrong translation there. List the added words and expressions at the end too.
