RULES_ENTRIES = """\
Each {unit} gets its own translation: never merge, split, move, skip or refuse an entry. Keep the number of lines; an entry with two speakers stays two lines, one speaker per line. When a sentence runs across lines or entries, split the Portuguese at the same point, as close as the word order allows."""
RULES_MEANING = """\
# Meaning

- Translate the meaning exactly. This is fiction for adults: never soften, censor, euphemize or omit anything. Profanity, insults, slurs, sexual content, violence, drugs and crude humor get Brazilian Portuguese expressions of the same strength and vulgarity: "fuck" is "porra", "caralho" or "foder" as the sentence requires, never "droga" or "nossa". Sarcasm stays sarcasm, threats stay threats, rudeness stays rude.
- Never mask a word that the English writes in full: "caralho", not "c*ralho". Never add warnings, notes or explanations.
- Translate what a phrase means in the scene, not its first dictionary sense: "You got me." after a joke is "Essa me pegou."; "Break a leg." before a show is "Boa sorte.".
- Adjectives and participles agree with the gender of the person they describe. Work it out from the names, earlier lines, how others address the person and the descriptions: a woman says "Estou atrasada"; a female lawyer is "a advogada".
- Translate a sound description such as [door creaks] or (sighs) by the exact sound it names, in the letter case of the English: "[FAUCET DRIPS]" is "[TORNEIRA PINGA]".
- Translate, do not localize. Units, currencies, dates, times and cultural references stay as in the English, with only the unit word translated: "30 miles" is "30 milhas", never "48 quilômetros"; "6 feet" is "6 pés"."""
RULES_REGISTER = """\
# Register

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
- "Vossa Majestade" and "Vossa Alteza" for royalty."""
RULES_KEPT = """\
# Kept unchanged

These stay exactly as in the English, in the same place relative to the text:
- names of people, places and brands, and titles of books, films, shows and songs. Ranks and honorifics before a name are translated: "Captain Rex" is "Capitão Rex", "Mr. Smith" is "Sr. Smith";
- digits;
- the symbols ♪ ♫ … - – — " ¿ ¡ # * [ ] ( ), and a leading "- " dialogue dash on its line;
- every tag, around the same words: <i>, <b>, <u> and <font ...> with their closing tags, {\\an1} to {\\an9} at the start of a line, and any other <...>, </...> or {\\...} tag. A tag without its pair stays without it;
- words in a language other than English, which the English left untranslated: "Carpe diem." stays "Carpe diem."; in "No habla español, fellas?" only "fellas" is translated: "No habla español, pessoal?";
- song lyrics: lines marked with ♪ or ♫, and lines the context shows are sung. A description of the music, such as [song playing] or (singing), is translated."""
RULES_EUROPEAN_PORTUGUESE = 'Never write European Portuguese forms: "tu" and its verb forms, "consigo" for "com você", "estar a" with the infinitive for the gerund ("está a fazer" is "está fazendo"), or spellings such as "facto", "equipa" and "tentámos".'
