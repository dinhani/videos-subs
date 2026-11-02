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
3. For each flagged entry, from the last to the first in its file: read it in the `.srt` at its `line`, from that line to its last text line. Decide, then fix it with the Correction and Caution of its rule, or mark the flag when the text is right. When the entry is not at its line, the file changed after the check: leave the file.
4. When an error shows up while you work, in a context entry or in anything else you read for a flagged entry, fix it too.
5. Delete the report of every file you change. Never edit a report, and never run check-ocr or ocr.
6. At the end, list: the fixes outside the flagged entries, the marked entries, the skipped entries and files, and the `.eng.srt` files that already had a `.pob.translated.srt`, since that translation was made from the old text. Then give the number of movie folders that still have an OCR report with issues, and stop.

## Reference

**Report.** The first line is the full path of the text subtitle. Each flagged entry starts with `entry <number> · line <line> · ` and its timing line exactly as it is in the `.srt`, then its issues, each with its rule in `RULES.md` and the marker that ignores it, as in `artifact · rule 1.4 · ignore:artifact · markup in the text: "*never*"`. Then comes its exact text, marked with `>>` and its number, between the entries before and after it. Those entries are context: use them for a caption that continues across entries. `line` is the line of the entry number in the `.srt`.

**Edit.** Fix the entries of a file from the last to the first, so a fix that adds or removes a line does not move the entries still to fix. The entry number, the timing line and the text together are unique in the file: put them in the edit, as they are in the report.

**Marker.** When a flag is wrong because the text is right, add the marker the report gives for that issue at the end of the entry's timing line, as `RULES.md` says; several markers of one entry are joined by commas after one `ignore:`. When you change the text of an entry with a marker, remove the marker.
