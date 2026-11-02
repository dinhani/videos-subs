import functools
import os
from pathlib import Path

DATA_FOLDER = Path(__file__).parents[2] / "data"
DICTIONARY_HUNSPELL = Path(os.environ["LOCALAPPDATA"]) / "hunspell" / "pt_BR"
CHECK_WORDS = DATA_FOLDER / "check-words.txt"
CHECK_EXPRESSIONS = DATA_FOLDER / "check-expressions.txt"
FIX_SOUNDS = DATA_FOLDER / "fix-sounds.txt"
FIX_SUBJECTS = DATA_FOLDER / "fix-subjects.txt"
FIX_WORDS = DATA_FOLDER / "fix-words.txt"


@functools.cache
def parse_pairs(file: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(" = ")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"{file.name}: not a 'key = value' line: {line!r}")
        pairs[key.strip()] = value.strip()
    return pairs
