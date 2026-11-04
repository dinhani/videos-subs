# Translates English subtitles, skipping ones already saved.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

import srt

from shared.console import Action, Batch, track
from shared.files import SubtitleFile, SubtitleFormat, find_subtitles, parse_srt, write_text_replacing
from shared.languages import WORD, parse_words
from shared.llm import Trace, connect_llama
from shared.subtitles import LANGUAGE_SOURCE, replace_text

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
The subtitles are from {movie}. Use what you know about it and its characters to resolve gender, forms of address and recurring terms.{movie_info}

The input is a JSON object:
- "lines": maps an id to one English subtitle entry, given as an array of its lines. Translate every entry.
- "context_before": earlier entries with their accepted translations. Read-only.
- "context_after": upcoming English entries. Read-only.

Reply with a JSON object that maps every id in "lines" to an object with:
- "en": the English lines of that id as you read them, with any junk from the source removed.
- "pt": the translated lines, one array element per English line, same number of elements.
Use exactly the same ids and nothing else.

Translation rules:
- Write natural spoken Brazilian Portuguese dialogue. Address people as the scene calls for: "você" in everyday speech, not "tu"; "o senhor" and "a senhora" for formal address, such as to a superior, an elder or a client; "Vossa Majestade" and "Vossa Alteza" for royalty. Keep the same form between the same characters.
- Write Brazilian Portuguese as people speak it in Brazil today, not as it is written in books. Match the register of the English line: casual stays casual, formal stays formal.
  - Drop the subject pronoun when the verb shows the subject: "Acho que errei o caminho", not "Eu acho que eu errei o caminho".
  - In casual speech, use "a gente" for "we", "pra" for "para", "tá" for "está", "cadê" for "where is" and the spoken imperative: "A gente precisa de um barco", "Espera", "Olha", "Vem", not "Nós precisamos de um barco", "Espere", "Olhe", "Venha".
  - Use "vai" with the infinitive for the future, not the written future tense: "ela vai adorar", not "ela adorará".
  - Use the words people say, not formal ones: "Consegui os ingressos", not "Obtive os ingressos"; "Tá" or "Tá bom" for a casual "Okay".
  - Keep each line short and direct, since a subtitle is read in a few seconds: "Give me that." is "Me dá isso.", not "Você poderia me dar isso?".
  - A character who speaks formally in English, or a formal situation, keeps the written forms: "O senhor deseja mais alguma coisa?".
- Preserve the original meaning exactly. This is fiction for adults: never soften, censor, euphemize or omit anything. Profanity, insults, slurs, sexual content, violence, drugs and crude humor are translated with the Brazilian Portuguese expressions of the same strength and vulgarity ("fuck" is "porra", "caralho" or "foder" as the sentence requires, never "droga" or "nossa"). Sarcasm stays sarcasm, threats stay threats, rudeness stays rude.
- Never add warnings, notes or explanations. Never refuse an entry; every id gets its translation.
- Never mask a word with symbols: "caralho", not "c*ralho"; "merda", not "m*rda".
- Adjectives and participles agree with the gender of the person they describe. Work out the gender from the context: names, earlier lines, how others address them, the descriptions. A woman says "Estou atrasada"; a female lawyer is "a advogada".
- Translate what each word means in the scene, not its first dictionary sense: "You got me." after a joke is "Essa me pegou."; "Break a leg." before a show is "Boa sorte.".
- Translate, do not localize. Units, currencies, measurements, dates, times and cultural references stay as in the original: "30 miles" is "30 milhas", never "48 quilômetros"; "6 feet" is "6 pés"; "90 degrees" is "90 graus". Translate the unit word; the digits stay the same.
- One translation per id. Never merge, split, move or skip entries. When a sentence continues into the next id, translate each part in its own id, split at the same point.
- Keep unchanged, in the same position relative to the text:
  - names of people, places, brands and titles;
  - digits;
  - these symbols: ♪ ♫ … - – — " ¿ ¡ # * [ ] ( );
  - these tag pairs, the opening tag before the text and the closing tag after it, exactly as in the source: <i>text</i>, <b>text</b>, <u>text</u>, <font color="#FFFFFF">text</font>, <font face="Arial">text</font>; if the source has <i> but no </i>, keep that too;
  - these position tags at the start of the line: {\\an1} {\\an2} {\\an3} {\\an4} {\\an5} {\\an6} {\\an7} {\\an8} {\\an9};
  - any other <...>, </...> or {\\...} tag not listed above, where it is;
  - a leading "- " dialogue dash on a line stays a leading "- " on the same line.
- Keep the same number of lines inside an entry. Two-speaker entries stay two lines, one speaker per line.
- Translate sound descriptions such as [door creaks] or (sighs), keeping the brackets and the letter case of the English. Name the sound the English describes, with the Portuguese word for that exact sound. Keep the grammatical form of the English:
  - a verb ending in -s becomes the verb in the third person of the present: "[FAUCET DRIPS]" is "[TORNEIRA PINGA]";
  - a verb ending in -ing becomes the gerund: "[TYPEWRITER CLACKING]" is "[MÁQUINA DE ESCREVER BATENDO]";
  - a noun stays a noun: "[FIREWORKS]" is "[FOGOS DE ARTIFÍCIO]", "[APPLAUSE]" is "[APLAUSOS]".
  Use only words that exist in Portuguese: when no Portuguese verb names the sound, use a noun for it. Never invent a word or use a Spanish one.
- Words in a language other than English stay in that language, unchanged, since the English subtitle left them untranslated: "Carpe diem." stays "Carpe diem."; in "No habla español, fellas?" only "fellas" is translated: "No habla español, pessoal?".
- Song lyrics stay in English, unchanged: lines marked with ♪ or ♫, and lines the context shows are sung. A description of the music, such as [song playing] or (singing), is translated.
- Use the context only to resolve gender, pronouns, formality and recurring terms. Never translate or output the context.

Example. For this input:
{"context_before": [{"en": ["Where's Victor?"], "pt": ["Cadê o Victor?"]}], "lines": {"1": ["[FAUCET DRIPS]"], "2": ["- We need a boat.", "- Okay."], "3": ["I think he took", "the old highway north,"], "4": ["but he's gonna run out of gas."], "5": ["[TYPEWRITER CLACKING]"], "6": ["<i>Fuck, we're out of gas.</i>"], "7": ["Good afternoon, sir.", "May I see your boarding pass?"], "8": ["♪ Fly me to the moon ♪"]}, "context_after": [["[SIREN WAILING]"]]}
the reply is:
{"1": {"en": ["[FAUCET DRIPS]"], "pt": ["[TORNEIRA PINGA]"]}, "2": {"en": ["- We need a boat.", "- Okay."], "pt": ["- A gente precisa de um barco.", "- Tá."]}, "3": {"en": ["I think he took", "the old highway north,"], "pt": ["Acho que ele pegou", "a rodovia velha pro norte,"]}, "4": {"en": ["but he's gonna run out of gas."], "pt": ["mas vai ficar sem gasolina."]}, "5": {"en": ["[TYPEWRITER CLACKING]"], "pt": ["[MÁQUINA DE ESCREVER BATENDO]"]}, "6": {"en": ["<i>Fuck, we're out of gas.</i>"], "pt": ["<i>Porra, a gasolina acabou.</i>"]}, "7": {"en": ["Good afternoon, sir.", "May I see your boarding pass?"], "pt": ["Boa tarde, senhor.", "Posso ver seu cartão de embarque?"]}, "8": {"en": ["♪ Fly me to the moon ♪"], "pt": ["♪ Fly me to the moon ♪"]}}
"""

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
        lines = ["From the metadata of {movie}:", *(f"{label}: {text}" for label, text in self.plots)]
        if characters:
            lines.append("Characters named in the subtitles, each with the actor who plays them:")
            lines += [f"- {character.role} ({character.actor})" for character in characters]
        return "\n".join(lines)

# ------------------------------------------------------------------------------
# Translation
# ------------------------------------------------------------------------------
@dataclass
class SubtitleTranslation:
    entries: list[srt.Subtitle]
    lines: list[list[str]]
    pending: list[int]
    trace: Trace
    temperatures: tuple[float, ...]
    prompt: str
    translated: dict[int, list[str]] = field(default_factory=dict)

    @classmethod
    def from_entries(cls, entries: list[srt.Subtitle], trace: Trace, temperatures: tuple[float, ...], prompt: str) -> "SubtitleTranslation":
        lines = [[" ".join(line.split()) for line in entry.content.split("\n") if line.strip()] for entry in entries]
        return cls(entries=entries, lines=lines, pending=[i for i, entry_lines in enumerate(lines) if entry_lines], trace=trace, temperatures=temperatures, prompt=prompt)

    def translate(self, start: int, end: int, splits: int = 0) -> None:
        # build request
        chunk = self.pending[start:end]
        ids = [str(n) for n in range(1, len(chunk) + 1)]
        request = {
            "context_before": [{"en": self.lines[i], "pt": self.translated[i]} for i in self.pending[max(0, start - CHUNK_CONTEXT_BEFORE):start] if i in self.translated],
            "lines": {id: self.lines[i] for id, i in zip(ids, chunk)},
            "context_after": [self.lines[i] for i in self.pending[end:end + CHUNK_CONTEXT_AFTER]],
        }
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
            problem = ""
            translated = {}
            resp = llama.chat.completions.create(
                model="local",
                temperature=temperature,
                response_format={"type": "json_schema", "json_schema": {"name": "translations", "strict": True, "schema": schema}},
                messages=[
                    {"role": "system", "content": self.prompt},
                    {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
                ],
            )
            choice = resp.choices[0]
            try:
                reply = json.loads(choice.message.content or "")
            except json.JSONDecodeError:
                reply = None

            if choice.finish_reason == "length":
                problem = "reply cut off by token limit"
            elif not isinstance(reply, dict):
                problem = "reply is not a JSON object"
            elif set(reply) != set(ids):
                problem = f"ids mismatch: missing {sorted(set(ids) - set(reply), key=int)}, extra {sorted(set(reply) - set(ids))}"
            elif bad := [id for id in ids if not isinstance(reply[id], dict) or not isinstance(reply[id].get("pt"), list)]:
                problem = f"reply is not an object with pt lines for ids {bad}"
            else:
                translated = {i: [" ".join(line.split()) if isinstance(line, str) else "" for line in reply[id]["pt"]] for id, i in zip(ids, chunk)}
                if bad := [id for id, i in zip(ids, chunk) if len(translated[i]) != len(self.lines[i])]:
                    problem = f"line count changed for ids {bad}"

            self.trace.record({
                "entries": [self.entries[i].index for i in chunk],
                "attempt": attempt,
                "temperature": temperature,
                "request": request,
                "reply": reply if reply is not None else choice.message.content,
                **({"reasoning": reasoning} if (reasoning := getattr(choice.message, "reasoning_content", None)) else {}),
                "finish_reason": choice.finish_reason,
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
            self.translate(start, middle, splits + 1)
            self.translate(middle, end, splits + 1)
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
    subs_src = find_subtitles(parser, formats=(SubtitleFormat.TEXT,), language=LANGUAGE_SOURCE)
    arguments = parser.parse_args()
    temperatures = tuple(arguments.temperatures)
    prompt = arguments.prompt.read_text(encoding="utf-8") if arguments.prompt else LLM_PROMPT
    llama = connect_llama()

    with Batch() as batch:
        for sub_src in batch.walk(subs_src):
            # prepare files
            sub_dst = sub_src.to_translation()
            if sub_dst.path.exists():
                Action.EXISTS.log(sub_dst.name)
                continue
            Action.TRANSLATE.log(sub_dst.name)

            with batch.attempt(sub_src.path):
                # parse entries and movie info
                sub_entries = parse_srt(sub_src.path)
                movie_info = MovieInfo.find(sub_src)
                if movie_info is None:
                    Action.NFO.log(f"{sub_src.nfo.name} not found, translating with the title only")
                movie_info_text = movie_info.to_prompt("\n".join(entry.content for entry in sub_entries)).replace("{movie}", sub_src.movie_title) if movie_info else ""
                movie_prompt = prompt.replace("{movie_info}", f"\n\n{movie_info_text}" if movie_info_text else "").replace("{movie}", sub_src.movie_title)

                # translate in chunks, tracing every request and reply
                sub_trace = sub_dst.to_trace()
                sub_trace.parent.mkdir(exist_ok=True)
                trace = Trace(file=sub_trace)
                if movie_info_text:
                    trace.record({"movie_info": movie_info_text})
                translation = SubtitleTranslation.from_entries(sub_entries, trace, temperatures, movie_prompt)
                chunk_starts = range(0, len(translation.pending), CHUNK_SIZE)
                for chunk_start in track(chunk_starts):
                    translation.translate(chunk_start, min(chunk_start + CHUNK_SIZE, len(translation.pending)))

                # write translated
                write_text_replacing(sub_dst.path, translation.compose())
