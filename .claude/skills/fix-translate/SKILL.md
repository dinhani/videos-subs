---
name: fix-translate
description: Fix the entries that check-translate flagged in the Brazilian Portuguese translations, deciding each entry on its own before changing it. Use after `just translate` and `just check-translate`, when asked to fix translation errors or check-translate issues.
---

# fix-translate

## Hard rule: decide every entry yourself

Every entry you change must be one you read in the report and decided on its own: what it says, what is wrong, and what the right text is. Similar flags do not share a fix; the same word can be right in one entry and wrong in the next.

Forbidden: a change decided by a pattern instead of by reading the entry, such as a regex or find-and-replace across entries, a script that chooses the fix, or a fix copied to entries you did not read.

Allowed: any tool to read, list and apply. After deciding a set of entries one by one, you may apply all of them in one script or edit, as long as each change holds the exact old and new text you wrote for that entry.

The corrections in `RULES.md` are the usual fixes, not substitutions. When a correction gives a text that does not make sense, write the text that does. The check is a heuristic and can flag a correct entry: leave its text as it is and mark it, as the procedure says.

## Procedure

Fix each entry flagged in the check-translate reports directly in its translation, from the report alone. Never run `just translate` again and never change the English `.eng.srt`.

Each run fixes one movie folder: every translation report with issues in its `_checks`, base and SDH, and nothing else. When the user names a movie or files, fix those. Otherwise take the movie folder of the report with the worst score, the first line of:

```
find Z:/midias/filmes Z:/midias/series -path '*/_checks/*.translated.txt' -exec awk 'FNR == 2 && !/· 0 issues/ { print $2 "\t" FILENAME }' {} + | sort -rn
```

Fix the base and SDH translations of the movie together: an error found in both gets the same fix in both.

The first line of a report is the full path of its translation. Each flagged entry starts with `entry <number> · line <line> · ` and its timing line exactly as it is in the `.srt`, then its issues, each with its rule in `RULES.md` and the marker that ignores it, as in `unknown word · rule 2.10 · ignore:unknown-word · "parafusadeira"`. Then come its exact `en` and `pt` lines from the `.srt`, with tags, marked with `>>` and its number, between the 2 entries before and after it. Those entries are context, to read only: use them for a sentence that continues across entries, gender, and the form of address between the characters. Edit only the entry marked `>>`. `line` is the line of the entry number in the `.srt`: read the entry there, from that line to its last text line, and nothing else; no search is needed. Fix the entries of a file from the last to the first, so a fix that adds or removes a line does not move the entries still to fix. The entry number, the timing line and the text together are unique in the file: put them in the edit, as they are in the report. When the entry is not at its line, the file changed after the check: leave the file and list it at the end.

Read `RULES.md` at the project root first. Fix each entry with the rules for every correction at the start of section 2 (Translation), and the Correction and Caution of the rule the report names.

After fixing a file, delete its report so the next check-translate run checks it again. Never edit a report and never run check-translate; the user does. When a flag is wrong because the text is already right, add the marker the report gives for that issue at the end of the entry's timing line, as `RULES.md` says, in its own edit; several markers of one entry are joined by commas after one `ignore:`. When you change the text of an entry with a marker, remove the marker. At the end, list the skipped entries and the entries you marked.

When an unknown word flag is wrong because the word is right, add the word to `data/dict-words.txt`: one word per line, lowercase, with its accents, in alphabetical order, each form on its own line (`wakandano`, `wakandanos`). When a gender flag is wrong because the expression is right, add the whole expression as it is in the entry to `data/dict-expressions.txt`, in the same format (`os relés`, `luta corpo a corpo`); never add a wrong translation there. List the added words and expressions at the end too. Last, give the number of movie folders that still have a translation report with issues, and stop: do not start another movie.
