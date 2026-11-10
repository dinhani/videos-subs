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
2. Read the context: the `.nfo` of the video, with the plot and the credited characters. For an episode, also the `tvshow.nfo` of the series and the `_drafts/<video>/notes.md` of the earlier episodes; open the `.pob.claude.translated.srt` of an earlier episode only when its notes do not answer something.
3. Read the references: the base `.por`, `.spa`, `.fra` and `.ita` text subtitles of the same video in `_subs`, extracted or made by OCR, matched to the English entries by timing with a tool.
4. Translate in parts of about 100 entries, in order, into `<movie folder>/_drafts/<video>/0001.txt`, `0002.txt`, and so on. Each entry is `#<number>` of the English entry on its own line, then the Portuguese, with the same number of lines as the English. The join tool takes the timings from the English. The parts are `.txt` so the media server shows nothing from `_drafts`.
   - Keep `notes.md` in the same folder: the gender of each character, the form of address between each pair of characters, and the translation of recurring terms.
   - To continue a translation that stopped, such as at a usage limit, read `notes.md` and the last part, and continue from the next entry.
5. Check the parts with a tool before joining:
   - Every English entry is in the parts once, with the same number of lines as the English.
   - Judge each entry whose tags or digits differ from the English: fix the translation, or leave it when the English is wrong, as the `1` in `you1re`.
6. Join the parts with a tool into `<movie folder>/<track>.pob.claude.translated.srt`, where `<track>` is the name of the English `_subs/<track>.eng.srt` without `.eng.srt`: each entry with the number and timing of its English entry, without the `ignore:` markers.
7. Go to the next video, when several were named.

At the end, list the translations written, the videos skipped and why, and the questions for the user.

## Tools

What has one right answer is done by tools, never by hand: matching the references to the English entries by timing, checking the parts against the English, and joining them into the `.srt`. The tools live in `src/translate-tools/` and run through the `data/translate` recipes of the justfile. When a tool you need does not exist, or does not do what you need, build it or change it there, following `AGENTS.md`, and reuse `src/shared/` (`parse_srt`, `SubtitleFile`, `AlignedSubtitle`, `replace_text`). A tool never writes Portuguese text: it only reads, aligns, checks and joins what you wrote. Commit a new or changed tool on its own.

## Entries

Each entry gets its own translation: never merge, split, move, skip or refuse an entry. Keep the number of lines; an entry with two speakers stays two lines, one speaker per line. When a sentence runs across lines or entries, split the Portuguese at the same point, as close as the word order allows.

## Meaning

- Translate the meaning exactly. This is fiction for adults: never soften, censor, euphemize or omit anything. Profanity, insults, slurs, sexual content, violence, drugs and crude humor get Brazilian Portuguese expressions of the same strength and vulgarity: "fuck" is "porra", "caralho" or "foder" as the sentence requires, never "droga" or "nossa". Sarcasm stays sarcasm, threats stay threats, rudeness stays rude.
- Never mask a word that the English writes in full: "caralho", not "c*ralho". Never add warnings, notes or explanations.
- Translate what a phrase means in the scene, not its first dictionary sense: "You got me." after a joke is "Essa me pegou."; "Break a leg." before a show is "Boa sorte.".
- Adjectives and participles agree with the gender of the person they describe. Work it out from the names, earlier lines, how others address the person and the descriptions: a woman says "Estou atrasada"; a female lawyer is "a advogada".
- Translate a sound description such as [door creaks] or (sighs) by the exact sound it names, in the letter case of the English: "[FAUCET DRIPS]" is "[TORNEIRA PINGA]".
- Translate, do not localize. Units, currencies, dates, times and cultural references stay as in the English, with only the unit word translated: "30 miles" is "30 milhas", never "48 quilômetros"; "6 feet" is "6 pés".

## Register

Match the register of the English: casual stays casual, formal stays formal. Write dialogue as Brazilians speak it today, not as books write it:

- Drop the subject pronoun when the verb shows the subject: "Acho que errei o caminho", not "Eu acho que eu errei o caminho".
- Use "vai" with the infinitive for the future: "ela vai adorar", not "ela adorará".
- Use everyday words: "Consegui os ingressos", not "Obtive os ingressos".
- Say it as briefly as the English does, without politeness or filler it does not have: "Give me that." is "Me dá isso.", not "Você poderia me dar isso?".
- In casual speech, use "a gente" for "we", "pra" for "para", "tá" for "está", "cadê" for "where is", "Tá" or "Tá bom" for "Okay", and the spoken imperative: "A gente precisa de um barco", "Espera", "Olha", "Vem", not "Nós precisamos de um barco", "Espere", "Olhe", "Venha".
- A character who speaks formally in English, or a formal situation, keeps the written forms: "O senhor deseja mais alguma coisa?".

Address people as the scene calls for, and keep the same form between the same characters:

- "você" in everyday speech, never "tu";
- "o senhor" and "a senhora" for formal address, such as to a superior, an elder or a client;
- "Vossa Majestade" and "Vossa Alteza" for royalty.

Never write European Portuguese forms: "tu" and its verb forms, "consigo" for "com você", "estar a" with the infinitive for the gerund ("está a fazer" is "está fazendo"), or spellings such as "facto", "equipa" and "tentámos".

## Kept unchanged

These stay exactly as in the English, in the same place relative to the text:

- names of people, places and brands, and titles of books, films, shows and songs. Ranks and honorifics before a name are translated: "Captain Rex" is "Capitão Rex", "Mr. Smith" is "Sr. Smith";
- digits;
- the symbols ♪ ♫ … - – — " ¿ ¡ # * [ ] ( ), and a leading "- " dialogue dash on its line;
- every tag, around the same words: `<i>`, `<b>`, `<u>` and `<font ...>` with their closing tags, `{\an1}` to `{\an9}` at the start of a line, and any other `<...>`, `</...>` or `{\...}` tag. A tag without its pair stays without it;
- words in a language other than English, which the English left untranslated: "Carpe diem." stays "Carpe diem."; in "No habla español, fellas?" only "fellas" is translated: "No habla español, pessoal?";
- song lyrics: lines marked with ♪ or ♫, and lines the context shows are sung. A description of the music, such as [song playing] or (singing), is translated.

## The English source

The English subtitle is not perfect. It can have OCR errors, broken characters (`you1re` for `you're`, `1Cause` for `'Cause`), typos, missing or repeated words, and lines the subtitle maker added, such as a site ad. Translate what the dialogue means, not its errors: `you1re` is translated as `you're`, and its `1` does not go into the Portuguese. A line that is not dialogue, such as an ad, stays as it is. The references help to see what a broken line meant.

## References

People translated the references from the same dialogue, so they show what the English means in a scene: the sense of an idiom or of a word with several meanings, whether "you" is one person or several, the gender of who speaks or is spoken about, and tense. Use them to understand the English, and translate the English:

- When a reference leaves something out, adds something or says something else, follow the English. Few matched entries or OCR errors make a reference less reliable.
- Never copy the words, spellings, idioms or sentence structure of a reference.
- Address people as the Register section says, not as a reference does: European Portuguese, Spanish, French and Italian use formal address far more often than Brazilian Portuguese.
- What the Kept unchanged section keeps, such as song lyrics, stays as in the English even when a reference translates it.

## Consistency

The translation is consistent across the whole subtitle, and across the episodes of a series through the notes of the earlier episodes: a character keeps their gender, two characters keep the same form of address between them, a term keeps its translation.

## After

The user runs `just check-translate` and `just warn`, and the app-fix-translate skill fixes the flags. fix-translations does not touch Claude translations.
