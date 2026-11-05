# Failed approaches

Approaches that were tried and failed, and why. Read this before proposing a new approach, and do not propose one of these again unless the reason no longer holds.

## Judging translations

### Claude as the reference translator

Claude judges translations against its own: it nitpicks valid translations, counts right word choices as errors (`dirigir` where the official has `pilotar`), and even rates worse translations above some that Gemma wrote. The Claude reference translations of the bench lost to Gemma on naturalness, and Claude defended them by their adherence to the prompt rules instead of by what a Brazilian viewer would say: `vão grande` over `vão enorme`, `GRUNHE` over `GRUNHIDO`, and `OS DOIS GRUNHINDO` called a draw. A subtitle is judged by how it reads to the viewer, and a translation is wrong only when it says something else or a Brazilian would not say it.

### One kind of error as the most important

Gender errors were treated as the most important problem of the translations, and work was aimed at them: character genders in the prompt, video frames, review passes, test cases picked for gender. In a sample of 460 entries one entry had a wrong gender. A gender error is an issue like any other, and no kind of error deserves more focus than the others; each is weighed by how often it appears in real translations.

### SDH as the target

SDH subtitles are not watched; they are a benchmark and the last priority. The description errors of SDH translations are covered by fix-translations, and work aimed at them does not improve the subtitles that are watched.

## Translating

### A bigger or another local model

About 25 models were benchmarked, translation models included (MADLAD-400, NLLB-200, Opus-MT, TranslateGemma, Hy-MT2, Tower+). None beat gemma-4-26b. gemma-4-31b-iq3 ties with it: some movies are better with one, some with the other, and it is about 5 times slower. Local models that fit the 5080 are at their limit for this task, and researching more of them is not expected to change that.

### Models that run partly on the CPU

A model that does not fit the GPU and runs partly on the CPU is too slow to use: the CPU runs at 100% and not one chunk comes back.

### Thinking

Thinking is unreliable: some replies come out bad because the reasoning is cut off in the middle, and others reason in a loop and never reply.

### The video frame of each subtitle in the translation request

Sending Gemma the frame where a subtitle appears, with the request, did not fix any of the errors tested: a frame of Clarice talking to Chilton did not change `você é a inimiga dele`, a frame without the object did not change `boina`, and in one entry the frame turned a right `Muito bem` into the wrong `Bom garoto`. A frame can only help with who speaks to whom, which is a rare error.

### Temperature 1.0 on every attempt

Google recommends it for Gemma, but it scored worse on the bench than retries at rising temperatures.

### Prompt examples from the movies being benchmarked

A prompt whose examples come from the movies being benchmarked is overfit to them, and its better scores are not real. Examples in a prompt never come from a bench source.

### Wrapping translated lines

Wrapping the translated lines at 42 characters left orphan words on their own line. Lines are not wrapped; the player wraps them.

### Google Translate

Google Translate gave good translations, but it depends on Google. The project translates with local models only.

### Cloud models as translator

Models from OpenRouter or other cloud services cost too much to translate the whole library, and there is no reason to expect them to translate better: Claude's own reference translations lost to Gemma.

## Reviewing and fixing translations

### An LLM review pass after translation

A second request that asks a model for the entries with a wrong meaning, after translation, is unreliable and looks random. gemma-4-26b reviewing its own translation found 1 to 2 of 9 known errors and proposed bad fixes as often as good ones, with or without thinking. Models of other families were not better: mistral-small-3.2-24b and gpt-oss-20b caught some errors, but also marked valid translations as wrong, such as turning Clarice male in 5 entries. None found the idioms translated word by word, the most common error.

### Mechanical fixes that are right most of the time

Table entries whose Portuguese depends on the scene were written wrong in some entries: a sound that is different for a person, an animal or a machine (`barks`, `hisses`), a word with several meanings (`choking`, `blows`), and a subject whose gender the English does not tell (`both`, `everyone`). Two independent reviews found them, and they were removed. A fix that needs the scene belongs to the fix skill.

### Object sounds as whole phrases with one form

About 200 phrases of an object and its sound, such as `engine humming` or `camera clicks`, were added to the tables with the verb form only. In a sample of 8 movies, 9 of every 10 entries they changed were already right: the model writes these sounds as nouns, such as `ZUMBIDO DO MOTOR`, `BIP DE ALARME` and `CLIQUE DE CÂMERA`, and the fix replaced them with `MOTOR ZUMBINDO`. An object sound goes in the tables only with every right form the model writes for it.

Listing those forms from the library did not work either: in 94 translations, most of these phrases appear 3 times or fewer, too few to list every right form. Most of what the model wrote was right, and the wrong forms were misspellings and English words (`ding do elevador`, `sino de elevator`, `dispositivo zumbando`, `click de arma`), which check-translate already flags as unknown words. Some right forms were ones the tables would have replaced with a wrong one: `drums playing` is `bateria tocando`, a drum kit, not `tambores tocando`.

### Human subtitles or a review at watch time

Human subtitles do not matter here: when a movie has one, picking it is the viewer's choice at watch time, and every English subtitle is still translated. The goal is every translation ready to watch, so a review that runs when a movie is picked to watch is out too.

## OCR and identification

### Tesseract for OCR

Tesseract reads image subtitles worse than Gemma. It is used only to read the first entries of an image subtitle for language detection.

### The metadata of the video

The language tags and flags of the tracks in a video are not trusted for anything. identify-text and identify-ocr detect the language and kind from the text.

### Images or a new OCR run to fix OCR errors

Fixing an OCR error by looking at the image of the entry, or by running ocr again, costs too much: ocr takes hours. The fix skill acts on the text of the report.

## Checking

### Check rules whose flags are not real errors

A rule is kept only when most of its flags are errors a viewer would see. The OCR image mismatch rule and LanguageTool `LINKING_VERB_PREDICATE_AGREEMENT` were removed because their flags were not real errors.
