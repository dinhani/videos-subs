import functools
import os
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from spylls.hunspell import Dictionary

from shared.files import write_text_replacing

DATA_FOLDER = Path(__file__).parents[2] / "data"
DICTIONARY_HUNSPELL = Path(os.environ["LOCALAPPDATA"]) / "hunspell" / "pt_BR"

SEPARATOR = " = "


class DataListFormat(Enum):
    WORD = ("word", 1)
    EXPRESSION = ("expression of several words", 1)
    PAIR = ("english = português", 2)
    TRIPLE = ("english = misspelled = right", 3)
    EXPRESSION_TRIPLE = ("english = wrong expression = right expression", 3)

    def __init__(self, shape: str, fields: int) -> None:
        self.shape = shape
        self.fields = fields

    def ensure_valid(self, item: str) -> None:
        if not item or item != " ".join(item.split()):
            raise ValueError(f"not a trimmed text: {item!r}")
        parts = item.split(SEPARATOR)
        if len(parts) != self.fields or not all(parts):
            raise ValueError(f"not a '{self.shape}' item: {item!r}")
        match self:
            case DataListFormat.WORD if " " in item:
                raise ValueError(f"not a single word: {item!r}")
            case DataListFormat.EXPRESSION if " " not in item:
                raise ValueError(f"not an expression of several words: {item!r}")
            case DataListFormat.TRIPLE if " " in parts[1]:
                raise ValueError(f"the misspelled word is not a single word: {item!r}")
            case DataListFormat.TRIPLE if load_dictionary(DICTIONARY_HUNSPELL).lookup(parts[1]):
                raise ValueError(f"the misspelled word is a Brazilian Portuguese word: {item!r}")


@dataclass(frozen=True)
class DataList:
    file: Path
    format: DataListFormat

    @property
    def name(self) -> str:
        return self.file.stem

    @functools.cache
    def parse(self) -> tuple[tuple[str, ...], ...]:
        items: list[tuple[str, ...]] = []
        for line in self.file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = tuple(part.strip() for part in line.split(SEPARATOR))
            if len(parts) != self.format.fields or not all(parts):
                raise ValueError(f"{self.file.name}: not a '{self.format.shape}' line: {line!r}")
            items.append(parts)
        return tuple(items)

    def parse_key(self, item: str) -> str:
        return SEPARATOR.join(item.split(SEPARATOR)[:-1]) if self.format.fields > 1 else item

    def add(self, items: list[str]) -> dict[str, bool]:
        # check every item before writing; an item whose key is listed with another value is an error
        items = list(dict.fromkeys(" ".join(item.lower().split()) for item in items))
        for item in items:
            self.format.ensure_valid(item)
        items_listed = self.file.read_text(encoding="utf-8").splitlines()
        values_listed = {self.parse_key(item): item for item in items_listed}
        for item in items:
            listed = values_listed.get(self.parse_key(item))
            if listed is not None and listed != item:
                raise ValueError(f"already listed with another value: {listed!r}")

        # add the new items, sorted by their letters without accents
        items_new = [item for item in items if item not in items_listed]
        if items_new:
            items_sorted = sorted([*items_listed, *items_new], key=lambda item: ("".join(character for character in unicodedata.normalize("NFD", self.parse_key(item)) if not unicodedata.combining(character)), item))
            write_text_replacing(self.file, "\n".join(items_sorted) + "\n", newline="\n")
        return {item: item in items_new for item in items}


@functools.cache
def load_dictionary(path: Path) -> Dictionary:
    return Dictionary.from_files(str(path))


CHECK_WORDS = DataList(file=DATA_FOLDER / "check-words.txt", format=DataListFormat.WORD)
CHECK_EXPRESSIONS = DataList(file=DATA_FOLDER / "check-expressions.txt", format=DataListFormat.EXPRESSION)
FIX_SOUNDS = DataList(file=DATA_FOLDER / "fix-sounds.txt", format=DataListFormat.PAIR)
FIX_MUSIC = DataList(file=DATA_FOLDER / "fix-music.txt", format=DataListFormat.PAIR)
FIX_OBJECTS = DataList(file=DATA_FOLDER / "fix-objects.txt", format=DataListFormat.PAIR)
FIX_LANGUAGES = DataList(file=DATA_FOLDER / "fix-languages.txt", format=DataListFormat.PAIR)
FIX_CHANNELS = DataList(file=DATA_FOLDER / "fix-channels.txt", format=DataListFormat.PAIR)
FIX_MUSIC_MOODS = DataList(file=DATA_FOLDER / "fix-music-moods.txt", format=DataListFormat.PAIR)
FIX_VOICE_MOODS = DataList(file=DATA_FOLDER / "fix-voice-moods.txt", format=DataListFormat.PAIR)
FIX_SOUND_LISTS = (FIX_SOUNDS, FIX_MUSIC, FIX_OBJECTS)
FIX_SUBJECTS = DataList(file=DATA_FOLDER / "fix-subjects.txt", format=DataListFormat.PAIR)
FIX_INTERJECTIONS = DataList(file=DATA_FOLDER / "fix-interjections.txt", format=DataListFormat.PAIR)
FIX_EXPRESSIONS = DataList(file=DATA_FOLDER / "fix-expressions.txt", format=DataListFormat.EXPRESSION_TRIPLE)
FIX_WORDS = DataList(file=DATA_FOLDER / "fix-words.txt", format=DataListFormat.TRIPLE)
DATA_LISTS = (CHECK_WORDS, CHECK_EXPRESSIONS, FIX_SOUNDS, FIX_MUSIC, FIX_OBJECTS, FIX_MUSIC_MOODS, FIX_VOICE_MOODS, FIX_LANGUAGES, FIX_CHANNELS, FIX_SUBJECTS, FIX_INTERJECTIONS, FIX_EXPRESSIONS, FIX_WORDS)
