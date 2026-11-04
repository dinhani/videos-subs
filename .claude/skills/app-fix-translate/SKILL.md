---
name: app-fix-translate
description: Fix the entries that check-translate flagged in the Brazilian Portuguese translations, deciding each entry on its own before changing it. Use after `just translate`, `just fix-translations` and `just check-translate`, when asked to fix translation errors or check-translate issues.
---

# app-fix-translate

## Hard rule: decide every entry yourself

Every entry you change must be one you read in the report and decided on its own: what it says, what is wrong, and what the right text is. Similar flags do not share a fix; the same word can be right in one entry and wrong in the next.

Forbidden: a change decided by a pattern instead of by reading the entry, such as a regex or find-and-replace across entries, a script that chooses the fix, or a fix copied to entries you did not read.

Allowed: any tool to read, list and apply. After deciding a set of entries one by one, you may apply all of them in one script or edit, as long as each change holds the exact old and new text you wrote for that entry.

## Hard rule: fix, do not retranslate

You are a fixer, not a translator. Change only what is wrong: a typo, a word in another language, broken grammar, pieces of the model reply, a line that says something other than its English line. A correct word or phrase stays as it is, flagged or not, even when you would translate it differently.

When you think a correct entry could be better, do not change it: add a note to `docs/IMPROVEMENTS.md`. Add a note too for every error you fixed that no rule flagged, so a new rule can catch it. Each note is one list item: the translation file, the entry number, the `en` and `pt` text, and what is wrong or could be better. At the end, list the notes you added.

The corrections in `docs/RULES.md` are the usual fixes, not substitutions. When a correction gives a text that does not make sense, write the text that does. The check is a heuristic and can flag a correct entry: leave its text as it is and mark it, as the procedure says.

## Procedure

Work from the reports. Your job is the flagged entries; do not look for other errors, such as by reading whole subtitles or searching for more of a mistake you fixed.

1. Read `docs/RULES.md`: the rules for every correction at the start of section 2 (Translation), and the rules the reports name.
2. Take one movie folder: the one the user names, or the one of the worst report, the first line of:

   ```
   find Z:/midias/filmes Z:/midias/series -path '*/_checks/*.translated.txt' -exec awk 'FNR == 2 && !/· 0 issues/ { print $2 "\t" FILENAME }' {} + | sort -rn
   ```

   Fix every translation report with issues in its `_checks`, base and SDH. An error flagged in both gets the same fix in both.
3. For each flagged entry, read it in the report: its `en` and `pt` lines are the exact text in the `.srt`, with the entries around it for context. Decide, then fix it with the Correction and Caution of its rule, or mark the flag when the text is right. Do not open the `.srt`; `just apply-fixes` checks every fix against it. When it stops because an entry's text is not the text of the report, the file changed after the check: leave the file.
4. When an error shows up while you work, in a context entry or in anything else you read for a flagged entry, fix it too.
5. Apply the fixes of the movie folder with `just apply-fixes`, then delete the report of every file you change. Never edit a report, never run check-translate or translate, and never change the English `.eng.srt`.
6. At the end, list: the fixes outside the flagged entries, the marked entries, the skipped entries and files, the words, expressions and pairs added to `data`, and the notes added to `docs/IMPROVEMENTS.md`. Then give the number of movie folders that still have a translation report with issues, and stop.

## Reference

**Report.** The first line is the full path of the translation. Each flagged entry starts with `entry <number> · line <line> · ` and its timing line exactly as it is in the `.srt`, then its issues, each with its rule in `docs/RULES.md` and the marker that ignores it, as in `unknown word · rule 2.10 · ignore:unknown-word · "parafusadeira"`. Then come its exact `en` and `pt` lines, with tags, marked with `>>` and its number, between the 2 entries before and after it. Those entries are context: use them for a sentence that continues across entries, gender, and the form of address between the characters. `line` is the line of the entry number in the `.srt`.

**Fixes file.** Write the fixes of the movie folder to one JSON file in your scratchpad and run `just apply-fixes <file>`. The file is a list with one item per subtitle: `{"subtitle": "<full path of the .srt>", "entries": [...]}`. Each entry is `{"entry": <number>, "old": [<text lines>], "new": [<text lines>], "ignore": [<kinds>]}`: `old` is the exact text of the entry as the report gives it, with tags; give `new`, `ignore` or both. The tool finds each entry by its number, context entries too, checks every `old` before writing any file, and writes nothing when one does not match. Never edit a `.srt` by hand or with another script.

**Marker.** When a flag is wrong because the text is right, put the kind of the marker the report gives for that issue in `ignore`, as in `"ignore": ["unknown-word"]`. The tool adds it to the end of the entry's timing line, joined by commas with the kinds already there, as `docs/RULES.md` says. A fix with `new` removes the entry's marker, so the entry is judged again.

**Data lists.** When an unknown word flag is wrong because the word is right, add the word with `just add-check-words <word>...`, with its accents and each form on its own (`wakandano wakandanos`); it goes to `data/check-words.txt` in lowercase and in alphabetical order. When a gender flag is wrong because the expression is right, add the whole expression as it is in the entry with `just add-check-expressions "<expression>"...` (`"os relés" "luta corpo a corpo"`); it goes to `data/check-expressions.txt`. Never add a wrong translation there.

**Fix tables.** `just fix-translations` writes descriptions and misspellings from the tables in `data`, in every translation. When you fix a description whose right translation is the same in every movie and scene, add it with `just add-fix-sounds "<english> = <português> | <other right form>..."` (`"grunts = grunhe | grunhido | grunhidos"`), the whole English description in lowercase, the first form with the verb in the third person for -s and in the gerund for -ing, then the other forms that are also right, which fix-translations leaves as they are. Never add a description whose Portuguese changes with the subject, the gender or the scene; add a subject that comes before such a phrase with `just add-fix-subjects "<english> = <português>"`. When you fix a misspelling the model repeats, add it with `just add-fix-words "<english> = <misspelled> = <right>"` (`"maybe = talve = talvez"`), with the English word of the entry that the misspelled word translates; fix-translations replaces it only in entries whose English has that word. The tool refuses a misspelling that is a Brazilian Portuguese word. List the pairs added at the end, with the other data list changes.
