# Adds warnings at the start of every translation: that it is SDH, and that some entries may be wrong when the translation accepted a problem. Skips warnings already added.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
from datetime import timedelta

import srt

from shared.console import Action, Batch
from shared.files import Backups, SubtitleFormat, find_subtitles, parse_srt, write_text_replacing
from shared.llm import Trace
from shared.subtitles import LANGUAGE_TRANSLATION

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
WARNING_SDH = "[Legenda SDH: descreve sons, músicas e quem fala.]"
WARNING_PROBLEM = "[Legenda traduzida automaticamente. Alguns trechos podem estar errados ou em inglês.]"
WARNING_DURATION = timedelta(seconds=5)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Adds warnings at the start of every translation: SDH, and possible errors.")
    Backups.add_argument(parser)
    subs = find_subtitles(parser, formats=(SubtitleFormat.TRANSLATION,), language=LANGUAGE_TRANSLATION)
    backups = Backups.start(parser.parse_args().backups)

    with Batch() as batch:
        for sub in batch.walk(subs):
            with batch.attempt(sub.path):
                # find the warnings the translation needs
                trace = Trace.find(sub.to_trace())
                warnings = [warning for warning, needed in (
                    (WARNING_SDH, sub.is_sdh()),
                    (WARNING_PROBLEM, trace is not None and bool(trace.find_problems_accepted())),
                ) if needed]

                # skip subs that need no warning or have every warning
                if not warnings:
                    Action.SKIP.log(sub.name)
                    continue
                sub_entries = parse_srt(sub.path)
                warnings_missing = [warning for warning in warnings if all(entry.content != warning for entry in sub_entries)]
                if not warnings_missing:
                    Action.EXISTS.log(sub.name)
                    continue

                # back up the sub and add each warning as entry 0, keeping the numbers of the other entries
                backups.save(sub.path)
                entries_warning = [srt.Subtitle(index=0, start=timedelta(0), end=WARNING_DURATION, content=warning) for warning in warnings_missing]
                write_text_replacing(sub.path, srt.compose([*entries_warning, *sub_entries], reindex=False))
                Action.WARN.log(sub.name)
