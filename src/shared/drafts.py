import json
import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import srt

from shared.alignment import AlignedSubtitle
from shared.files import SubtitleFile, parse_srt
from shared.subtitles import FORMATTING_TAGS, LANGUAGE_UNDETECTED, parse_lines, replace_text

WORK_ROOT = Path(tempfile.gettempdir()) / "translate-claude"
WORK_ENGLISH = "english.txt"
WORK_REFERENCES = "references"
WORK_REFERENCES_INDEX = "references.md"
WORK_TRANSLATION = "translation"
WORK_NOTES = "notes.md"
WORK_SESSION = "session.json"

ENTRY_HEADER = re.compile(r"^#(\d+)$")
DIGITS = re.compile(r"\d+")


def parse_blocks(text: str) -> list[tuple[int, tuple[str, ...]]]:
    blocks: list[tuple[int, list[str]]] = []
    for line in text.splitlines():
        if header := ENTRY_HEADER.match(line.strip()):
            blocks.append((int(header[1]), []))
        elif line.strip() and blocks:
            blocks[-1][1].append(" ".join(line.split()))
    return [(number, tuple(lines)) for number, lines in blocks]


def format_blocks(blocks: Iterable[tuple[int, tuple[str, ...]]]) -> str:
    return "\n\n".join("\n".join((f"#{number}", *lines)) for number, lines in blocks) + "\n"


@dataclass(frozen=True)
class Source:
    english: dict[int, tuple[str, ...]]

    @classmethod
    def from_entries(cls, entries: list[srt.Subtitle]) -> "Source":
        return cls(english={number: lines for number, entry in enumerate(entries, start=1) if (lines := parse_lines(entry))})

    @classmethod
    def from_text(cls, text: str) -> "Source":
        return cls(english=dict(parse_blocks(text)))

    def to_text(self) -> str:
        return format_blocks(self.english.items())


@dataclass(frozen=True)
class Reference:
    file: SubtitleFile
    lines: dict[int, tuple[str, ...]]
    entries_english: int

    @classmethod
    def from_file(cls, file: SubtitleFile, english_entries: list[srt.Subtitle]) -> "Reference":
        aligned = AlignedSubtitle.from_entries(parse_srt(file.path), english=english_entries)
        lines = {i + 1: matched for i, matched in aligned.find_lines(english_entries).items()}
        return cls(file=file, lines=lines, entries_english=sum(1 for entry in english_entries if parse_lines(entry)))

    @property
    def name(self) -> str:
        return f"{self.file.language or LANGUAGE_UNDETECTED}{self.file.kind}{self.file.track.removeprefix(self.file.video_name)}.txt"

    def to_text(self) -> str:
        return format_blocks(sorted(self.lines.items()))

    def describe(self) -> str:
        origin = "made by ocr" if self.file.find_image_subtitle() else "extracted"
        kind = "SDH" if self.file.is_sdh() else "base"
        return f"- `{WORK_REFERENCES}/{self.name}`: {self.file.language or LANGUAGE_UNDETECTED}, {kind}, {origin}, {len(self.lines)} of {self.entries_english} English entries matched"


def format_references_index(references: list[Reference]) -> str:
    return "\n".join(reference.describe() for reference in references) + "\n" if references else "No other subtitles.\n"


@dataclass(frozen=True)
class Draft:
    translated: dict[int, tuple[str, ...]]
    repeated: tuple[int, ...]

    @classmethod
    def from_parts(cls, parts: list[str]) -> "Draft":
        translated: dict[int, tuple[str, ...]] = {}
        repeated: list[int] = []
        for number, lines in (block for part in parts for block in parse_blocks(part)):
            if number in translated:
                repeated.append(number)
            translated[number] = lines
        return cls(translated=translated, repeated=tuple(repeated))

    def find_problems(self, source: Source) -> list[str]:
        problems = [f"#{number}: written more than once" for number in self.repeated]
        for number, lines in sorted(self.translated.items()):
            english = source.english.get(number)
            if english is None:
                problems.append(f"#{number}: not in the source")
                continue
            if len(lines) != len(english):
                problems.append(f"#{number}: {len(lines)} lines, the English has {len(english)}")
        return problems

    def find_warnings(self, source: Source) -> list[str]:
        warnings = []
        for number, lines in sorted(self.translated.items()):
            english = source.english.get(number)
            if english is None:
                continue
            tags, tags_english = FORMATTING_TAGS.findall(" ".join(lines)), FORMATTING_TAGS.findall(" ".join(english))
            if Counter(tags) != Counter(tags_english):
                warnings.append(f"#{number}: tags {' '.join(tags) or 'none'}, the English has {' '.join(tags_english) or 'none'}")
            digits, digits_english = DIGITS.findall(" ".join(lines)), DIGITS.findall(" ".join(english))
            if Counter(digits) != Counter(digits_english):
                warnings.append(f"#{number}: digits {' '.join(digits) or 'none'}, the English has {' '.join(digits_english) or 'none'}")
        return warnings

    def find_missing(self, source: Source) -> list[int]:
        return [number for number in source.english if number not in self.translated]

    def describe(self, source: Source) -> str:
        missing = self.find_missing(source)
        if not missing:
            return f"translated all {len(source.english)} entries"
        return f"translated {len(source.english) - len(missing)} of {len(source.english)} entries, first missing #{missing[0]}"

    def compose(self, entries: list[srt.Subtitle]) -> str:
        composed = [replace_text(entry, "\n".join(self.translated[number]) if number in self.translated else entry.content) for number, entry in enumerate(entries, start=1)]
        return srt.compose(composed, reindex=False)


@dataclass(frozen=True)
class Session:
    session_id: str
    home: Path


@dataclass(frozen=True)
class WorkFolder:
    path: Path

    @classmethod
    def for_video(cls, video_name: str) -> "WorkFolder":
        return cls(path=WORK_ROOT / video_name)

    def create(self, source: Source, references: list[Reference]) -> None:
        if self.path.exists():
            self.remove()
        (self.path / WORK_REFERENCES).mkdir(parents=True)
        (self.path / WORK_TRANSLATION).mkdir()
        (self.path / WORK_ENGLISH).write_text(source.to_text(), encoding="utf-8", newline="\n")
        for reference in references:
            (self.path / WORK_REFERENCES / reference.name).write_text(reference.to_text(), encoding="utf-8", newline="\n")
        (self.path / WORK_REFERENCES_INDEX).write_text(format_references_index(references), encoding="utf-8", newline="\n")

    def save_session(self, session: Session) -> None:
        (self.path / WORK_SESSION).write_text(json.dumps({"session_id": session.session_id, "home": str(session.home)}), encoding="utf-8", newline="\n")

    def find_session(self) -> Session | None:
        file = self.path / WORK_SESSION
        if not file.exists():
            return None
        record = json.loads(file.read_text(encoding="utf-8"))
        return Session(session_id=record["session_id"], home=Path(record["home"]))

    def read_references_index(self) -> str:
        return (self.path / WORK_REFERENCES_INDEX).read_text(encoding="utf-8")

    def read_source(self) -> Source:
        return Source.from_text((self.path / WORK_ENGLISH).read_text(encoding="utf-8"))

    def read_draft(self) -> Draft:
        return Draft.from_parts([part.read_text(encoding="utf-8") for part in sorted((self.path / WORK_TRANSLATION).glob("*.txt"))])

    def read_notes(self) -> str:
        notes = self.path / WORK_NOTES
        return notes.read_text(encoding="utf-8") if notes.exists() else ""

    def remove(self) -> None:
        shutil.rmtree(self.path)
