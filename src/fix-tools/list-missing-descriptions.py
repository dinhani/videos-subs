# Lists the descriptions of the English sources that fix-translations cannot translate from data/fix-sounds.txt and data/fix-subjects.txt, the most common first.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
from collections import Counter

from shared.console import Color, console, log_action
from shared.data_lists import FIX_SOUNDS, FIX_SUBJECTS
from shared.descriptions import DESCRIPTION_SIMPLE, DescriptionTable
from shared.files import SUBS_FOLDER, find_files, find_language_code, parse_srt

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
DESCRIPTIONS_LISTED = 100

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    sources = [file for file in find_files(argparse.ArgumentParser(description="Lists the descriptions of the English sources that fix-translations cannot translate, the most common first."), extensions_file=(".srt",), extensions_folder=(".srt",), folder=SUBS_FOLDER) if find_language_code(file) == "eng"]
    table = DescriptionTable.from_lists(FIX_SOUNDS, FIX_SUBJECTS)

    # count every description with letters by its words in lowercase, keeping the first form seen to translate it
    counts: Counter[str] = Counter()
    forms: dict[str, str] = {}
    for source in sources:
        for entry in parse_srt(source):
            for match in DESCRIPTION_SIMPLE.finditer(entry.content):
                description = " ".join((match[1] or match[2]).split())
                if not any(character.isalpha() for character in description):
                    continue
                key = description.lower()
                counts[key] += 1
                forms.setdefault(key, description)

    missing = Counter({key: count for key, count in counts.items() if table.find_translation(forms[key]) is None})
    covered = counts.total() - missing.total()
    console.print(f"{covered} of {counts.total()} descriptions ({100 * covered // max(counts.total(), 1)}%) in {len(sources)} English sources have a translation", style=Color.CYAN)
    for key, count in missing.most_common(DESCRIPTIONS_LISTED):
        log_action(f"{count}", Color.YELLOW, key)
