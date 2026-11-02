# Identifies the language of image subtitles (PGS .sup, DVD .mks) by reading their first entries with Tesseract, and renames each file with that language code.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import itertools
import math
import os
import subprocess
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn

from shared.console import Batch, Color, log_action, progress_bar
from shared.files import IMAGE_EXTENSIONS, SUBS_FOLDER, TRACES_FOLDER, find_files, find_language_code, parse_movie_dir
from shared.image_subtitles import parse_image_subtitle
from shared.languages import SubtitleLanguage
from shared.subtitles import SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
SAMPLE_ENTRIES = 200
SAMPLE_WORKERS = 8

TESSERACT_MODEL = "script/Latin"
TESSERACT_WORKERS = 8

# ------------------------------------------------------------------------------
# OCR
# ------------------------------------------------------------------------------
def read_sample(sub: Path) -> list[str] | None:
    # read the first entries
    entries = parse_image_subtitle(sub, entries_max=SAMPLE_ENTRIES)
    if not entries:
        return None

    # read the entry images with tesseract, split into parallel runs of one page per image
    with tempfile.TemporaryDirectory() as images_dir:
        images = [Path(images_dir) / f"{n}.png" for n in range(len(entries))]
        for entry, image in zip(entries, images):
            entry.to_black_on_white().save(image)
        images_parts = list(itertools.batched(images, math.ceil(len(images) / TESSERACT_WORKERS)))
        images_lists: list[Path] = []
        for n, images_part in enumerate(images_parts):
            images_list = Path(images_dir) / f"images.{n}.txt"
            images_list.write_text("\n".join(str(image) for image in images_part), encoding="utf-8")
            images_lists.append(images_list)
        with ThreadPoolExecutor(max_workers=TESSERACT_WORKERS) as executor:
            tesseracts = list(executor.map(
                lambda images_list: subprocess.run(
                    ["tesseract", str(images_list), "stdout", "-l", TESSERACT_MODEL, "--psm", "6"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env={**os.environ, "OMP_THREAD_LIMIT": "1"},
                ),
                images_lists,
            ))
    if failed := [tesseract for tesseract in tesseracts if tesseract.returncode != 0]:
        raise RuntimeError(f"tesseract exited with {failed[0].returncode}: {' | '.join(failed[0].stderr.splitlines())}")
    return [page for tesseract, images_part in zip(tesseracts, images_parts) for page in tesseract.stdout.split("\f")[:len(images_part)]]

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_files("Identifies the language of image subtitles (PGS .sup, DVD .mks) by reading their first entries with Tesseract, and renames each file with that language code.", extensions_file=IMAGE_EXTENSIONS, extensions_folder=IMAGE_EXTENSIONS, folder=SUBS_FOLDER)
    batch = Batch()

    with ThreadPoolExecutor(max_workers=SAMPLE_WORKERS) as executor:
        # read a sample of every sub not identified yet, SAMPLE_WORKERS at a time across movies
        samples: dict[Path, Future[list[str] | None]] = {sub: executor.submit(read_sample, sub) for sub in subs if find_language_code(sub) is None}

        try:
            for movie_dir, movie_subs in itertools.groupby(subs, key=parse_movie_dir):
                batch.log_movie(movie_dir)

                # skip subs already identified
                subs_pending: list[Path] = []
                for sub in movie_subs:
                    sub_language_code = find_language_code(sub)
                    if sub_language_code is not None:
                        log_action("exists", Color.BLUE, f".{sub_language_code:<3} {'':<26} {sub.name}")
                        continue
                    subs_pending.append(sub)
                if not subs_pending:
                    continue

                # identify every pending sub of the movie as its sample arrives, showing progress
                with progress_bar(MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn()) as progress:
                    for sub in progress.track(subs_pending):
                        try:
                            # identify language and kind from the sample
                            sample = samples[sub].result()
                            if sample is None:
                                log_action("empty", Color.YELLOW, f"{'':<31} {sub.name}")
                                continue
                            sub_language = SubtitleLanguage.from_text(" ".join(sample))
                            sub_kind = SubtitleKind.from_texts(sample)

                            # rename file with language code and kind, with its ocr output and trace, the file itself last
                            sub_stem = f"{sub.stem}.{sub_language.code}{sub_kind}"
                            for companion, companion_new in (
                                (sub.with_suffix(".srt"), sub.with_name(f"{sub_stem}.srt")),
                                (movie_dir / TRACES_FOLDER / f"{sub.stem}.ocr.json", movie_dir / TRACES_FOLDER / f"{sub_stem}.ocr.json"),
                            ):
                                if companion.exists():
                                    companion.replace(companion_new)
                            sub.replace(sub.with_name(f"{sub_stem}{sub.suffix}"))
                            log_action("rename", Color.GREEN, f".{sub_language.code:<3}{sub_kind:<4} {sub_language.name:<22} {sub.name}")
                        except Exception as error:
                            batch.fail(sub, error)
        except KeyboardInterrupt:
            executor.shutdown(cancel_futures=True)
            raise

    batch.finish()
