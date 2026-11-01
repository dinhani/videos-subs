# Identifies the language of text subtitles and renames each file with that language code.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
from shared import SUBS_FOLDER, Batch, Color, SubtitleLanguage, find_files, find_language_code, log_action, parse_movie_dir, parse_srt

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
subs = find_files("Identifies the language of text subtitles and renames each file with that language code.", extensions_file=(".srt",), extensions_folder=(".srt",), folder=SUBS_FOLDER)
batch = Batch()

for sub in subs:
    batch.log_movie(parse_movie_dir(sub))

    # skip subs already identified
    sub_language_code = find_language_code(sub)
    if sub_language_code is not None:
        log_action("exists", Color.BLUE, f".{sub_language_code:<3} {'':<22} {sub.name}")
        continue

    try:
        # identify language
        sub_language = SubtitleLanguage.from_text(" ".join(entry.content for entry in parse_srt(sub)))

        # rename file with language code
        sub.replace(sub.with_name(f"{sub.stem}.{sub_language.code}{sub.suffix}"))
        log_action("rename", Color.GREEN, f".{sub_language.code:<3} {sub_language.name:<22} {sub.name}")
    except Exception as error:
        batch.fail(sub, error)

batch.finish()
