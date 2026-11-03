# TODO

Open items for fix-translations and check-translate. Read `AGENTS.md` first, its Principle section above all: fix-translations changes an entry only when the change cannot be wrong, and everything that needs judgment is left to check-translate and the fix-translate skill.

Verify every change with `just test` and on a scratch copy of a few movie folders, as AGENTS.md says; fix-translations, warn and `just apply-fixes` take `--backups <folder>` for a scratch run.

- **Descriptions split across two lines** (`[WHISTLING` / `"SPIDER-MAN THEME"]`) and descriptions with a tag inside are never fixed. A missed fix costs nothing, so change this only if the fix cannot be wrong.
- **NER names in check-translate**: the gender and unknown word rules skip names by their capital letter, which does not work in entries in capitals only. `Movie.from_subs_folder` could cover them; add it only when the reports show false flags of that kind.
- **Names NER misses**: a name that appears only inside descriptions, or that NER does not tag, keeps the model's text in descriptions until it is in `data/check-words.txt`. Add such a name with `just add-check-words` only when it stays unchanged in Brazilian Portuguese; `K` is left out, since a single letter would cover too much. World terms with a Portuguese translation, such as `skimwing` and `direhorse`, stay out, so check-translate still flags them when they are left in English.
