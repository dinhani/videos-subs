---
name: fix-ocr
description: Fix the entries that check-ocr flagged in the text subtitles made by ocr, deciding each entry on its own before changing it. Use after `just ocr` and `just check-ocr`, when asked to fix OCR errors or check-ocr issues.
---

# fix-ocr

## Hard rule: decide every entry yourself

Every entry you change must be one you read in the report and decided on its own: what it says, what is wrong, and what the right text is. Similar flags do not share a fix; the same word can be right in one entry and wrong in the next.

Forbidden: a change decided by a pattern instead of by reading the entry, such as a regex or find-and-replace across entries, a script that chooses the fix, or a fix copied to entries you did not read.

Allowed: any tool to read, list and apply. After deciding a set of entries one by one, you may apply all of them in one script or edit, as long as each change holds the exact old and new text you wrote for that entry.

The corrections in `RULES.md` are the usual fixes, not substitutions. When a correction gives a text that does not make sense, write the text that does. The check is a heuristic and can flag a correct entry: leave its text as it is and mark it, as the procedure says.

## Procedure

Work from the reports. Your job is the flagged entries; do not look for other errors, such as by reading whole subtitles or searching for more of a mistake you fixed.

1. Read `RULES.md` at the project root: section 1 (OCR), and the rules the reports name.
2. Take one movie folder: the one the user names, or the one of the worst report, the first line of:

   ```
   find Z:/midias/filmes Z:/midias/series -path '*/_checks/*.ocr.txt' -exec awk 'FNR == 2 && !/· 0 issues/ { print $2 "\t" FILENAME }' {} + | sort -rn
   ```

   Fix every OCR report with issues in its `_checks`. An error flagged in several text subtitles gets the same fix in each.
3. For each flagged entry, read it in the report: its text is the exact text in the `.srt`, with the entries around it for context. Decide, then fix it with the Correction and Caution of its rule, or mark the flag when the text is right. Do not open the `.srt`; `just apply-fixes` checks every fix against it. When it stops because an entry's text is not the text of the report, the file changed after the check: leave the file.
4. When an error shows up while you work, in a context entry or in anything else you read for a flagged entry, fix it too.
5. Apply the fixes of the movie folder with `just apply-fixes`, then delete the report of every file you change. Never edit a report, and never run check-ocr or ocr.
6. At the end, list: the fixes outside the flagged entries, the marked entries, the skipped entries and files, and the `.eng.srt` files that already had a `.pob.translated.srt`, since that translation was made from the old text. Then give the number of movie folders that still have an OCR report with issues, and stop.

## Reference

**Report.** The first line is the full path of the text subtitle. Each flagged entry starts with `entry <number> · line <line> · ` and its timing line exactly as it is in the `.srt`, then its issues, each with its rule in `RULES.md` and the marker that ignores it, as in `artifact · rule 1.4 · ignore:artifact · markup in the text: "*never*"`. Then comes its exact text, marked with `>>` and its number, between the entries before and after it. Those entries are context: use them for a caption that continues across entries. `line` is the line of the entry number in the `.srt`.

**Fixes file.** Write the fixes of the movie folder to one JSON file in your scratchpad and run `just apply-fixes <file>`. The file is a list with one item per subtitle: `{"subtitle": "<full path of the .srt>", "entries": [...]}`. Each entry is `{"entry": <number>, "old": [<text lines>], "new": [<text lines>], "ignore": [<kinds>]}`: `old` is the exact text of the entry as the report gives it, with tags; give `new`, `ignore` or both. The tool finds each entry by its number, context entries too, checks every `old` before writing any file, and writes nothing when one does not match. Never edit a `.srt` by hand or with another script.

**Marker.** When a flag is wrong because the text is right, put the kind of the marker the report gives for that issue in `ignore`, as in `"ignore": ["artifact"]`. The tool adds it to the end of the entry's timing line, joined by commas with the kinds already there, as `RULES.md` says. A fix with `new` removes the entry's marker, so the entry is judged again.
