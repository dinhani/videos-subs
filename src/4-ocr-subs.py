# Transcribes image subtitles in the wanted languages to text subtitles with a vision LLM, skipping ones already transcribed.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import itertools
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import srt
from openai import BaseModel, OpenAI
from openai.types.chat import ChatCompletionContentPartParam

from shared.console import Action, Batch, track
from shared.files import SubtitleFormat, find_subtitles, write_text_replacing
from shared.image_subtitles import SubtitleEntry, parse_image_subtitle
from shared.languages import LANGUAGES_OCR
from shared.llm import LLM_PROPS_URL, JsonReply, Trace, connect_llama
from shared.subtitles import OCR_FAILED_TEXT

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
CHUNK_SIZE = 10

ITALIC_TAG_WITH_ATTRIBUTES = re.compile(r"<i\s[^<>]*>")

LLM_TEMPERATURES = (0.0, 0.5, 1.0)
LLM_ATTEMPTS = len(LLM_TEMPERATURES)
LLM_MAX_TOKENS = 4096
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
    llama: OpenAI
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
        for attempt, temperature in enumerate(LLM_TEMPERATURES, start=1):
            reply = JsonReply.request(self.llama, schema_name="transcriptions", schema=schema, system=LLM_PROMPT, user=content, temperature=temperature, max_tokens=LLM_MAX_TOKENS)
            problem = reply.find_problem(ids)
            if not problem and (bad := [id for id in ids if not isinstance(reply.data[id], list) or not all(isinstance(line, dict) and isinstance(line.get("text"), str) and isinstance(line.get("italic"), bool) for line in reply.data[id])]):
                problem = f"reply is not a list of lines with text and italic for ids {bad}"
            transcribed: dict[int, list[str]] = {} if problem else {i: [f"<i>{text.replace('<i>', '').replace('</i>', '')}</i>" if line["italic"] else text for line in reply.data[id] if (text := ITALIC_TAG_WITH_ATTRIBUTES.sub("<i>", " ".join(line["text"].split())).replace("</em>", "</i>"))] for id, i in zip(ids, chunk)}
            if not problem and (bad := [id for id, i in zip(ids, chunk) if not transcribed[i]]):
                problem = f"no text for ids {bad}"

            self.trace.record({
                "entries": [i + 1 for i in chunk],
                "attempt": attempt,
                "temperature": temperature,
                **reply.to_record(),
                "problem": problem,
            })
            if not problem:
                self.transcribed.update(transcribed)
                return
            if attempt < LLM_ATTEMPTS:
                Action.RETRY.log(f"{entries_label}: {problem} (attempt {attempt}/{LLM_ATTEMPTS})")

        # still failing: split in halves down to single entries, then write the entry as failed
        if len(chunk) > 1:
            Action.SPLIT.log(f"{entries_label}: failed {LLM_ATTEMPTS} attempts, splitting in halves")
            middle = len(chunk) // 2
            self.transcribe(chunk[:middle])
            self.transcribe(chunk[middle:])
            return
        self.transcribed[chunk[0]] = [OCR_FAILED_TEXT]
        self.trace.record_accepted_with_problem([chunk[0] + 1], problem)
        Action.ACCEPT.log(f"entry {chunk[0] + 1} written as {OCR_FAILED_TEXT}: {problem}")

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
    subs = find_subtitles(argparse.ArgumentParser(description="Transcribes image subtitles to text subtitles with a vision LLM; folders only English and second-language ones."), formats=(SubtitleFormat.PGS, SubtitleFormat.VOBSUB), languages=LANGUAGES_OCR)
    llama = connect_llama()
    llama_slots = llama.get(LLM_PROPS_URL, cast_to=LlamaProps).total_slots

    with Batch() as batch:
        for sub in batch.walk(subs):
            # skip subs already transcribed
            sub_dst = sub.to_text()
            if sub_dst.path.exists():
                Action.EXISTS.log(sub_dst.name)
                continue
            Action.OCR.log(sub_dst.name)

            with batch.attempt(sub.path):
                # read entries
                sub_entries = parse_image_subtitle(sub.path)
                if not sub_entries:
                    Action.EMPTY.log(sub.name)
                    continue

                # transcribe each distinct image once, in chunks, one per llama slot at a time, tracing every reply
                sub_trace = sub.to_trace()
                sub_trace.parent.mkdir(exist_ok=True)
                transcription = SubtitleTranscription(entries=sub_entries, trace=Trace(file=sub_trace), llama=llama)
                entries_distinct: dict[int, int] = {}
                for i, entry in enumerate(sub_entries):
                    entries_distinct.setdefault(entry.image_key, i)
                chunks = list(itertools.batched(entries_distinct.values(), CHUNK_SIZE))
                with ThreadPoolExecutor(max_workers=llama_slots) as executor:
                    for _ in track(executor.map(transcription.transcribe, chunks), total=len(chunks)):
                        pass

                # write transcribed
                write_text_replacing(sub_dst.path, transcription.compose())
