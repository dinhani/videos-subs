import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

import srt

FORMATTING_TAGS = re.compile(r"<[^>]+>|\{[^}]*\}")
IGNORE_MARKER = re.compile(r"\s*\bignore:(\S+)")
QUOTES = '"“”'
BRACKETS = (("(", ")"), ("[", "]"))

LANGUAGE_SOURCE = "eng"
LANGUAGE_TRANSLATION = "pob"
LANGUAGE_UNDETECTED = "und"

OCR_FAILED_TEXT = "[OCR failed]"

SOUND_DESCRIPTION = re.compile(r"\[([^\]]+)\]|\(([^)]+)\)")

SDH_ENTRIES_DESCRIBED_MIN = 0.05
SDH_DESCRIPTIONS_TOP = 3
SDH_DESCRIPTIONS_TOP_MAX = 0.60


class SubtitleKind(StrEnum):
    BASE = ""
    SDH = ".sdh"

    def is_sdh(self) -> bool:
        return self == SubtitleKind.SDH

    @classmethod
    def from_texts(cls, texts: list[str]) -> "SubtitleKind":
        texts = [FORMATTING_TAGS.sub("", text) for text in texts]
        entries_described = sum(1 for text in texts if SOUND_DESCRIPTION.search(text))
        if not texts or entries_described < SDH_ENTRIES_DESCRIBED_MIN * len(texts):
            return cls.BASE

        descriptions = Counter(" ".join((match[1] or match[2]).lower().split()) for text in texts for match in SOUND_DESCRIPTION.finditer(text))
        descriptions_top = sum(count for _, count in descriptions.most_common(SDH_DESCRIPTIONS_TOP))
        return cls.SDH if descriptions_top < SDH_DESCRIPTIONS_TOP_MAX * descriptions.total() else cls.BASE


@dataclass(frozen=True)
class IgnoreMarker:
    kinds: tuple[str, ...]

    @classmethod
    def from_entry(cls, entry: srt.Subtitle) -> "IgnoreMarker":
        return cls(kinds=tuple(dict.fromkeys(kind for match in IGNORE_MARKER.finditer(entry.proprietary or "") for kind in match[1].split(","))))

    def add(self, kinds: Iterable[str]) -> "IgnoreMarker":
        return IgnoreMarker(kinds=tuple(dict.fromkeys([*self.kinds, *kinds])))

    def to_entry(self, entry: srt.Subtitle) -> srt.Subtitle:
        proprietary = IGNORE_MARKER.sub("", entry.proprietary or "").strip()
        if self.kinds:
            proprietary = f"{proprietary} ignore:{','.join(self.kinds)}".strip()
        return srt.Subtitle(index=entry.index, start=entry.start, end=entry.end, content=entry.content, proprietary=proprietary)


def replace_text(entry: srt.Subtitle, content: str) -> srt.Subtitle:
    # an entry whose text changes is judged again, so it loses its marker
    return srt.Subtitle(index=entry.index, start=entry.start, end=entry.end, content=content, proprietary=IGNORE_MARKER.sub("", entry.proprietary or "").strip())


def parse_lines(entry: srt.Subtitle) -> tuple[str, ...]:
    return tuple(" ".join(line.split()) for line in entry.content.split("\n") if line.strip())


def parse_plain_text(entry: srt.Subtitle) -> str:
    return "\n".join(" ".join(line.split()) for line in FORMATTING_TAGS.sub("", entry.content).split("\n") if line.strip())
