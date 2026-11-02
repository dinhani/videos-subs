# Rules

Each example shows an entry line by line: `ocr` is the OCR text, `en` the English entry, `pt` the translation, and `fix` the corrected text.

A rule is a heuristic: it can flag a correct entry. Judge every flag, and leave the entry when it is right.

# 1. OCR

Every correction writes what the picture most likely says.

## 1.1 ocr failed

- **About**: every attempt to transcribe the entry failed.
- **Examples**:
  ```
  ocr  [OCR failed]
  ```
- **Correction**: none; leave the entry.
- **Caution**: the report has no text to work from.

## 1.2 wrong order

- **About**: the lines fit the picture only in reverse order. Happens in SDH tracks, where Gemma reads the left speaker first.
- **Examples**:
  ```
  ocr  - Over here.
       - Where are you?
  fix  - Where are you?
       - Over here.
  ```
- **Correction**: swap the lines.
- **Caution**: lines that read correctly in their order are right, such as a sentence that continues from line 1 to line 2, or a speaker label above its line.

## 1.3 inconsistent

- **About**: the entry has the same picture as another entry but a different text.
- **Examples**:
  ```
  entry 12   Let's go.
  entry 480  Let's g0.
  fix        Let's go.
  ```
- **Correction**: copy the text of the entry the report names.
- **Caution**: the named entry is the most common reading, not always the right one; when it has the error, fix that entry instead.

## 1.4 artifact

- **About**: markup the model added.
- **Examples**:
  ```
  ocr  I said *never* go there.
  fix  I said <i>never</i> go there.

  ocr  It's _the_ Titanic.
  fix  It's <i>the</i> Titanic.

  ocr  You read <i style="color:white">Moby Dick</i>?
  fix  You read <i>Moby Dick</i>?

  ocr  I told you <i>not</em> to come.
  fix  I told you <i>not</i> to come.

  ocr  Where ` is he?
  fix  Where is he?

  ocr  Wait{ for me.
  fix  Wait for me.

  ocr  Image 3: Run!
  fix  Run!
  ```
- **Correction**: markdown emphasis becomes `<i>...</i>` around the same words; a tag with attributes becomes `<i>`; `</em>` becomes `</i>`; stray characters are deleted.
- **Caution**: only the marked words become italic, not the whole line; inside a line already wrapped in `<i>`, the emphasis is deleted with no second tag.

## 1.5 glyph confusion

- **About**: a character read as a similar one.
- **Examples**:
  ```
  ocr  | don't know.
  fix  I don't know.

  ocr  C0ME BACK!
  fix  COME BACK!

  ocr  l'll go.
  fix  I'll go.

  ocr  lT'S HIM.
  fix  IT'S HIM.

  ocr  Come wIth me.
  fix  Come with me.

  ocr  | I can do it.
  fix  I-- I can do it.
  ```
- **Correction**: write the right letter. A `|` before the same word is a stammer: `I--`.
- **Caution**: codes with digits between letters can be right, such as `MP5s`.

## 1.6 unbalanced

- **About**: a bracket opened and not closed within 3 entries, or closed with nothing open.
- **Examples**:
  ```
  ocr  [GUNSHOTS
  fix  [GUNSHOTS]

  ocr  sighs)
  fix  (sighs)
  ```
- **Correction**: add the missing bracket at the start or end of the caption.
- **Caution**: brackets that pair up in the quoted text, or across more than 3 entries, are right; emoticons (`:)`) and list numbers (`1) Load the gun.`) are not brackets.

# 2. Translation

Every correction:

- keeps the number of lines of the English entry, each line the translation of the English line in the same place;
- keeps the tags of the English lines (`<i>`, `</i>`, `{\an8}`, `<font ...>`) in the same places;
- is natural spoken Brazilian Portuguese, in the form the movie calls for (usually `você`; `o senhor`, `a senhora` or `Vossa Majestade` for formal address) and the same form the translation already uses between those characters;
- keeps profanity as strong as in the English.

The warning entries at the start of a translation, `[Legenda SDH...]` and `[Legenda traduzida automaticamente...]`, stay.

## 2.1 accepted with problem

- **About**: the translation of the entry failed every retry and its last reply was kept.
- **Examples**:
  ```
  en   I'm fine.
  pt   I'm fine.
  fix  Estou bem.
  ```
- **Correction**: fix the entry with the rule that matches its problem.
- **Caution**: the entry may be right; the problem may be in another entry of the same request.

## 2.2 untranslated

- **About**: a line reads as English, or is its English line unchanged.
- **Examples**:
  ```
  en   Get out of here!
  pt   Get out of here!
  fix  Sai daqui!
  ```
- **Correction**: translate the line.
- **Caution**: names, titles of works, brands, and words the movie says in another language (Latin, Na'vi, Spanish) stay as they are: `Star Wars`, `Carpe diem.` English song lyrics stay in English, also without `♪`, such as `<i>Hold me when you go to sleep</i>`.

## 2.3 added symbols

- **About**: symbols, tags or letters of another script that the English entry does not have, mostly pieces of the model reply.
- **Examples**:
  ```
  en   Let's go.
  pt   Vamos embora.']},
  fix  Vamos embora.

  en   Easy.
  pt   pt_alt_1: Calma.
  fix  Calma.

  en   Easy.
  pt   Calma.</div>
  fix  Calma.

  en   Run!
  pt   Corra!"]
  fix  Corra!

  en   Stay here.
  pt   Fique aqui. 22-23-24-25-26
  fix  Fique aqui.

  en   Run!
  pt   {"pt": ["Corra!"]}
  fix  Corra!

  en   Watch out!
  pt   <b>Cuidado!</b>
  fix  Cuidado!
  ```
- **Correction**: delete the debris; when the whole line is debris, write the translation of its English line. Delete a tag the English entry does not have.
- **Caution**: keep the tags the English entry has, such as `<i>` and `{\an8}`.

## 2.4 duplicated line

- **About**: one line holds the translation of the whole entry and another line repeats part of it.
- **Examples**:
  ```
  en   I told you
       to stay home.
  pt   Eu te disse para ficar em casa.
       para ficar em casa.
  fix  Eu te disse
       para ficar em casa.
  ```
- **Correction**: split the long line at the English line break and remove the repeated part.
- **Caution**: each line translates its own English line; keep the number of lines.

## 2.5 broken line

- **About**: a line without letters or digits, or starting with `,`, `;` or `:`.
- **Examples**:
  ```
  en   I don't know,
       maybe tomorrow.
  pt   Não sei,
       ,
  fix  Não sei,
       talvez amanhã.
  ```
- **Correction**: replace the line with the translation of its English line.
- **Caution**: the other line may already hold this line's text; then split it as in duplicated line.

## 2.6 grammar: verb broken at a line break

- **About**: LanguageTool `AUXILIARY_VERB_INFINITIVE`.
- **Examples**:
  ```
  en   They're gonna
       kill him.
  pt   Eles vão
       mate-o.
  fix  Eles vão
       matá-lo.
  ```
- **Correction**: fix the verb, with the LanguageTool suggestion (`maybe "..."` in the report) when it is right.
- **Caution**: keep the line break; do not merge the lines.

## 2.7 grammar: missing contraction

- **About**: LanguageTool `CONTRACOES_OBRIGATORIAS`.
- **Examples**:
  ```
  en   Take care of
       the wife.
  pt   Cuide de
       a esposa.
  fix  Cuide da
       esposa.
  ```
- **Correction**: contract, or rephrase as `de` and `sua esposa`, with the LanguageTool suggestion when it is right.
- **Caution**: keep the line break; do not merge the lines.

## 2.8 grammar: que and quê

- **About**: LanguageTool `QUE_QUÊ`.
- **Examples**:
  ```
  en   What?
  pt   O que?
  fix  O quê?
  ```
- **Correction**: write `quê`.
- **Caution**: only at the end of a phrase; `O que você quer?` is right.

## 2.9 grammar: gender agreement

- **About**: LanguageTool `GENERAL_GENDER_AGREEMENT_ERRORS` and `ERRO_DE_CONCORDNCIA_DO_GÉNERO_MASCULINO_O`. A flag on a name, or inside an expression of `data/dict-expressions.txt`, is not reported.
- **Examples**:
  ```
  en   Get in a trench!
  pt   Entre em um trincheira!
  fix  Entre em uma trincheira!

  en   It's on the fucking ceiling!
  pt   Está no porra do teto!
  fix  Está na porra do teto!
  ```
- **Correction**: fix the article or adjective, with the LanguageTool suggestion when it is right. `porra` as an intensifier is feminine: `a porra do`, `na porra do`, `uma porra de`.
- **Caution**: `seu porra!` as an insult to a man, and feminine forms for women (`uma ciborgue`), are right. Add a right expression to `data/dict-expressions.txt` as it is in the entry, in lowercase and whole (`os relés`, `luta corpo a corpo`), so it is not flagged again.

## 2.10 unknown word

- **About**: a rare word missing from the pt_BR dictionary and from `data/dict-words.txt`: a typo, a broken word or a word of another language.
- **Examples**:
  ```
  en   A growl.
  pt   Um gruñido.
  fix  Um grunhido.

  en   You see?
  pt   Você percece?
  fix  Você percebe?
  ```
- **Correction**: write the right Brazilian Portuguese word, keeping its case.
- **Caution**: interjections (`uhuu`), names, terms of the movie's world (`wakandanos`, `tulkun`, `droide`, `hipersono`), valid coinages (`criogênese`) and diminutives (`homemzinho`) are right. Add a right word to `data/dict-words.txt`, so it is not flagged again.

## 2.11 unknown word in caps

- **About**: an unknown word in capital letters, usually a misspelled sound description.
- **Examples**:
  ```
  en   [GRUNTS]
  pt   [GRUNNIDO]
  fix  [GRUNHIDO]

  en   [WHISTLING]
  pt   [ASSÓBIO]
  fix  [ASSOBIO]

  en   [LAUGHING]
  pt   [RISINDO]
  fix  [RINDO]

  en   CREW MEMBERS
  pt   TRUPULANTES
  fix  TRIPULANTES
  ```
- **Correction**: write the right word in capitals.
- **Caution**: a shouted word or a name in capitals can be right. Add a right word to `data/dict-words.txt` in lowercase, so it is not flagged again.

## 2.12 portugal portuguese

- **About**: a word or form used in Portugal and not in Brazil.
- **Examples**:
  ```
  en   Take the bus.
  pt   Apanha o autocarro.
  fix  Pega o ônibus.

  en   Call my cell phone.
  pt   Liga-me para o telemóvel.
  fix  Me liga no celular.

  en   I'm making dinner.
  pt   Estou a fazer o jantar.
  fix  Estou fazendo o jantar.

  en   Where's the bathroom?
  pt   Onde é a casa de banho?
  fix  Onde é o banheiro?
  ```
- **Correction**: write the Brazilian form.
- **Caution**: `estar a` before a noun is Brazilian too, as in `estou a favor`.
