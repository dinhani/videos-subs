# Crosschecks the files of each movie folder: a text subtitle made by ocr must have the language code and kind of its image subtitle, a track has at most one text subtitle, a translation must have an English source of its kind, reports must have their subtitle, and no .part file may be left.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import re
from collections import defaultdict
from pathlib import Path

from shared.console import Batch, Color, console
from shared.files import CHECKS_FOLDER, IMAGE_EXTENSIONS, SUBS_FOLDER, find_files, find_language_code
from shared.subtitles import SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
WORK_FOLDERS = (SUBS_FOLDER, CHECKS_FOLDER)
SOURCE_LANGUAGE = "eng"
TRACK = re.compile(r"^(.*\.embed\.\d+)\.")
PART_SUFFIX = ".part"

OCR_REPORT_SUFFIX = ".ocr.txt"
TRANSLATION_REPORT_SUFFIX = ".translated.txt"

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    files = find_files("Crosschecks the subtitles, translations and reports of each movie folder.", extensions_file=IMAGE_EXTENSIONS + (".srt",), extensions_folder=IMAGE_EXTENSIONS + (".srt", PART_SUFFIX))
    movie_dirs = sorted({file.parent.parent if file.parent.name in WORK_FOLDERS else file.parent for file in files})
    batch = Batch()

    for movie_dir in movie_dirs:
        # list the files of the movie folder and its work folders
        files_by_folder = {folder: sorted(path for path in (movie_dir / folder).iterdir() if path.is_file()) if (movie_dir / folder).is_dir() else [] for folder in WORK_FOLDERS}
        movie_files = sorted(path for path in movie_dir.iterdir() if path.is_file())
        subs = files_by_folder[SUBS_FOLDER]
        subs_names = {sub.name for sub in subs}
        translations_names = {file.name for file in movie_files if SubtitleKind.find_in_translation(file) is not None}
        subs_by_track: defaultdict[str, list[Path]] = defaultdict(list)
        for sub in subs:
            if track := TRACK.match(sub.name):
                subs_by_track[track[1]].append(sub)
        problems: list[tuple[Path, str]] = []

        # a text subtitle made by ocr has the language code and kind of its image subtitle
        for sub in subs:
            if sub.suffix not in IMAGE_EXTENSIONS or not (track := TRACK.match(sub.name)):
                continue
            texts_different = [text for text in subs_by_track[track[1]] if text.suffix == ".srt" and (find_language_code(text), SubtitleKind.from_file(text)) != (find_language_code(sub), SubtitleKind.from_file(sub))]
            if texts_different:
                problems.append((sub, f"text subtitle with another language code or kind: {', '.join(text.name for text in texts_different)}"))

        # a track has at most one text subtitle
        for track_subs in subs_by_track.values():
            texts = [sub for sub in track_subs if sub.suffix == ".srt"]
            if len(texts) > 1:
                problems.append((texts[0], f"{len(texts)} text subtitles for one track: {', '.join(text.name for text in texts)}"))

        # a translation has one English source of its kind
        for name in sorted(translations_names):
            track = TRACK.match(name)
            sources = [sub for sub in subs_by_track[track[1]] if sub.suffix == ".srt"] if track else []
            if not sources:
                problems.append((movie_dir / name, "no source in _subs"))
            elif len(sources) == 1 and find_language_code(sources[0]) != SOURCE_LANGUAGE:
                problems.append((movie_dir / name, f"source is not English: {sources[0].name}"))
            elif len(sources) == 1 and SubtitleKind.from_file(sources[0]) != SubtitleKind.find_in_translation(movie_dir / name):
                problems.append((movie_dir / name, f"source of another kind: {sources[0].name}"))

        # reports belong to a subtitle that exists
        for report in files_by_folder[CHECKS_FOLDER]:
            if report.name.endswith(OCR_REPORT_SUFFIX) and f"{report.name.removesuffix(OCR_REPORT_SUFFIX)}.srt" not in subs_names:
                problems.append((report, "OCR report without its text subtitle"))
            elif report.name.endswith(TRANSLATION_REPORT_SUFFIX) and f"{report.name.removesuffix(TRANSLATION_REPORT_SUFFIX)}.translated.srt" not in translations_names:
                problems.append((report, "translation report without its translation"))

        # no write was left half done
        for file in movie_files + [file for folder_files in files_by_folder.values() for file in folder_files]:
            if file.name.endswith(PART_SUFFIX):
                problems.append((file, "file left by a write that did not finish"))

        for file, problem in problems:
            batch.log_movie(movie_dir)
            batch.fail(file, ValueError(problem))

    console.print()
    console.print(f"{len(movie_dirs)} movie folders checked, {len(batch.failures)} problems", style=Color.RED if batch.failures else Color.GREEN)
    batch.finish()
