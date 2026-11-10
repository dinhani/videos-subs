# Identifies the language of text subtitles and renames each file with that language code.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse

from shared.console import Action, Batch
from shared.files import SubtitleFormat, find_subtitles, parse_srt
from shared.languages import SubtitleLanguage
from shared.subtitles import SubtitleKind

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Identifies the language of text subtitles and renames each file with that language code.")

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_subtitles(create_parser(), formats=(SubtitleFormat.TEXT,))

    with Batch() as batch:
        for sub in batch.walk(subs):
            # skip subs already identified, and subs made by ocr, which identify-ocr names with their image subtitle
            if sub.is_identified():
                Action.EXISTS.log(f"{sub.describe_language()} {sub.name}")
                continue
            if sub.find_image_subtitle() is not None:
                Action.SKIP.log(f"{'made by ocr':<31} {sub.name}")
                continue

            with batch.attempt(sub.path):
                # identify language and kind
                sub_texts = [entry.content for entry in parse_srt(sub.path)]
                sub_language = SubtitleLanguage.from_text(" ".join(sub_texts))
                sub_kind = SubtitleKind.from_texts(sub_texts)

                # rename file with language code and kind
                sub_identified = sub.rename_identified(sub_language.code, sub_kind)
                Action.RENAME.log(f"{sub_identified.describe_language(sub_language.name)} {sub.name}")
