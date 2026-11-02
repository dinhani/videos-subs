# Identifies the language of text subtitles and renames each file with that language code.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
from shared import IMAGE_EXTENSIONS, SUBS_FOLDER, Batch, Color, SubtitleKind, SubtitleLanguage, find_files, find_language_code, log_action, parse_movie_dir, parse_srt

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_files("Identifies the language of text subtitles and renames each file with that language code.", extensions_file=(".srt",), extensions_folder=(".srt",), folder=SUBS_FOLDER)
    batch = Batch()

    for sub in subs:
        batch.log_movie(parse_movie_dir(sub))

        # skip subs already identified, and subs made by ocr, which identify-ocr names with their image subtitle
        sub_language_code = find_language_code(sub)
        if sub_language_code is not None:
            log_action("exists", Color.BLUE, f".{sub_language_code:<3} {'':<26} {sub.name}")
            continue
        if any(sub.with_suffix(extension).exists() for extension in IMAGE_EXTENSIONS):
            log_action("ocr", Color.BLUE, f"{'':<31} {sub.name}")
            continue

        try:
            # identify language and kind
            sub_texts = [entry.content for entry in parse_srt(sub)]
            sub_language = SubtitleLanguage.from_text(" ".join(sub_texts))
            sub_kind = SubtitleKind.from_texts(sub_texts)

            # rename file with language code and kind
            sub.replace(sub.with_name(f"{sub.stem}.{sub_language.code}{sub_kind}{sub.suffix}"))
            log_action("rename", Color.GREEN, f".{sub_language.code:<3}{sub_kind:<4} {sub_language.name:<22} {sub.name}")
        except Exception as error:
            batch.fail(sub, error)

    batch.finish()
