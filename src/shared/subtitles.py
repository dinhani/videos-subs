import re
from collections import Counter
from enum import StrEnum
from pathlib import Path

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

    @classmethod
    def from_file(cls, file: Path) -> "SubtitleKind":
        return cls.SDH if Path(file.stem).suffix == cls.SDH else cls.BASE

    @classmethod
    def find_in_translation(cls, file: Path) -> "SubtitleKind | None":
        return next((kind for kind in cls if file.name.endswith(kind.to_translation_suffix())), None)

    def to_translation_suffix(self) -> str:
        return f".{LANGUAGE_TRANSLATION}{self}.translated.srt"

    @classmethod
    def from_texts(cls, texts: list[str]) -> "SubtitleKind":
        texts = [FORMATTING_TAGS.sub("", text) for text in texts]
        entries_described = sum(1 for text in texts if SOUND_DESCRIPTION.search(text))
        if not texts or entries_described < SDH_ENTRIES_DESCRIBED_MIN * len(texts):
            return cls.BASE

        descriptions = Counter(" ".join((match[1] or match[2]).lower().split()) for text in texts for match in SOUND_DESCRIPTION.finditer(text))
        descriptions_top = sum(count for _, count in descriptions.most_common(SDH_DESCRIPTIONS_TOP))
        return cls.SDH if descriptions_top < SDH_DESCRIPTIONS_TOP_MAX * descriptions.total() else cls.BASE


def parse_plain_text(entry: srt.Subtitle) -> str:
    return "\n".join(" ".join(line.split()) for line in FORMATTING_TAGS.sub("", entry.content).split("\n") if line.strip())
