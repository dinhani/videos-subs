# Adds items judged right to the data lists that check-translations and fix-translations read, in lowercase and in alphabetical order: words and expressions that are right, and the pairs and triples that fix-translations writes for a description, a subject or a misspelled word. Skips the items already in the list.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import unicodedata
from enum import Enum
from pathlib import Path

from spylls.hunspell import Dictionary

from shared.console import Color, log_action
from shared.data_lists import CHECK_EXPRESSIONS, CHECK_WORDS, DICTIONARY_HUNSPELL, FIX_SOUNDS, FIX_SUBJECTS, FIX_WORDS
from shared.files import write_text_replacing


# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
class DictionaryList(Enum):
    CHECK_WORDS = CHECK_WORDS
    CHECK_EXPRESSIONS = CHECK_EXPRESSIONS
    FIX_SOUNDS = FIX_SOUNDS
    FIX_SUBJECTS = FIX_SUBJECTS
    FIX_WORDS = FIX_WORDS

    @property
    def file(self) -> Path:
        return self.value

    @property
    def has_pairs(self) -> bool:
        return self in (DictionaryList.FIX_SOUNDS, DictionaryList.FIX_SUBJECTS, DictionaryList.FIX_WORDS)

    def parse_key(self, item: str) -> str:
        if not self.has_pairs:
            return item
        return " = ".join(item.split(" = ")[:-1])

    def ensure_valid(self, item: str) -> None:
        if not item or item != " ".join(item.split()):
            raise ValueError(f"not a trimmed text: {item!r}")
        if self == DictionaryList.CHECK_WORDS and " " in item:
            raise ValueError(f"not a single word: {item!r}")
        if self == DictionaryList.CHECK_EXPRESSIONS and " " not in item:
            raise ValueError(f"not an expression of several words: {item!r}")
        if not self.has_pairs:
            return

        parts = item.split(" = ")
        if self != DictionaryList.FIX_WORDS and (len(parts) != 2 or not all(parts)):
            raise ValueError(f"not an 'english = português' pair: {item!r}")
        if self != DictionaryList.FIX_WORDS:
            return

        if len(parts) != 3 or not all(parts):
            raise ValueError(f"not an 'english = misspelled = right' triple: {item!r}")
        misspelled = parts[1]
        if " " in misspelled:
            raise ValueError(f"the misspelled word is not a single word: {item!r}")
        if Dictionary.from_files(str(DICTIONARY_HUNSPELL)).lookup(misspelled):
            raise ValueError(f"the misspelled word is a Brazilian Portuguese word: {item!r}")


# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adds items judged right to the data lists of check-translations and fix-translations.")
    parser.add_argument("list", choices=[dictionary.file.stem for dictionary in DictionaryList], help="the data list, by its file name in data")
    parser.add_argument("items", nargs="+", help="words, expressions as they are in the entry, or pairs as 'english = português' (fix-sounds, fix-subjects), or triples as 'english = misspelled = right' (fix-words), where the English word must be in the English entry")
    arguments = parser.parse_args()
    dictionary = DictionaryList[arguments.list.upper().replace("-", "_")]

    # check every item before writing; a pair whose key is listed with another value is an error
    items = list(dict.fromkeys(" ".join(item.lower().split()) for item in arguments.items))
    for item in items:
        dictionary.ensure_valid(item)
    items_listed = dictionary.file.read_text(encoding="utf-8").splitlines()
    values_listed = {dictionary.parse_key(item): item for item in items_listed}
    for item in items:
        listed = values_listed.get(dictionary.parse_key(item))
        if listed is not None and listed != item:
            raise ValueError(f"already listed with another value: {listed!r}")

    # add the new items, sorted by their letters without accents
    items_new = [item for item in items if item not in items_listed]
    if items_new:
        items_sorted = sorted([*items_listed, *items_new], key=lambda item: ("".join(char for char in unicodedata.normalize("NFD", dictionary.parse_key(item)) if not unicodedata.combining(char)), item))
        write_text_replacing(dictionary.file, "\n".join(items_sorted) + "\n", newline="\n")

    for item in items:
        log_action("add" if item in items_new else "exists", Color.GREEN if item in items_new else Color.BLUE, item)
