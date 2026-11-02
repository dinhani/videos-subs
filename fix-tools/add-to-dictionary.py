# Adds words or expressions judged right to the data lists that check-translate reads, in lowercase and in alphabetical order. Skips the ones already in the list.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import unicodedata
from enum import Enum
from pathlib import Path

from shared import Color, log_action, write_text_replacing

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
DATA_FOLDER = Path(__file__).parent.parent / "data"


# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
class DictionaryList(Enum):
    WORDS = "dict-words.txt"
    EXPRESSIONS = "dict-expressions.txt"

    @property
    def file(self) -> Path:
        return DATA_FOLDER / self.value

    def ensure_valid(self, item: str) -> None:
        if not item or item != " ".join(item.split()):
            raise ValueError(f"not a trimmed text: {item!r}")
        if self == DictionaryList.WORDS and " " in item:
            raise ValueError(f"not a single word: {item!r}")
        if self == DictionaryList.EXPRESSIONS and " " not in item:
            raise ValueError(f"not an expression of several words: {item!r}")


# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adds words or expressions judged right to the data lists of check-translate.")
    parser.add_argument("list", choices=["words", "expressions"], help="words: data/dict-words.txt, expressions: data/dict-expressions.txt")
    parser.add_argument("items", nargs="+", help="words, or expressions as they are in the entry")
    arguments = parser.parse_args()
    dictionary = DictionaryList[arguments.list.upper()]

    # check every item before writing
    items = list(dict.fromkeys(item.lower() for item in arguments.items))
    for item in items:
        dictionary.ensure_valid(item)

    # add the new items, sorted by their letters without accents
    items_listed = dictionary.file.read_text(encoding="utf-8").splitlines()
    items_new = [item for item in items if item not in items_listed]
    if items_new:
        items_sorted = sorted([*items_listed, *items_new], key=lambda item: ("".join(char for char in unicodedata.normalize("NFD", item) if not unicodedata.combining(char)), item))
        write_text_replacing(dictionary.file, "\n".join(items_sorted) + "\n", newline="\n")

    for item in items:
        log_action("add" if item in items_new else "exists", Color.GREEN if item in items_new else Color.BLUE, item)
