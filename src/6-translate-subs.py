# Translates English subtitles, skipping ones already saved.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import srt
from openai import OpenAI
from rich.highlighter import JSONHighlighter

from shared.alignment import AlignedSubtitle
from shared.console import Action, Answer, Batch, Color, confirm, console, log_heading, track
from shared.files import SubtitleFile, SubtitleFormat, find_subtitles, parse_srt, write_text_replacing
from shared.languages import WORD, SecondLanguage, parse_words
from shared.llm import JsonReply, Trace, connect_llama
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
- "context_before": the entries just before, each with its English lines ("en") and its accepted translation ("pt"). Keep the terms and forms of address these translations use.
- "entries": maps an id to one English entry, given as an array of its lines. These are the entries to translate.
- "context_after": the English lines of the entries just after.
Use both contexts to understand the scene, but never translate them or put them in the reply.{second_language}

# Reply

A JSON object that maps every id in "entries", and no other id, to an object with:
- "en": the English lines of that entry, copied unchanged.
- "pt": the translation, one Portuguese line per English line.

Each id gets its own translation: never merge, split, move, skip or refuse an entry. Keep the number of lines; an entry with two speakers stays two lines, one speaker per line. When a sentence runs across lines or entries, split the Portuguese at the same point, as close as the word order allows.

# Meaning

- Translate the meaning exactly. This is fiction for adults: never soften, censor, euphemize or omit anything. Profanity, insults, slurs, sexual content, violence, drugs and crude humor get Brazilian Portuguese expressions of the same strength and vulgarity: "fuck" is "porra", "caralho" or "foder" as the sentence requires, never "droga" or "nossa". Sarcasm stays sarcasm, threats stay threats, rudeness stays rude.
- Never mask a word that the English writes in full: "caralho", not "c*ralho". Never add warnings, notes or explanations.
- Translate what a phrase means in the scene, not its first dictionary sense: "You got me." after a joke is "Essa me pegou."; "Break a leg." before a show is "Boa sorte.".
- Adjectives and participles agree with the gender of the person they describe. Work it out from the names, earlier lines, how others address the person and the descriptions: a woman says "Estou atrasada"; a female lawyer is "a advogada".
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
- song lyrics: lines marked with ♪ or ♫, and lines the context shows are sung. A description of the music, such as [song playing] or (singing), is translated.

# Sound descriptions

Translate sound descriptions such as [door creaks] or (sighs), keeping the brackets and the letter case of the English. Name the exact sound the English describes, in the grammatical form of the English:
- a verb ending in -s becomes the verb in the third person of the present: "[FAUCET DRIPS]" is "[TORNEIRA PINGA]";
- a verb ending in -ing becomes the gerund: "[TYPEWRITER CLACKING]" is "[MÁQUINA DE ESCREVER BATENDO]";
- a noun stays a noun: "[FIREWORKS]" is "[FOGOS DE ARTIFÍCIO]", "[APPLAUSE]" is "[APLAUSOS]".
Use only words that exist in Portuguese: when no Portuguese verb names the sound, use a noun. Never invent a word or use a Spanish one.

# Example

Input:
{"context_before": [{"en": ["Where's Victor?"], "pt": ["Cadê o Victor?"]}], "entries": {"1": ["[FAUCET DRIPS]"], "2": ["- We need a boat.", "- Okay."], "3": ["I think he took", "the old highway north,"], "4": ["but he's gonna run out of gas."], "5": ["[TYPEWRITER CLACKING]"], "6": ["<i>Fuck, we're out of gas.</i>"], "7": ["Good afternoon, sir.", "May I see your boarding pass?"], "8": ["♪ Fly me to the moon ♪"]}, "context_after": [["[SIREN WAILING]"]]}

Reply:
{"1": {"en": ["[FAUCET DRIPS]"], "pt": ["[TORNEIRA PINGA]"]}, "2": {"en": ["- We need a boat.", "- Okay."], "pt": ["- A gente precisa de um barco.", "- Tá."]}, "3": {"en": ["I think he took", "the old highway north,"], "pt": ["Acho que ele pegou", "a rodovia velha pro norte,"]}, "4": {"en": ["but he's gonna run out of gas."], "pt": ["mas vai ficar sem gasolina."]}, "5": {"en": ["[TYPEWRITER CLACKING]"], "pt": ["[MÁQUINA DE ESCREVER BATENDO]"]}, "6": {"en": ["<i>Fuck, we're out of gas.</i>"], "pt": ["<i>Porra, a gasolina acabou.</i>"]}, "7": {"en": ["Good afternoon, sir.", "May I see your boarding pass?"], "pt": ["Boa tarde, senhor.", "Posso ver seu cartão de embarque?"]}, "8": {"en": ["♪ Fly me to the moon ♪"], "pt": ["♪ Fly me to the moon ♪"]}}
"""

SECOND_LANGUAGE_MATCHED_MIN = 0.9
SECOND_LANGUAGE_PROMPT = """

"second_language" maps ids in "entries" to the lines of the {language} subtitle of the same movie that are on screen at the same time. People translated it from the same dialogue, so it shows what the English means in the scene: the sense of an idiom or of a word with several meanings, whether "you" is one person or several, formal or casual address, gender and tense. Use it only to understand the English, and translate the English: when the {language} leaves something out or adds something, follow the English. Never copy {language} words, spellings or sentence structure; write Brazilian Portuguese.{forms}"""
SECOND_LANGUAGE_FORMS = {
    SecondLanguage.EUROPEAN_PORTUGUESE: 'European Portuguese forms are not Brazilian: never write "tu" and its verb forms, "consigo" for "com você", "estar a" with the infinitive for the gerund ("está a fazer" is "está fazendo"), or spellings such as "facto", "equipa" and "tentámos".',
}

NFO_CHARACTERS_MAX = 20
NFO_SHOW = "tvshow.nfo"
NFO_CREDIT_NOTES = re.compile(r"\s*\((?:voice|archive footage|archive sound)(?:,\s*(?:voice|archive footage|archive sound))*\)", re.IGNORECASE)
NFO_TITLES = frozenset({"Agent", "Captain", "Chief", "Corporal", "Detective", "Doctor", "Director", "General", "Lieutenant", "Major", "Officer", "Private", "Senator", "Sergeant", "Sheriff", "Young", "Old"})

# ------------------------------------------------------------------------------
# Movie info
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Character:
    role: str
    actor: str

    def is_named_in(self, names: set[str]) -> bool:
        return any(word.upper() in names for word in WORD.findall(self.role) if word[0].isupper() and len(word) >= 3 and word not in NFO_TITLES and not word.endswith(("'s", "’s")))


@dataclass(frozen=True)
class MovieInfo:
    plots: tuple[tuple[str, str], ...]
    characters: tuple[Character, ...]

    @classmethod
    def find(cls, sub: SubtitleFile) -> "MovieInfo | None":
        if not sub.nfo.exists():
            return None
        root = ElementTree.parse(sub.nfo).getroot()
        show_nfo = sub.movie_dir.parent / NFO_SHOW
        show = ElementTree.parse(show_nfo).getroot() if root.tag == "episodedetails" and show_nfo.exists() else None

        # an episode takes the plot of its season, or of its series when the season has none
        plots = [("Plot", root.findtext("plot"))]
        if show is not None:
            season = root.findtext("season", "")
            season_plot = next((element.text for element in show.iter("seasonplot") if element.get("number") == season and element.text and element.text.strip()), None)
            plots = [(f"Season {season}", season_plot) if season_plot else ("Series", show.findtext("plot")), ("Episode", root.findtext("plot"))]

        # credited roles without credit notes, the first billed of each role
        characters: dict[str, Character] = {}
        for actor in root.findall("actor") or (show.findall("actor") if show is not None else []):
            role = " ".join(NFO_CREDIT_NOTES.sub("", actor.findtext("role", "")).split())
            name = " ".join(actor.findtext("name", "").split())
            if role and name and "uncredited" not in role.lower():
                characters.setdefault(role, Character(role=role, actor=name))
        return cls(plots=tuple((label, " ".join(text.split())) for label, text in plots if text and text.strip()), characters=tuple(characters.values()))

    def to_prompt(self, english: str) -> str:
        names = {word.text.upper() for word in parse_words(english) if word.name and not word.text.isupper()}
        characters = [character for character in self.characters if character.is_named_in(names)][:NFO_CHARACTERS_MAX]
        lines = [f"{label}: {text}" for label, text in self.plots]
        if characters:
            lines.append("Characters named in the subtitles, each with the actor who plays them:")
            lines += [f"- {character.role} ({character.actor})" for character in characters]
        return "\n".join(lines)

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
        lines = {i: matched for i in english_texts if (matched := tuple(line for other in aligned.find_overlapping(english_entries[i]) for line in parse_lines(other)))}
        return cls(language=language, file=file, offset=aligned.offset, lines=lines, entries_english=len(english_texts))

    def is_matched(self) -> bool:
        return len(self.lines) >= SECOND_LANGUAGE_MATCHED_MIN * self.entries_english

    def to_prompt(self) -> str:
        forms = SECOND_LANGUAGE_FORMS.get(self.language)
        return SECOND_LANGUAGE_PROMPT.replace("{language}", self.language.label).replace("{forms}", f" {forms}" if forms else "")

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
    second_lines: dict[int, tuple[str, ...]]
    translated: dict[int, Sequence[str]] = field(default_factory=dict)

    @classmethod
    def from_entries(cls, entries: list[srt.Subtitle], trace: Trace, temperatures: tuple[float, ...], prompt: str, second_lines: dict[int, tuple[str, ...]]) -> "SubtitleTranslation":
        lines = [parse_lines(entry) for entry in entries]
        return cls(entries=entries, lines=lines, pending=[i for i, entry_lines in enumerate(lines) if entry_lines], trace=trace, temperatures=temperatures, prompt=prompt, second_lines=second_lines)

    def to_request(self, start: int, end: int) -> dict[str, Any]:
        chunk = self.pending[start:end]
        return {
            "context_before": [{"en": self.lines[i], "pt": self.translated[i]} for i in self.pending[max(0, start - CHUNK_CONTEXT_BEFORE):start] if i in self.translated],
            "entries": {str(n): self.lines[i] for n, i in enumerate(chunk, start=1)},
            **({"second_language": {str(n): self.second_lines[i] for n, i in enumerate(chunk, start=1) if i in self.second_lines}} if self.second_lines else {}),
            "context_after": [self.lines[i] for i in self.pending[end:end + CHUNK_CONTEXT_AFTER]],
        }

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
                        "pt": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1, "maxLength": max(60, 3 * max(len(line) for line in self.lines[i]))},
                            "minItems": len(self.lines[i]),
                            "maxItems": len(self.lines[i]),
                        },
                    },
                    "required": ["en", "pt"],
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
            if not problem and (bad := [id for id in ids if not isinstance(reply.data[id], dict) or not isinstance(reply.data[id].get("pt"), list)]):
                problem = f"reply is not an object with pt lines for ids {bad}"
            translated = {} if problem else {i: [" ".join(line.split()) if isinstance(line, str) else "" for line in reply.data[id]["pt"]] for id, i in zip(ids, chunk)}
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
    parser.add_argument("--prompt", type=Path, help="text file with the system prompt to use instead of LLM_PROMPT; {movie} in it is replaced by the title of the movie or episode, as in LLM_PROMPT")
    parser.add_argument("--second-languages", nargs="*", choices=[language.code for language in SecondLanguage], default=[language.code for language in SecondLanguage], help=f"language codes of text subtitles of the same video, in its _subs folder, in order of preference; the first one that matches enough entries has its lines on screen with each English entry sent with it; with no codes, or when none matches, the English is sent alone (default: {' '.join(language.code for language in SecondLanguage)})")
    parser.add_argument("--print-prompt", action="store_true", help="print the system prompt and the first chunk of each subtitle, even one already translated, without translating it")
    subs_src = find_subtitles(parser, formats=(SubtitleFormat.TEXT,), languages=(LANGUAGE_SOURCE,))
    arguments = parser.parse_args()
    temperatures = tuple(arguments.temperatures)
    prompt = arguments.prompt.read_text(encoding="utf-8") if arguments.prompt else LLM_PROMPT
    second_languages = tuple(next(language for language in SecondLanguage if language.code == code) for code in arguments.second_languages)
    if second_languages and "{second_language}" not in prompt:
        parser.error("--second-languages needs {second_language} in the prompt; pass --second-languages with no codes to send the English alone")
    llama = None if arguments.print_prompt else connect_llama()

    with Batch() as batch:
        for position, sub_src in enumerate(batch.walk(subs_src), start=1):
            # prepare files
            sub_dst = sub_src.to_translation()
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
                for second_language in second_languages:
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
                if second_languages and second_subtitle is None:
                    Action.SKIP.log("no second language, translating from the English alone")
                second_prompt = second_subtitle.to_prompt() if second_subtitle else ""

                # build translation
                movie_prompt = prompt.replace("{movie_info}", f"\n\n{movie_info_text}" if movie_info_text else "").replace("{second_language}", second_prompt).replace("{movie}", sub_src.movie_title)
                translation = SubtitleTranslation.from_entries(sub_entries, trace=Trace(file=sub_dst.to_trace()), temperatures=temperatures, prompt=movie_prompt, second_lines=second_subtitle.lines if second_subtitle else {})
                chunk_starts = range(0, len(translation.pending), CHUNK_SIZE)

                # print the system prompt, with the movie title and info marked, and the first chunk, without translating
                if llama is None:
                    system = JSONHighlighter()(movie_prompt)
                    system.highlight_words([text for text in (movie_info_text, second_prompt.strip()) if text], style=Color.GREEN)
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
