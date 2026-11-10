# Translates English subtitles, skipping ones already saved.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import srt
from openai import OpenAI
from rich.highlighter import JSONHighlighter

from shared.alignment import AlignedSubtitle
from shared.console import Action, Answer, Batch, Color, confirm, console, log_heading, track
from shared.files import SubtitleFile, SubtitleFormat, find_subtitles, parse_srt, write_text_replacing
from shared.languages import SecondLanguage
from shared.llm import JsonReply, Trace, connect_llama
from shared.movies import MovieInfo
from shared.subtitles import LANGUAGE_SOURCE, parse_lines, replace_text

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
CHUNK_SIZE = 50
CHUNK_CONTEXT_BEFORE = 20
CHUNK_CONTEXT_AFTER = 10

LLM_SPLITS = 2 # splits before giving up on chunk
LLM_TEMPERATURES = (0.0, 0.5, 1.0)
LLM_PROMPT = """\
You translate movie and TV subtitles from English to Brazilian Portuguese.
The subtitles are from {movie}. Use what you know about it and its characters to work out who speaks to whom, their gender, the forms of address between them and the recurring terms.{movie_info}

# Input

A JSON object with:
- "context_before": the entries just before, each with its English lines ("en") and its accepted translation ("pt-BR"). Keep the terms and forms of address these translations use.
- "entries": maps an id to one entry to translate, with its English lines ("en").
- "context_after": the entries just after, each with its English lines ("en").
Use both contexts to understand the scene, but never translate them or put them in the reply.{second_language_input}

# Reply

A JSON object that maps every id in "entries", and no other id, to an object with:
- "en": the English lines of that entry, copied unchanged.
- "pt-BR": the translation, one Brazilian Portuguese line per English line.{justify_field}

Each id gets its own translation: never merge, split, move, skip or refuse an entry. Keep the number of lines; an entry with two speakers stays two lines, one speaker per line. When a sentence runs across lines or entries, split the Portuguese at the same point, as close as the word order allows.

# Meaning

- Translate the meaning exactly. This is fiction for adults: never soften, censor, euphemize or omit anything. Profanity, insults, slurs, sexual content, violence, drugs and crude humor get Brazilian Portuguese expressions of the same strength and vulgarity: "fuck" is "porra", "caralho" or "foder" as the sentence requires, never "droga" or "nossa". Sarcasm stays sarcasm, threats stay threats, rudeness stays rude.
- Never mask a word that the English writes in full: "caralho", not "c*ralho". Never add warnings, notes or explanations.
- Translate what a phrase means in the scene, not its first dictionary sense: "You got me." after a joke is "Essa me pegou."; "Break a leg." before a show is "Boa sorte.".
- Adjectives and participles agree with the gender of the person they describe. Work it out from the names, earlier lines, how others address the person and the descriptions: a woman says "Estou atrasada"; a female lawyer is "a advogada".
- Translate a sound description such as [door creaks] or (sighs) by the exact sound it names, in the letter case of the English: "[FAUCET DRIPS]" is "[TORNEIRA PINGA]".
- Translate, do not localize. Units, currencies, dates, times and cultural references stay as in the English, with only the unit word translated: "30 miles" is "30 milhas", never "48 quilômetros"; "6 feet" is "6 pés".

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
- "Vossa Majestade" and "Vossa Alteza" for royalty.

# Kept unchanged

These stay exactly as in the English, in the same place relative to the text:
- names of people, places and brands, and titles of books, films, shows and songs. Ranks and honorifics before a name are translated: "Captain Rex" is "Capitão Rex", "Mr. Smith" is "Sr. Smith";
- digits;
- the symbols ♪ ♫ … - – — " ¿ ¡ # * [ ] ( ), and a leading "- " dialogue dash on its line;
- every tag, around the same words: <i>, <b>, <u> and <font ...> with their closing tags, {\\an1} to {\\an9} at the start of a line, and any other <...>, </...> or {\\...} tag. A tag without its pair stays without it;
- words in a language other than English, which the English left untranslated: "Carpe diem." stays "Carpe diem."; in "No habla español, fellas?" only "fellas" is translated: "No habla español, pessoal?";
- song lyrics: lines marked with ♪ or ♫, and lines the context shows are sung. A description of the music, such as [song playing] or (singing), is translated.{second_language}

# Example

Input:
{"context_before": [{"en": ["Where's Victor?"], "pt-BR": ["Cadê o Victor?"]}], "entries": {"1": {"en": ["[FAUCET DRIPS]"]}, "2": {"en": ["- We need a boat.", "- Okay."]}, "3": {"en": ["I think he took", "the old highway north,"]}, "4": {"en": ["but he's gonna run out of gas."]}, "5": {"en": ["<i>Fuck, we're out of gas.</i>"]}, "6": {"en": ["Good afternoon, sir.", "May I see your boarding pass?"]}, "7": {"en": ["♪ Fly me to the moon ♪"]}}, "context_after": [{"en": ["[SIREN WAILING]"]}]}

Reply:
{example_reply}{second_language_example}
"""
LLM_JUSTIFY_FIELD = """
- "why": one short sentence that justifies the translation: what the English means in the scene and why the Portuguese words say it."""
LLM_EXAMPLE_REPLY = {
    "1": {"en": ["[FAUCET DRIPS]"], "pt-BR": ["[TORNEIRA PINGA]"], "why": "A sound description, translated word for word in capitals."},
    "2": {"en": ["- We need a boat.", "- Okay."], "pt-BR": ["- A gente precisa de um barco.", "- Tá."], "why": "Two speakers in casual speech, so \"a gente\" and \"Tá\"."},
    "3": {"en": ["I think he took", "the old highway north,"], "pt-BR": ["Acho que ele pegou", "a rodovia velha pro norte,"], "why": "The sentence continues in the next entry, so it is split at the same point."},
    "4": {"en": ["but he's gonna run out of gas."], "pt-BR": ["mas vai ficar sem gasolina."], "why": "\"Run out of gas\" means the car's fuel ends: \"ficar sem gasolina\"."},
    "5": {"en": ["<i>Fuck, we're out of gas.</i>"], "pt-BR": ["<i>Porra, a gasolina acabou.</i>"], "why": "\"Fuck\" as an exclamation of anger is \"Porra\"; the tags stay."},
    "6": {"en": ["Good afternoon, sir.", "May I see your boarding pass?"], "pt-BR": ["Boa tarde, senhor.", "Posso ver seu cartão de embarque?"], "why": "An airline employee speaks formally to a passenger."},
    "7": {"en": ["♪ Fly me to the moon ♪"], "pt-BR": ["♪ Fly me to the moon ♪"], "why": "Song lyrics stay in English."},
}

SECOND_LANGUAGE_MATCHED_MIN = 0.9
SECOND_LANGUAGE_PLACEHOLDERS = ("{second_language_input}", "{second_language}", "{second_language_example}")
SECOND_LANGUAGE_INPUT = """
An entry in "entries" or "context_after" can also have "{tag}": the lines of the {language} subtitle of the same movie that are on screen at the same time. An entry with no such lines has no "{tag}"."""
SECOND_LANGUAGE_SECTION = """

# {language} subtitle

The {language} subtitle ("{tag}") was translated by people from the same dialogue as the English, so it shows what the English means in the scene: the sense of an idiom or of a word with several meanings, whether "you" is one person or several, gender and tense. Use it only to understand the English, and translate the English:
- When the {language} leaves something out or adds something, follow the English.
- Address people as the Register section says, never as the {language} does: it uses formal address far more often than Brazilian Portuguese.
- What the Kept unchanged section keeps, such as song lyrics, stays as in the English even when the {language} translates it.
- Never copy {language} words, spellings, idioms or sentence structure; write Brazilian Portuguese.{forms}"""
SECOND_LANGUAGE_EXAMPLE = """

Input with a {language} subtitle:
{example_request}

Reply:
{example_reply}"""
SECOND_LANGUAGE_FORMS = {
    SecondLanguage.EUROPEAN_PORTUGUESE: 'Never write European Portuguese forms: "tu" and its verb forms, "consigo" for "com você", "estar a" with the infinitive for the gerund ("está a fazer" is "está fazendo"), or spellings such as "facto", "equipa" and "tentámos".',
}
SECOND_LANGUAGE_EXAMPLES = {
    SecondLanguage.EUROPEAN_PORTUGUESE: (("I'm ready.", "Estou pronta.", "Estou pronta.", "The {language} shows the speaker is a woman: \"pronta\"."), ("Take the bus.", "Apanha o autocarro.", "Pega o ônibus.", "\"Take the bus\" means ride it: \"pegar o ônibus\" in Brazilian Portuguese.")),
    SecondLanguage.SPANISH: (("I'm ready.", "Estoy lista.", "Estou pronta.", "The {language} shows the speaker is a woman: \"pronta\"."), ("It's a piece of cake.", "Es pan comido.", "É moleza.", "\"A piece of cake\" means easy: \"É moleza\".")),
    SecondLanguage.FRENCH: (("I'm ready.", "Je suis prête.", "Estou pronta.", "The {language} shows the speaker is a woman: \"pronta\"."), ("It's a piece of cake.", "C'est du gâteau.", "É moleza.", "\"A piece of cake\" means easy: \"É moleza\".")),
    SecondLanguage.ITALIAN: (("I'm ready.", "Sono pronta.", "Estou pronta.", "The {language} shows the speaker is a woman: \"pronta\"."), ("It's a piece of cake.", "È un gioco da ragazzi.", "É moleza.", "\"A piece of cake\" means easy: \"É moleza\".")),
}

# ------------------------------------------------------------------------------
# Second language
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class SecondLanguageSubtitle:
    language: SecondLanguage
    file: SubtitleFile
    offset: timedelta
    lines: dict[int, tuple[str, ...]]
    entries_english: int

    @classmethod
    def find(cls, language: SecondLanguage, english: SubtitleFile, english_entries: list[srt.Subtitle]) -> "SecondLanguageSubtitle | None":
        # pick most entries
        candidates = [(file, parse_srt(file.path)) for file in english.find_text_subtitles(language.code)]
        if not candidates:
            return None
        file, entries = max(candidates, key=lambda candidate: len(candidate[1]))

        # match lines on screen
        aligned = AlignedSubtitle.from_entries(entries, english=english_entries)
        english_texts = [i for i, entry in enumerate(english_entries) if parse_lines(entry)]
        return cls(language=language, file=file, offset=aligned.offset, lines=aligned.find_lines(english_entries), entries_english=len(english_texts))

    def is_matched(self) -> bool:
        return len(self.lines) >= SECOND_LANGUAGE_MATCHED_MIN * self.entries_english

    def to_prompt_parts(self, justify: bool) -> dict[str, str]:
        forms = SECOND_LANGUAGE_FORMS.get(self.language)
        examples = SECOND_LANGUAGE_EXAMPLES[self.language]
        example_request = {"context_before": [], "entries": {str(n): {"en": [english], self.language.tag: [second]} for n, (english, second, _, _) in enumerate(examples, start=1)}, "context_after": []}
        example_reply = {str(n): {"en": [english], "pt-BR": [translation], **({"why": why} if justify else {})} for n, (english, _, translation, why) in enumerate(examples, start=1)}
        texts = (
            SECOND_LANGUAGE_INPUT,
            SECOND_LANGUAGE_SECTION.replace("{forms}", f"\n- {forms}" if forms else ""),
            SECOND_LANGUAGE_EXAMPLE.replace("{example_request}", json.dumps(example_request, ensure_ascii=False)).replace("{example_reply}", json.dumps(example_reply, ensure_ascii=False)),
        )
        return {placeholder: text.replace("{language}", self.language.label).replace("{tag}", self.language.tag) for placeholder, text in zip(SECOND_LANGUAGE_PLACEHOLDERS, texts)}

    def to_record(self) -> dict[str, Any]:
        return {"second_language": self.file.name, "offset_seconds": self.offset.total_seconds(), "matched": len(self.lines), "entries": self.entries_english}

# ------------------------------------------------------------------------------
# Translation
# ------------------------------------------------------------------------------
@dataclass
class SubtitleTranslation:
    entries: list[srt.Subtitle]
    lines: list[tuple[str, ...]]
    pending: list[int]
    trace: Trace
    temperatures: tuple[float, ...]
    prompt: str
    second: SecondLanguageSubtitle | None
    justify: bool
    translated: dict[int, Sequence[str]] = field(default_factory=dict)

    @classmethod
    def from_entries(cls, entries: list[srt.Subtitle], trace: Trace, temperatures: tuple[float, ...], prompt: str, second: SecondLanguageSubtitle | None, justify: bool) -> "SubtitleTranslation":
        lines = [parse_lines(entry) for entry in entries]
        return cls(entries=entries, lines=lines, pending=[i for i, entry_lines in enumerate(lines) if entry_lines], trace=trace, temperatures=temperatures, prompt=prompt, second=second, justify=justify)

    def to_request(self, start: int, end: int) -> dict[str, Any]:
        chunk = self.pending[start:end]
        return {
            "context_before": [{"en": self.lines[i], "pt-BR": self.translated[i]} for i in self.pending[max(0, start - CHUNK_CONTEXT_BEFORE):start] if i in self.translated],
            "entries": {str(n): self.to_source(i) for n, i in enumerate(chunk, start=1)},
            "context_after": [self.to_source(i) for i in self.pending[end:end + CHUNK_CONTEXT_AFTER]],
        }

    def to_source(self, i: int) -> dict[str, tuple[str, ...]]:
        if self.second is None or i not in self.second.lines:
            return {"en": self.lines[i]}
        return {"en": self.lines[i], self.second.language.tag: self.second.lines[i]}

    def translate(self, llama: OpenAI, start: int, end: int, splits: int = 0) -> None:
        # build request
        chunk = self.pending[start:end]
        ids = [str(n) for n in range(1, len(chunk) + 1)]
        request = self.to_request(start, end)
        schema = {
            "type": "object",
            "properties": {
                id: {
                    "type": "object",
                    "properties": {
                        "en": {"type": "array", "items": {"type": "string"}, "minItems": len(self.lines[i]), "maxItems": len(self.lines[i])},
                        "pt-BR": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1, "maxLength": max(60, 3 * max(len(line) for line in self.lines[i]))},
                            "minItems": len(self.lines[i]),
                            "maxItems": len(self.lines[i]),
                        },
                        **({"why": {"type": "string", "minLength": 1}} if self.justify else {}),
                    },
                    "required": ["en", "pt-BR", *(["why"] if self.justify else [])],
                    "additionalProperties": False,
                }
                for id, i in zip(ids, chunk)
            },
            "required": ids,
            "additionalProperties": False,
        }

        # request and validate, retrying immediately on any problem
        problem = ""
        translated: dict[int, list[str]] = {}
        for attempt, temperature in enumerate(self.temperatures, start=1):
            reply = JsonReply.request(llama, schema_name="translations", schema=schema, system=self.prompt, user=json.dumps(request, ensure_ascii=False), temperature=temperature)
            problem = reply.find_problem(ids)
            if not problem and (bad := [id for id in ids if not isinstance(reply.data[id], dict) or not isinstance(reply.data[id].get("pt-BR"), list)]):
                problem = f"reply is not an object with pt-BR lines for ids {bad}"
            translated = {} if problem else {i: [" ".join(line.split()) if isinstance(line, str) else "" for line in reply.data[id]["pt-BR"]] for id, i in zip(ids, chunk)}
            if not problem and (bad := [id for id, i in zip(ids, chunk) if len(translated[i]) != len(self.lines[i])]):
                problem = f"line count changed for ids {bad}"

            self.trace.record({
                "entries": [self.entries[i].index for i in chunk],
                "attempt": attempt,
                "temperature": temperature,
                "request": request,
                **reply.to_record(),
                "problem": problem,
            })
            if not problem:
                self.translated.update(translated)
                return
            if attempt < len(self.temperatures):
                Action.RETRY.log(f"{problem} (attempt {attempt}/{len(self.temperatures)})")

        # still failing: split in halves up to the limit, then accept the last reply as is and record it in the trace
        if splits < LLM_SPLITS and len(chunk) > 1:
            Action.SPLIT.log(f"{len(chunk)} entries failed {len(self.temperatures)} attempts, splitting in halves")
            middle = (start + end) // 2
            self.translate(llama, start, middle, splits + 1)
            self.translate(llama, middle, end, splits + 1)
            return
        for i in chunk:
            self.translated[i] = translated[i] if len(translated.get(i, [])) == len(self.lines[i]) else self.lines[i]
        self.trace.record_accepted_with_problem([self.entries[i].index for i in chunk], problem)
        Action.ACCEPT.log(f"{len(chunk)} entries accepted with problem: {problem}")

    def compose(self) -> str:
        entries = []
        for i, entry in enumerate(self.entries):
            content = entry.content
            if i in self.translated:
                content = "\n".join(self.translated[i])
            entries.append(replace_text(entry, content))
        return srt.compose(entries, reindex=False)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translates English subtitles to Brazilian Portuguese; folders only English ones.")
    parser.add_argument("--temperatures", nargs="+", type=float, default=LLM_TEMPERATURES, help=f"temperature of each attempt of a chunk, in order (default: {' '.join(map(str, LLM_TEMPERATURES))})")
    parser.add_argument("--justify", action="store_true", help="ask for a short justification of each entry, written after its translation and kept only in the trace")
    parser.add_argument("--print-prompt", action="store_true", help="print the system prompt and the first chunk of each subtitle, even one already translated, without translating it")
    subs_src = find_subtitles(parser, formats=(SubtitleFormat.TEXT,), languages=(LANGUAGE_SOURCE,))
    arguments = parser.parse_args()
    temperatures = tuple(arguments.temperatures)
    llama = None if arguments.print_prompt else connect_llama()

    with Batch() as batch:
        for position, sub_src in enumerate(batch.walk(subs_src), start=1):
            # prepare files
            sub_dst = sub_src.to_translation(format=SubtitleFormat.TRANSLATION)
            if llama is None:
                batch.clear_screen()
                Action.PROMPT.log(sub_src.name)
            elif sub_dst.path.exists():
                Action.EXISTS.log(sub_dst.name)
                continue
            else:
                Action.TRANSLATE.log(sub_dst.name)

            with batch.attempt(sub_src.path):
                # parse entries and movie info
                sub_entries = parse_srt(sub_src.path)
                movie_info = MovieInfo.find(sub_src)
                if movie_info is None:
                    Action.NFO.log(f"{sub_src.nfo.name} not found, translating with the title only")
                movie_info_text = movie_info.to_prompt("\n".join(entry.content for entry in sub_entries)) if movie_info else ""

                # match second language
                second_subtitle = None
                for second_language in SecondLanguage:
                    candidate = SecondLanguageSubtitle.find(second_language, english=sub_src, english_entries=sub_entries)
                    if candidate is None:
                        Action.SKIP.log(f"no {second_language.label} text subtitle")
                        continue
                    Action.ALIGN.log(f"{candidate.file.name}: {len(candidate.lines)} of {candidate.entries_english} entries matched, offset {candidate.offset.total_seconds():.2f}s")
                    if not candidate.is_matched():
                        Action.DISCARD.log(f"{candidate.file.name} matches under {SECOND_LANGUAGE_MATCHED_MIN:.0%} of the entries")
                        continue
                    second_subtitle = candidate
                    break
                if second_subtitle is None:
                    Action.SKIP.log("no second language, translating from the English alone")
                second_parts = second_subtitle.to_prompt_parts(justify=arguments.justify) if second_subtitle else dict.fromkeys(SECOND_LANGUAGE_PLACEHOLDERS, "")

                # build translation
                example_reply = {id: {key: value for key, value in entry.items() if key != "why" or arguments.justify} for id, entry in LLM_EXAMPLE_REPLY.items()}
                movie_prompt = LLM_PROMPT.replace("{movie_info}", f"\n\n{movie_info_text}" if movie_info_text else "")
                movie_prompt = movie_prompt.replace("{justify_field}", LLM_JUSTIFY_FIELD if arguments.justify else "").replace("{example_reply}", json.dumps(example_reply, ensure_ascii=False))
                for placeholder, second_text in second_parts.items():
                    movie_prompt = movie_prompt.replace(placeholder, second_text)
                movie_prompt = movie_prompt.replace("{movie}", sub_src.movie_title)
                translation = SubtitleTranslation.from_entries(sub_entries, trace=Trace(file=sub_dst.to_trace()), temperatures=temperatures, prompt=movie_prompt, second=second_subtitle, justify=arguments.justify)
                chunk_starts = range(0, len(translation.pending), CHUNK_SIZE)

                # print the system prompt, with the movie title and info marked, and the first chunk, without translating
                if llama is None:
                    system = JSONHighlighter()(movie_prompt)
                    system.highlight_words([text for text in (movie_info_text, *(second_text.strip() for second_text in second_parts.values())) if text], style=Color.GREEN)
                    system.highlight_words([sub_src.movie_title], style=Color.CYAN)
                    log_heading(f"system: {sub_src.name}", Color.YELLOW)
                    console.print(system, soft_wrap=True)

                    # one entry per line, since the compact JSON the model reads is a single line
                    chunk_end = min(CHUNK_SIZE, len(translation.pending))
                    blocks = []
                    for key, value in translation.to_request(0, chunk_end).items():
                        opener, closer = ("{", "}") if isinstance(value, dict) else ("[", "]")
                        items = [f"{json.dumps(id)}: {json.dumps(item, ensure_ascii=False)}" for id, item in value.items()] if isinstance(value, dict) else [json.dumps(item, ensure_ascii=False) for item in value]
                        body = ",\n".join(f"    {item}" for item in items)
                        blocks.append(f'  "{key}": {opener}\n{body}\n  {closer}' if items else f'  "{key}": {opener}{closer}')
                    log_heading(f"user: chunk 1 of {len(chunk_starts)}, entries 1 to {chunk_end} of {len(translation.pending)}", Color.YELLOW)
                    console.print(JSONHighlighter()("{\n" + ",\n".join(blocks) + "\n}"), soft_wrap=True)

                    # ask before showing the next subtitle
                    if position == len(subs_src):
                        continue
                    console.print()
                    if not confirm(f"Next subtitle, {len(subs_src) - position} left: {subs_src[position].name}. Show it?", default=Answer.YES):
                        break
                    continue

                # translate in chunks, tracing every request and reply
                translation.trace.file.parent.mkdir(exist_ok=True)
                if movie_info_text:
                    translation.trace.record({"movie_info": movie_info_text})
                if second_subtitle:
                    translation.trace.record(second_subtitle.to_record())
                for chunk_start in track(chunk_starts):
                    translation.translate(llama, chunk_start, min(chunk_start + CHUNK_SIZE, len(translation.pending)))

                # write translated
                write_text_replacing(sub_dst.path, translation.compose())
