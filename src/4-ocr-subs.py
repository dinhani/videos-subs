# Transcribes image subtitles in the wanted languages to text subtitles with a vision LLM, skipping ones already transcribed.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import itertools
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import srt
from openai import BaseModel
from openai.types.chat import ChatCompletionContentPartParam
from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn

from shared.console import Batch, Color, log_action, progress_bar
from shared.files import IMAGE_EXTENSIONS, SUBS_FOLDER, TRACES_FOLDER, find_files, parse_movie_dir, write_text_replacing
from shared.image_subtitles import SubtitleEntry, parse_image_subtitle
from shared.llm import LLM_PROPS_URL, Trace, connect_llama
from shared.subtitles import SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
LANGUAGES = ("eng",)

CHUNK_SIZE = 10

ENTRY_FAILED_TEXT = "[OCR failed]"
ITALIC_TAG_WITH_ATTRIBUTES = re.compile(r"<i\s[^<>]*>")

LLM_TEMPERATURES = (0.0, 0.5, 1.0)
LLM_ATTEMPTS = len(LLM_TEMPERATURES)
LLM_PROMPT = """\
You transcribe subtitle images from movies and TV shows.

The user message has numbered images. Each image is one subtitle as shown on screen: light text with a dark outline on a gray background.

Reply with a JSON object that maps every image id to an array with the text lines of that image, top to bottom, one element per line on screen. Each line is an object with "italic" and "text". Use exactly the given ids and nothing else.

Transcription rules:
- Copy the text exactly as written, in its original language. Never translate, correct, complete or summarize it.
- Keep capitalization, punctuation, digits and symbols such as ♪ ♫ … - – — " ¿ ¡ [ ] ( ) as they appear.
- A dialogue dash at the start of a line stays at the start of that line.
- Set "italic" for every line by looking at its letters: true when they are slanted (italic type), false when they are upright. Judge each line on its own; lines in the same image can differ.
- When only some words of a line are slanted, set "italic" to false and wrap those words in <i> and </i>. Never mark italics with * or _.
- Never add notes, descriptions of the image or explanations.
"""

# ------------------------------------------------------------------------------
# OCR
# ------------------------------------------------------------------------------
class LlamaProps(BaseModel):
    total_slots: int


@dataclass
class SubtitleTranscription:
    entries: list[SubtitleEntry]
    trace: Trace
    transcribed: dict[int, list[str]] = field(default_factory=dict)

    def transcribe(self, chunk: tuple[int, ...]) -> None:
        # build request
        entries_label = f"entries {chunk[0] + 1}-{chunk[-1] + 1}"
        ids = [str(n) for n in range(1, len(chunk) + 1)]
        content: list[ChatCompletionContentPartParam] = []
        for id, i in zip(ids, chunk):
            content += [{"type": "text", "text": f"Image {id}:"}, {"type": "image_url", "image_url": {"url": self.entries[i].to_data_url()}}]
        schema = {
            "type": "object",
            "properties": {
                id: {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"italic": {"type": "boolean"}, "text": {"type": "string", "minLength": 1, "maxLength": 200}},
                        "required": ["italic", "text"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 6,
                }
                for id in ids
            },
            "required": ids,
            "additionalProperties": False,
        }

        # request and validate, retrying immediately on any problem
        problem = ""
        transcribed: dict[int, list[str]] = {}
        for attempt, temperature in enumerate(LLM_TEMPERATURES, start=1):
            problem = ""
            transcribed = {}
            resp = llama.chat.completions.create(
                model="local",
                temperature=temperature,
                max_tokens=4096,
                response_format={"type": "json_schema", "json_schema": {"name": "transcriptions", "strict": True, "schema": schema}},
                messages=[
                    {"role": "system", "content": LLM_PROMPT},
                    {"role": "user", "content": content},
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
            elif bad := [id for id in ids if not isinstance(reply[id], list) or not all(isinstance(line, dict) and isinstance(line.get("text"), str) and isinstance(line.get("italic"), bool) for line in reply[id])]:
                problem = f"reply is not a list of lines with text and italic for ids {bad}"
            else:
                transcribed = {i: [f"<i>{text.replace('<i>', '').replace('</i>', '')}</i>" if line["italic"] else text for line in reply[id] if (text := ITALIC_TAG_WITH_ATTRIBUTES.sub("<i>", " ".join(line["text"].split())).replace("</em>", "</i>"))] for id, i in zip(ids, chunk)}
                if bad := [id for id, i in zip(ids, chunk) if not transcribed[i]]:
                    problem = f"no text for ids {bad}"

            self.trace.record({
                "entries": [i + 1 for i in chunk],
                "attempt": attempt,
                "temperature": temperature,
                "reply": reply if reply is not None else choice.message.content,
                "finish_reason": choice.finish_reason,
                "problem": problem,
            })
            if not problem:
                self.transcribed.update(transcribed)
                return
            if attempt < LLM_ATTEMPTS:
                log_action("retry", Color.YELLOW, f"{entries_label}: {problem} (attempt {attempt}/{LLM_ATTEMPTS})")

        # still failing: split in halves down to single entries, then write the entry as failed
        if len(chunk) > 1:
            log_action("split", Color.YELLOW, f"{entries_label}: failed {LLM_ATTEMPTS} attempts, splitting in halves")
            middle = len(chunk) // 2
            self.transcribe(chunk[:middle])
            self.transcribe(chunk[middle:])
            return
        self.transcribed[chunk[0]] = [ENTRY_FAILED_TEXT]
        self.trace.record({"entries": [chunk[0] + 1], "accepted_with_problem": problem})
        log_action("accept", Color.YELLOW, f"entry {chunk[0] + 1} written as {ENTRY_FAILED_TEXT}: {problem}")

    def compose(self) -> str:
        transcribed_by_image = {self.entries[i].image_key: lines for i, lines in self.transcribed.items()}
        subtitles = [
            srt.Subtitle(index=i + 1, start=entry.start, end=entry.end, content=entry.position + "\n".join(transcribed_by_image[entry.image_key]))
            for i, entry in enumerate(self.entries)
        ]
        return srt.compose(subtitles)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_files(argparse.ArgumentParser(description="Transcribes image subtitles to text subtitles with a vision LLM; folders only in the wanted languages."), extensions_file=IMAGE_EXTENSIONS, extensions_folder=tuple(f".{language}{kind}{extension}" for language in LANGUAGES for kind in SubtitleKind for extension in IMAGE_EXTENSIONS), folder=SUBS_FOLDER)
    llama = connect_llama()
    llama_slots = llama.get(LLM_PROPS_URL, cast_to=LlamaProps).total_slots
    batch = Batch()

    for sub in subs:
        movie_dir = parse_movie_dir(sub)
        batch.log_movie(movie_dir)

        # skip subs already transcribed
        sub_dst = sub.with_suffix(".srt")
        if sub_dst.exists():
            log_action("exists", Color.BLUE, sub_dst.name)
            continue
        log_action("ocr", Color.GREEN, sub_dst.name)

        sub_trace = movie_dir / TRACES_FOLDER / f"{sub.stem}.ocr.json"
        try:
            # read entries
            sub_entries = parse_image_subtitle(sub)
            if not sub_entries:
                log_action("empty", Color.YELLOW, sub.name)
                continue

            # transcribe each distinct image once, in chunks, one per llama slot at a time, tracing every reply
            sub_trace.parent.mkdir(exist_ok=True)
            transcription = SubtitleTranscription(entries=sub_entries, trace=Trace(file=sub_trace))
            entries_distinct: dict[int, int] = {}
            for i, entry in enumerate(sub_entries):
                entries_distinct.setdefault(entry.image_key, i)
            with progress_bar(MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn()) as progress, ThreadPoolExecutor(max_workers=llama_slots) as executor:
                chunks = list(itertools.batched(entries_distinct.values(), CHUNK_SIZE))
                for _ in progress.track(executor.map(transcription.transcribe, chunks), total=len(chunks)):
                    pass

            # write transcribed
            write_text_replacing(sub_dst, transcription.compose())
        except Exception as error:
            batch.fail(sub, error)

    batch.finish()
