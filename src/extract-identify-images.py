# Identifies the language of image subtitles (PGS .sup, DVD .mks) by reading their first entries with Tesseract, and renames each file with that language code.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import itertools
import math
import os
import subprocess
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from shared.console import Action, Batch, cancel_on_interrupt
from shared.files import SubtitleFile, SubtitleFormat, find_subtitles
from shared.image_subtitles import parse_image_subtitle
from shared.languages import SubtitleLanguage
from shared.subtitles import SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
SAMPLE_ENTRIES = 200
NAS_WORKERS = 8

TESSERACT_MODEL = "script/Latin"
CPU_WORKERS = 8

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Identifies the language of image subtitles (PGS .sup, DVD .mks) by reading their first entries with Tesseract, and renames each file with that language code.")

# ------------------------------------------------------------------------------
# Functions
# ------------------------------------------------------------------------------
def read_sample(sub: SubtitleFile) -> list[str] | None:
    # read the first entries
    entries = parse_image_subtitle(sub.path, entries_max=SAMPLE_ENTRIES)
    if not entries:
        return None

    # read the entry images with tesseract, split into parallel runs of one page per image
    with tempfile.TemporaryDirectory() as images_dir:
        images = [Path(images_dir) / f"{n}.png" for n in range(len(entries))]
        for entry, image in zip(entries, images):
            entry.to_black_on_white().save(image)
        images_parts = list(itertools.batched(images, math.ceil(len(images) / CPU_WORKERS)))
        images_lists: list[Path] = []
        for n, images_part in enumerate(images_parts):
            images_list = Path(images_dir) / f"images.{n}.txt"
            images_list.write_text("\n".join(str(image) for image in images_part), encoding="utf-8")
            images_lists.append(images_list)
        with ThreadPoolExecutor(max_workers=CPU_WORKERS) as executor:
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
    subs = find_subtitles(create_parser(), formats=(SubtitleFormat.PGS, SubtitleFormat.VOBSUB))

    with Batch() as batch, cancel_on_interrupt(ThreadPoolExecutor(max_workers=NAS_WORKERS)) as executor:
        # read a sample of every sub not identified yet, NAS_WORKERS at a time across movies
        samples: dict[SubtitleFile, Future[list[str] | None]] = {sub: executor.submit(read_sample, sub) for sub in subs if not sub.is_identified()}

        # identify every pending sub of a movie as its sample arrives, skipping subs already identified
        for sub, sample_read in batch.walk_ahead(subs, samples):
            if sample_read is None:
                Action.EXISTS.log(f"{sub.describe_language()} {sub.name}")
                continue

            with batch.attempt(sub.path):
                # identify language and kind from the sample
                sample = sample_read.result()
                if sample is None:
                    Action.EMPTY.log(f"{sub.describe_language()} {sub.name}")
                    continue
                sub_language = SubtitleLanguage.from_text(" ".join(sample))
                sub_kind = SubtitleKind.from_texts(sample)

                # rename file with language code and kind, with its ocr output and trace
                sub_identified = sub.rename_identified(sub_language.code, sub_kind)
                Action.RENAME.log(f"{sub_identified.describe_language(sub_language.name)} {sub.name}")
