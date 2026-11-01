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

The rules per issue kind below are the usual fixes, not substitutions. When a rule gives a text that does not make sense, write the text that does. The check is a heuristic and can flag a correct entry: leave it and list it at the end.
## Procedure

Fix each entry flagged in the check-ocr reports directly in its text subtitle, from the report alone. Never run `just ocr` again.

Reports with issues, unless the user names files:

```
grep -L "· 0 issues" Z:/midias/filmes/*/_checks/*.ocr.txt Z:/midias/series/*/_checks/*.ocr.txt Z:/midias/series/*/*/_checks/*.ocr.txt
```

The first line of a report names its text subtitle, in `_subs` next to `_checks`. Each flagged entry quotes its exact text from the `.srt`. Do not read the rest of the file. Fix every entry except the kinds marked skip below.

- **glyph confusion**: `|` → `I`, `0` → `O` inside a word, `l'll` → `I'll`. A repeated word is a stammer: `| I can` → `I-- I can`.
- **artifact**: markdown emphasis becomes `<i>...</i>`, or no tag when the line is already inside `<i>`; a tag with attributes such as `<i style="...">` becomes `<i>`, and `</em>` becomes `</i>`; stray `{`, `<`, `` ` `` are deleted.
- **inconsistent**: copy the text of the entry the report names.
- **unbalanced**: add the missing bracket at the start or end of the caption. Skip when the brackets in the quoted text already pair up; the check is wrong there.
- **wrong order**: swap the lines. Skip when the lines already read correctly in their current order, such as a sentence that continues from the first line to the second or a speaker label above its line; the check is wrong there.
- **ocr failed**: skip.

After fixing a file, delete its report so the next check-ocr run checks it again. Never edit a report and never run check-ocr; the user does. At the end, list the skipped entries, the flags left alone because the text was already right, and the `.eng.srt` files that already had a `.pob.translated.srt`, since that translation was made from the old text.
