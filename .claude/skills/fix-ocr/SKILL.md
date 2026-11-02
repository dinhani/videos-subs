---
name: fix-ocr
description: Fix by hand, one entry at a time and never with a script, the entries that check-ocr flagged in the text subtitles made by ocr. Every fix needs a decision about that entry first. Use after `just ocr` and `just check-ocr`, when asked to fix OCR errors or check-ocr issues.
---

# fix-ocr

## Hard rule: judge every entry, never fix in bulk

This skill exists only because the fixes need judgment. If the fixes could be mechanical, there would be a script instead of this skill. Applying the rules below mechanically defeats its purpose and breaks entries the check flagged by mistake.

Forbidden, with no exception and no matter how many entries there are:

- writing or running any script (Python, PowerShell, shell) that changes a `.srt`;
- `sed`, regex replace, find-and-replace across entries or files, `replace_all`;
- a loop over entries or files, or several entries fixed in one edit.

For every flagged entry: read its issue and text, decide what the text on screen most likely says, then make one edit to that entry only, or none when it is already right.

The corrections in `RULES.md` are the usual fixes, not substitutions. When a correction gives a text that does not make sense, write the text that does. The check is a heuristic and can flag a correct entry: leave its text as it is and mark it, as the procedure says.

## Procedure

Fix each entry flagged in the check-ocr reports directly in its text subtitle, from the report alone. Never run `just ocr` again.

Reports with issues, unless the user names files:

```
grep -L "· 0 issues" Z:/midias/filmes/*/_checks/*.ocr.txt Z:/midias/series/*/_checks/*.ocr.txt Z:/midias/series/*/*/_checks/*.ocr.txt
```

The first line of a report names its text subtitle, in `_subs` next to `_checks`. Each flagged entry quotes its exact text from the `.srt`. Do not read the rest of the file.

Read `RULES.md` at the project root first. Fix each entry with the Correction and Caution of its rule in section 1 (OCR).

After fixing a file, delete its report so the next check-ocr run checks it again. Never edit a report and never run check-ocr; the user does. When a flag is wrong because the text is already right, mark the entry with `ignore:` on its timing line, as `RULES.md` says, in its own edit. The report does not show the timing line: find the entry by its number with Grep, then edit its timing line. When you change the text of an entry with a marker, remove the marker. At the end, list the skipped entries, the entries you marked, and the `.eng.srt` files that already had a `.pob.translated.srt`, since that translation was made from the old text.
