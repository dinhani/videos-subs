# Crosschecks the files of each movie folder: a text subtitle made by ocr must have the language code and kind of its image subtitle, a track has at most one text subtitle, a translation must have an English source of its kind, reports must have their subtitle, and no .part file may be left.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
from collections import defaultdict
from pathlib import Path

from shared.console import Batch, Color, log_heading
from shared.files import CHECKS_FOLDER, SUBS_FOLDER, SubtitleFile, SubtitleFormat, find_files

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
WORK_FOLDERS = (SUBS_FOLDER, CHECKS_FOLDER)
PART_SUFFIX = ".part"
SUBTITLE_EXTENSIONS = tuple(format.extension for format in SubtitleFormat)

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Crosschecks the subtitles, translations and reports of each movie folder.")

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    files = find_files(create_parser(), extensions_file=SUBTITLE_EXTENSIONS, extensions_folder=SUBTITLE_EXTENSIONS + (PART_SUFFIX,))
    movie_dirs = sorted({file.parent.parent if file.parent.name in WORK_FOLDERS else file.parent for file in files})

    with Batch() as batch:
        for movie_dir in movie_dirs:
            # list the files of the movie folder and its work folders
            files_by_folder = {folder: sorted(path for path in (movie_dir / folder).iterdir() if path.is_file()) if (movie_dir / folder).is_dir() else [] for folder in WORK_FOLDERS}
            movie_files = sorted(path for path in movie_dir.iterdir() if path.is_file())
            subs = [sub for file in files_by_folder[SUBS_FOLDER] if (sub := SubtitleFile.find(file)) is not None]
            translations = [sub for file in movie_files if (sub := SubtitleFile.find(file)) is not None and sub.is_translation()]
            texts_by_track: defaultdict[str, list[SubtitleFile]] = defaultdict(list)
            for sub in subs:
                if sub.is_text():
                    texts_by_track[sub.track].append(sub)
            problems: list[tuple[Path, str]] = []

            # a text subtitle made by ocr has the language code and kind of its image subtitle
            for sub in subs:
                if not sub.is_image():
                    continue
                texts_different = [text for text in texts_by_track[sub.track] if (text.language, text.kind) != (sub.language, sub.kind)]
                if texts_different:
                    problems.append((sub.path, f"text subtitle with another language code or kind: {', '.join(text.name for text in texts_different)}"))

            # a track has at most one text subtitle
            for texts in texts_by_track.values():
                if len(texts) > 1:
                    problems.append((texts[0].path, f"{len(texts)} text subtitles for one track: {', '.join(text.name for text in texts)}"))

            # a translation has one English source of its kind
            for translation in sorted(translations, key=lambda translation: translation.name):
                sources = texts_by_track[translation.track]
                if not sources:
                    problems.append((translation.path, "no source in _subs"))
                elif len(sources) == 1 and not sources[0].is_english():
                    problems.append((translation.path, f"source is not English: {sources[0].name}"))
                elif len(sources) == 1 and sources[0].kind != translation.kind:
                    problems.append((translation.path, f"source of another kind: {sources[0].name}"))

            # reports belong to a subtitle that exists
            reports_ocr = {text.to_report() for texts in texts_by_track.values() for text in texts}
            reports_translation = {translation.to_report() for translation in translations}
            for report in files_by_folder[CHECKS_FOLDER]:
                if report.name.endswith(SubtitleFormat.TEXT.report_suffix) and report not in reports_ocr:
                    problems.append((report, "OCR report without its text subtitle"))
                elif report.name.endswith(SubtitleFormat.TRANSLATION.report_suffix) and report not in reports_translation:
                    problems.append((report, "translation report without its translation"))

            # no write was left half done
            for file in movie_files + [file for folder_files in files_by_folder.values() for file in folder_files]:
                if file.name.endswith(PART_SUFFIX):
                    problems.append((file, "file left by a write that did not finish"))

            for file, problem in problems:
                batch.log_movie(movie_dir)
                batch.fail(file, ValueError(problem))

        log_heading(f"{len(movie_dirs)} movie folders checked, {len(batch.failures)} problems", Color.RED if batch.failures else Color.GREEN)
