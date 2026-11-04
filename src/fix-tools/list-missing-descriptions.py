# Lists the descriptions of the English sources that fix-translations cannot translate from data/fix-sounds.txt and data/fix-subjects.txt, the most common first.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
from collections import Counter

from rich.text import Text

from shared.console import Color, console, log_heading
from shared.data_lists import FIX_SOUNDS, FIX_SUBJECTS
from shared.descriptions import DESCRIPTION_SIMPLE, DescriptionTable, MovieNames
from shared.files import SubtitleFormat, find_subtitles, parse_srt
from shared.subtitles import LANGUAGE_SOURCE

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
DESCRIPTIONS_LISTED = 100

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    sources = [sub for sub in find_subtitles(argparse.ArgumentParser(description="Lists the descriptions of the English sources that fix-translations cannot translate, the most common first."), formats=(SubtitleFormat.TEXT,), language=LANGUAGE_SOURCE) if sub.is_english()]
    table = DescriptionTable.from_lists(FIX_SOUNDS, FIX_SUBJECTS)

    # count every description with letters by its words in lowercase, keeping the first form seen to translate it; a description that is only names of the movie, such as a speaker, is left out
    counts: Counter[str] = Counter()
    forms: dict[str, str] = {}
    for source in sources:
        movie_names = MovieNames.from_subs_folder(source.folder)
        for entry in parse_srt(source.path):
            for match in DESCRIPTION_SIMPLE.finditer(entry.content):
                description = " ".join((match[1] or match[2]).split())
                if not any(character.isalpha() for character in description) or movie_names.has_names_only(description):
                    continue
                key = description.lower()
                counts[key] += 1
                forms.setdefault(key, description)

    missing = Counter({key: count for key, count in counts.items() if table.find_translation(forms[key]) is None})
    covered = counts.total() - missing.total()
    log_heading(f"{covered} of {counts.total()} descriptions ({100 * covered // max(counts.total(), 1)}%) in {len(sources)} English sources have a translation", Color.CYAN)
    for key, count in missing.most_common(DESCRIPTIONS_LISTED):
        console.print(Text.assemble("  ", (f"{count:<9}", Color.YELLOW), " ", key))
