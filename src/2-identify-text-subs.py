# Identifies the language of text subtitles and renames each file with that language code.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse

from shared.console import Batch, Color, log_action
from shared.files import SUBS_FOLDER, SubtitleFile, find_files, parse_movie_dir, parse_srt
from shared.languages import SubtitleLanguage
from shared.subtitles import SubtitleKind

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_files(argparse.ArgumentParser(description="Identifies the language of text subtitles and renames each file with that language code."), extensions_file=(".srt",), extensions_folder=(".srt",), folder=SUBS_FOLDER)
    batch = Batch()

    for sub in subs:
        batch.log_movie(parse_movie_dir(sub))

        # skip subs already identified, and subs made by ocr, which identify-ocr names with their image subtitle
        sub_file = SubtitleFile.from_path(sub)
        if sub_file.is_identified():
            log_action("exists", Color.BLUE, f".{sub_file.language:<3} {'':<26} {sub.name}")
            continue
        if sub_file.find_image_subtitle() is not None:
            log_action("ocr", Color.BLUE, f"{'':<31} {sub.name}")
            continue

        try:
            # identify language and kind
            sub_texts = [entry.content for entry in parse_srt(sub)]
            sub_language = SubtitleLanguage.from_text(" ".join(sub_texts))
            sub_kind = SubtitleKind.from_texts(sub_texts)

            # rename file with language code and kind
            sub.replace(sub_file.to_identified(sub_language.code, sub_kind).path)
            log_action("rename", Color.GREEN, f".{sub_language.code:<3}{sub_kind:<4} {sub_language.name:<22} {sub.name}")
        except Exception as error:
            batch.fail(sub, error)

    batch.finish()
