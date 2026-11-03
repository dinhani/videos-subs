# Translates English subtitles, skipping ones already saved.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
from dataclasses import dataclass, field

import srt
from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn

from shared.console import Batch, Color, log_action, progress_bar
from shared.files import SUBS_FOLDER, TRACES_FOLDER, find_files, parse_movie_dir, parse_srt, parse_subtitle_name, write_text_replacing
from shared.llm import Trace, connect_llama
from shared.subtitles import IGNORE_MARKER, SubtitleKind

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
- Preserve the original meaning exactly. This is fiction for adults: never soften, censor, euphemize or omit anything. Profanity, insults, slurs, sexual content, violence, drugs and crude humor are translated with the Brazilian Portuguese expressions of the same strength and vulgarity ("fuck" is "porra", "caralho" or "foder" as the sentence requires, never "droga" or "nossa"). Sarcasm stays sarcasm, threats stay threats, rudeness stays rude.
- Never add warnings, notes or explanations. Never refuse an entry; every id gets its translation.
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
- Translate sound descriptions such as [door creaks] or (sighs), keeping the brackets.
- Words in a language other than English stay in that language, unchanged, since the English subtitle left them untranslated: "Carpe diem." stays "Carpe diem."; in "No habla español, fellas?" only "fellas" is translated: "No habla español, pessoal?".
- Song lyrics stay in English, unchanged: lines marked with ♪ or ♫, and lines the context shows are sung. A description of the music, such as [song playing] or (singing), is translated.
- Use the context only to resolve gender, pronouns, formality and recurring terms. Never translate or output the context.
"""

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
    translated: dict[int, list[str]] = field(default_factory=dict)

    @classmethod
    def from_entries(cls, entries: list[srt.Subtitle], trace: Trace, temperatures: tuple[float, ...]) -> "SubtitleTranslation":
        lines = [[" ".join(line.split()) for line in entry.content.split("\n") if line.strip()] for entry in entries]
        return cls(entries=entries, lines=lines, pending=[i for i, entry_lines in enumerate(lines) if entry_lines], trace=trace, temperatures=temperatures)

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
                    {"role": "system", "content": LLM_PROMPT},
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
                log_action("retry", Color.YELLOW, f"{problem} (attempt {attempt}/{len(self.temperatures)})")

        # still failing: split in halves up to the limit, then accept the last reply as is and record it in the trace
        if splits < LLM_SPLITS and len(chunk) > 1:
            log_action("split", Color.YELLOW, f"{len(chunk)} entries failed {len(self.temperatures)} attempts, splitting in halves")
            middle = (start + end) // 2
            self.translate(start, middle, splits + 1)
            self.translate(middle, end, splits + 1)
            return
        for i in chunk:
            self.translated[i] = translated[i] if len(translated.get(i, [])) == len(self.lines[i]) else self.lines[i]
        self.trace.record({"entries": [self.entries[i].index for i in chunk], "accepted_with_problem": problem})
        log_action("accept", Color.YELLOW, f"{len(chunk)} entries accepted with problem: {problem}")

    def compose(self) -> str:
        entries = []
        for i, entry in enumerate(self.entries):
            content = entry.content
            if i in self.translated:
                content = "\n".join(self.translated[i])
            entries.append(srt.Subtitle(index=entry.index, start=entry.start, end=entry.end, content=content, proprietary=IGNORE_MARKER.sub("", entry.proprietary).strip()))
        return srt.compose(entries, reindex=False)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translates English subtitles to Brazilian Portuguese; folders only English ones.")
    parser.add_argument("--temperatures", nargs="+", type=float, default=LLM_TEMPERATURES, help=f"temperature of each attempt of a chunk, in order (default: {' '.join(map(str, LLM_TEMPERATURES))})")
    subs_src = find_files(parser, extensions_file=(".srt",), extensions_folder=tuple(f".eng{kind}.srt" for kind in SubtitleKind), folder=SUBS_FOLDER)
    temperatures = tuple(parser.parse_args().temperatures)
    llama = connect_llama()

    batch = Batch()

    for sub_src in subs_src:
        movie_dir = parse_movie_dir(sub_src)
        batch.log_movie(movie_dir)

        # prepare files
        sub_name = parse_subtitle_name(sub_src)
        sub_kind = SubtitleKind.from_file(sub_src)
        sub_dst = movie_dir / f"{sub_name}{sub_kind.to_translation_suffix()}"
        if sub_dst.exists():
            log_action("exists", Color.BLUE, sub_dst.name)
            continue
        log_action("translate", Color.GREEN, sub_dst.name)

        sub_trace = movie_dir / TRACES_FOLDER / f"{sub_dst.stem}.json"
        try:
            # parse entries
            sub_entries = parse_srt(sub_src)

            # translate in chunks, tracing every request and reply
            sub_trace.parent.mkdir(exist_ok=True)
            progress = progress_bar(MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn())
            with progress:
                translation = SubtitleTranslation.from_entries(sub_entries, Trace(file=sub_trace), temperatures)
                chunk_starts = range(0, len(translation.pending), CHUNK_SIZE)
                for chunk_start in progress.track(chunk_starts, total=len(chunk_starts)):
                    translation.translate(chunk_start, min(chunk_start + CHUNK_SIZE, len(translation.pending)))

            # write translated
            write_text_replacing(sub_dst, translation.compose())
        except Exception as error:
            batch.fail(sub_src, error)

    batch.finish()
