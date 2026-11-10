---
name: app-translate
description: Translate the English subtitle of a movie or episode to Brazilian Portuguese, into `<name>.pob.claude.translated.srt` in the movie folder. Use when asked to translate a video, movie, episode or series with Claude.
---

# app-translate

You translate a movie or TV subtitle from English to Brazilian Portuguese, for viewers who watch it with the video.

## Hard rule: you are the translator

Translate every entry yourself, reading it in its scene. No script, regex, find-and-replace or other translator writes Portuguese text.

## Scope

- Base subtitles only. SDH is out of scope: never translate or use a `.eng.sdh.srt`.
- Skip a video that has a Brazilian Portuguese subtitle in `_subs` (`.pob.` in any format): it has an official translation.
- Skip a video that already has a `.pob.claude.translated.srt`.

## Procedure

For each video:

1. Choose the canonical English. Look at the base `.eng.srt` files of the video in `_subs`, extracted or made by OCR. The canonical one has the full dialogue of the video: not a forced-only or foreign-parts track, a commentary, or another cut. Compare entry counts, timings, and how each starts and ends. When none is clearly canonical, or there is only SDH, stop and ask the user, showing the candidates and what you found.
2. Read the translation rules in `src/shared/translation_rules.py`, the same rules the llama translation uses. In `RULES_ENTRIES`, `{unit}` is "entry".
3. Read the context: the `.nfo` of the video, with the plot and the credited characters. For an episode, also the `tvshow.nfo` of the series, and the `.pob.claude.translated.srt` and `_drafts/<video>/notes.md` of the earlier episodes.
4. Read the references: the base `.por`, `.spa`, `.fra` and `.ita` text subtitles of the same video in `_subs`, extracted or made by OCR. Match their entries to the English entries by timing; a reference can be shifted by a constant offset.
5. Translate in parts of about 100 entries, in order, into `<movie folder>/_drafts/<video>/0001.txt`, `0002.txt`, and so on. Each entry is in `.srt` form: the number and timing line of the English entry, copied exactly but without any `ignore:` marker after the end time, then the Portuguese, with the same number of lines as the English, then a blank line. The parts are `.txt` so the media server shows nothing from `_drafts`.
   - Keep `notes.md` in the same folder: the gender of each character, the form of address between each pair of characters, and the translation of recurring terms.
   - To continue a translation that stopped, such as at a usage limit, read `notes.md` and the last part, and continue from the next entry.
6. Check before joining:
   - Every English entry is in the parts once, in order, with its number and timing, and with the same number of lines as the English.
   - Judge each entry whose tags or digits differ from the English: fix the translation, or leave it when the English is wrong, as the `1` in `you1re`.
7. Join the parts in name order into `<movie folder>/<track>.pob.claude.translated.srt`, where `<track>` is the name of the English `_subs/<track>.eng.srt` without `.eng.srt`.
8. Go to the next video, when several were named.

At the end, list the translations written, the videos skipped and why, and the questions for the user.

## The English source

The English subtitle is not perfect. It can have OCR errors, broken characters (`you1re` for `you're`, `1Cause` for `'Cause`), typos, missing or repeated words, and lines the subtitle maker added, such as a site ad. Translate what the dialogue means, not its errors: `you1re` is translated as `you're`, and its `1` does not go into the Portuguese. A line that is not dialogue, such as an ad, stays as it is. The references help to see what a broken line meant.

## References

People translated the references from the same dialogue, so they show what the English means in a scene: the sense of an idiom or of a word with several meanings, whether "you" is one person or several, the gender of who speaks or is spoken about, and tense. Use them to understand the English, and translate the English:

- When a reference leaves something out, adds something or says something else, follow the English. Few matched entries or OCR errors make a reference less reliable.
- Never copy the words, spellings, idioms or sentence structure of a reference.
- Address people as the register rules say, not as a reference does: European Portuguese, Spanish, French and Italian use formal address far more often than Brazilian Portuguese. Never write European Portuguese forms (`RULES_EUROPEAN_PORTUGUESE`).
- What the kept-unchanged rules keep, such as song lyrics, stays as in the English even when a reference translates it.

## Consistency

The translation is consistent across the whole subtitle, and across the episodes of a series through the notes of the earlier episodes: a character keeps their gender, two characters keep the same form of address between them, a term keeps its translation.

## After

The user runs `just check-translate` and `just warn`, and the app-fix-translate skill fixes the flags. fix-translations does not touch Claude translations.
