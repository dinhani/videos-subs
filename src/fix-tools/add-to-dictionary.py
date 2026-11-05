# Adds items judged right to the data lists that check-translations and fix-translations read, in lowercase and in alphabetical order: words and expressions that are right, and the pairs and triples that fix-translations writes for a description, a subject or a misspelled word. Skips the items already in the list.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse

from shared.console import Action
from shared.data_lists import DATA_LISTS

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adds items judged right to the data lists of check-translations and fix-translations.")
    parser.add_argument("list", choices=[data_list.name for data_list in DATA_LISTS], help="the data list, by its file name in data")
    parser.add_argument("items", nargs="+", help="words, expressions as they are in the entry, or pairs as 'english = português' (fix-sounds, fix-music, fix-objects, fix-manners, fix-music-moods, fix-voice-moods, fix-languages, fix-channels, fix-subjects, fix-interjections), or triples as 'english = misspelled = right' (fix-words) and 'english = wrong expression = right expression' (fix-expressions), where the English word must be in the English entry")
    arguments = parser.parse_args()
    data_list = next(data_list for data_list in DATA_LISTS if data_list.name == arguments.list)

    for item, added in data_list.add(arguments.items).items():
        (Action.ADD if added else Action.EXISTS).log(item)
