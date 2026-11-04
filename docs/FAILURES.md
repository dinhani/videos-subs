# Failed approaches

Approaches that were tried and failed, and why. Read this before proposing a new approach, and do not propose one of these again unless the reason no longer holds.

## Judging translations

### Claude as the reference translator

Claude judges translations against its own: it nitpicks valid translations, and even rates worse translations above some that Gemma wrote. The Claude reference translations of the bench lost to Gemma on naturalness, and Claude defended them by their adherence to the prompt rules instead of by what a Brazilian viewer would say: `vão grande` over `vão enorme`, `GRUNHE` over `GRUNHIDO`, and `OS DOIS GRUNHINDO` called a draw. A subtitle is judged by how it reads to the viewer, not by the rules of the prompt.

### Word choices counted as errors

In a survey of the translations, Claude counted right word choices as errors: `dirigir` where the official has `pilotar`, `Muito bem` where it has `Mandou bem`. A translation is wrong only when it says something else, or when a Brazilian would not say it. A translation is not wrong because Claude would write it another way.

### One kind of error as the most important

Claude overfocused on gender errors, as if they were the most important problem of the translations, and kept proposing work aimed at them: character genders in the prompt, video frames, review passes, test cases picked for gender. In a sample of 460 entries one entry had a wrong gender. A gender error is an issue like any other, and no kind of error deserves more focus than the others; each is weighed by how often it appears in real translations.

### SDH as the target

SDH subtitles are not watched; they are a benchmark and the last priority. The description errors of SDH translations are covered by fix-translations, and work aimed at them does not improve the subtitles that are watched.

## Fixing subtitles

### Retranslating instead of fixing

Agents running the fix skill rewrote correct lines into their own wording, up to retranslating whole entries. The skill fixes what is wrong and nothing else; a better translation goes to `docs/IMPROVEMENTS.md`.

### Looking beyond the report

Agents read whole subtitles and searched for more errors instead of working from the report, which already holds each flagged entry and its context. An error seen while working is fixed; searching for more is not the job.

### Fixing by script

An agent applied the fixes of a report with a script, deciding them by pattern. The skill exists because each entry needs judgment; a fix decided by a pattern is a mechanical fix and belongs to fix-translations, under its rule that it cannot be wrong.

### Claude at low effort fixing translations

Claude at medium effort invented Portuguese words while fixing translations, and all of its fixes were removed.

### Fixes decided from the translation alone

A fix from one Portuguese text to another, without looking at the English entry, writes text that the English does not support. The English source is the source of truth for every fix.

### Mechanical fixes that are right most of the time

Table entries whose Portuguese depends on the scene were written wrong in some entries: a sound that is different for a person, an animal or a machine (`barks`, `hisses`), a word with several meanings (`choking`, `blows`), and a subject whose gender the English does not tell (`both`, `everyone`). Two independent reviews found them, and they were removed. A fix that needs the scene belongs to the fix skill.

### Images or a new OCR run to fix OCR errors

Fixing an OCR error by looking at the image of the entry, or by running ocr again, costs too much: ocr takes hours. The fix skill acts on the text of the report.

## Checking subtitles

### Check rules whose flags are not real errors

A rule is kept only when most of its flags are errors a viewer would see. The OCR image mismatch rule and LanguageTool `LINKING_VERB_PREDICATE_AGREEMENT` were removed because their flags were not real errors.

### A rule extended beyond what was said

The principle that subtitles follow no fixed format is about `♪`. Claude extended it to brackets and quotes and stopped flagging them, but a bracket or a quote is still expected to close.

## Translating

### A bigger or another local model

About 25 models were benchmarked, translation models included (MADLAD-400, NLLB-200, Opus-MT, TranslateGemma, Hy-MT2, Tower+). None beat gemma-4-26b. gemma-4-31b-iq3 ties with it: some movies are better with one, some with the other, and it is about 5 times slower. Local models that fit the 5080 are at their limit for this task, and researching more of them is not expected to change that.

### Models that run partly on the CPU

A model that does not fit the GPU and runs partly on the CPU is too slow to use: the CPU runs at 100% and not one chunk comes back.

### Thinking

Thinking is unreliable: some replies come out bad because the reasoning is cut off in the middle, and others reason in a loop and never reply. It was benchmarked and removed, and Claude still proposed it again as a way to catch wrong-sense errors.

### The video frame of each subtitle in the translation request

Sending Gemma the frame where a subtitle appears, with the request, did not fix any of the errors tested: a frame of Clarice talking to Chilton did not change `você é a inimiga dele`, a frame without the object did not change `boina`, and in one entry the frame turned a right `Muito bem` into the wrong `Bom garoto`. A frame can only help with who speaks to whom, which is a rare error.

### A general domain instruction in the prompt

Telling the model that it is an F1 movie and to use proper F1 terms changed nothing. Only a prompt that names the exact term changed the translation, and that covers only the terms someone lists in advance.

### Chunk size

A wrong word sense is not fixed by the size of the chunk. Entry 99 of The Silence of the Lambs, `mouthpiece`, came out wrong in 6 of 7 chunk sizes from 1 to 50 entries, with a different wrong word each time, and right when sent alone without the scene. The choice is unstable in that context, and no chunking controls it.

### Temperature 1.0 on every attempt

Google recommends it for Gemma, but it scored worse on the bench than retries at rising temperatures.

### Wrapping translated lines

Wrapping the translated lines at 42 characters left orphan words on their own line. Lines are not wrapped; the player wraps them.

## Reviewing translations

### An LLM review pass after translation

A second request that asks a model for the entries with a wrong meaning, after translation, is unreliable and looks random. gemma-4-26b reviewing its own translation found 1 to 2 of 9 known errors and proposed bad fixes as often as good ones, with or without thinking. Models of other families were not better: mistral-small-3.2-24b and gpt-oss-20b caught some errors, but also marked valid translations as wrong, such as turning Clarice male in 5 entries. None found the idioms translated word by word, the most common error.

## OCR and identification

### Tesseract for OCR

Tesseract reads image subtitles worse than Gemma. It is used only to read the first entries of an image subtitle for language detection.

### The metadata of the video

The language tags and flags of the tracks in a video are not trusted for anything. identify-text and identify-ocr detect the language and kind from the text.

## Evidence and testing

### A change without evidence from the data

Claude proposed changes, such as a more complex Portugal Portuguese detection, without data showing that the current approach fails, and projected a problem seen in a few files onto every file. A change needs evidence from the library first.

### A test whose prompt names the expected answer

A test prompt that contains the word Claude expects, such as `pilotar`, proves nothing about the model.

### Prompt examples from the movies being benchmarked

A prompt whose examples come from the movies being benchmarked is overfit to them, and its better scores are not real. Examples in a prompt never come from a bench source.

### One-off tests on a single entry

The translation of a borderline entry changes with small differences in the prompt and in how the chunk is cut: `Attaboy` came out `Bom garoto` in the bench and `Muito bem` in a test with another chunk. One entry tested once proves little in either direction.

## Performance

### The NAS as the bottleneck

Claude blamed the NAS for slow steps when the time was spent in Python or in the GPU requests. Find where the time goes before naming a cause. Local copies or caches of library files are not an option.

## Scope

### Google Translate

Google Translate gave good translations, but it depends on Google. The project translates with local models only.

### Human subtitles or work at watch time

Human subtitles do not matter here: when a movie has one, picking it is the viewer's choice at watch time, and every English subtitle is still translated. The goal is every translation ready to watch, so a review that runs when a movie is picked to watch is out too.
