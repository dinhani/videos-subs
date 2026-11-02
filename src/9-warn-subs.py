# Adds warnings at the start of every translation: that it is SDH, and that some entries may be wrong when the translation accepted a problem. Skips warnings already added.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
from datetime import timedelta

import srt

from shared.console import Batch, Color, log_action
from shared.files import TRACES_FOLDER, find_files, parse_movie_dir, parse_srt, write_text_replacing
from shared.subtitles import SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
TRANSLATION_SUFFIXES = tuple(kind.to_translation_suffix() for kind in SubtitleKind)

WARNING_SDH = "[Legenda SDH: descreve sons, músicas e quem fala.]"
WARNING_PROBLEM = "[Legenda traduzida automaticamente. Alguns trechos podem estar errados ou em inglês.]"
WARNING_DURATION = timedelta(seconds=5)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_files(argparse.ArgumentParser(description="Adds warnings at the start of every translation: SDH, and possible errors."), extensions_file=TRANSLATION_SUFFIXES, extensions_folder=TRANSLATION_SUFFIXES)
    batch = Batch()

    for sub in subs:
        movie_dir = parse_movie_dir(sub)
        batch.log_movie(movie_dir)

        try:
            # find the warnings the translation needs
            trace = movie_dir / TRACES_FOLDER / f"{sub.stem}.json"
            records = json.loads(trace.read_text(encoding="utf-8")) if trace.exists() else []
            warnings = [warning for warning, needed in (
                (WARNING_SDH, SubtitleKind.find_in_translation(sub) == SubtitleKind.SDH),
                (WARNING_PROBLEM, any("accepted_with_problem" in record for record in records)),
            ) if needed]

            # skip subs that need no warning or have every warning
            if not warnings:
                log_action("skip", Color.BLUE, sub.name)
                continue
            sub_entries = parse_srt(sub)
            warnings_missing = [warning for warning in warnings if all(entry.content != warning for entry in sub_entries)]
            if not warnings_missing:
                log_action("exists", Color.BLUE, sub.name)
                continue

            # add each warning as entry 0, keeping the numbers of the other entries
            entries_warning = [srt.Subtitle(index=0, start=timedelta(0), end=WARNING_DURATION, content=warning) for warning in warnings_missing]
            write_text_replacing(sub, srt.compose([*entries_warning, *sub_entries], reindex=False))
            log_action("warn", Color.GREEN, sub.name)
        except Exception as error:
            batch.fail(sub, error)

    batch.finish()
