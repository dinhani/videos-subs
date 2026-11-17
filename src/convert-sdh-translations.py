# Converts each SDH translation into a translation without SDH, next to it: removes descriptions and speaker labels, and keeps song lyrics. Skips translations already converted.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import re

import srt

from shared.console import Action, Batch
from shared.files import SubtitleFile, find_files, parse_srt, write_text_replacing
from shared.subtitles import FORMATTING_TAGS, LANGUAGE_TRANSLATION, WARNING_SDH, SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
NOSDH_MARKER = ".nosdh"
SDH_NAME = f".{LANGUAGE_TRANSLATION}{SubtitleKind.SDH}."
NOSDH_NAME = f".{LANGUAGE_TRANSLATION}{NOSDH_MARKER}."
SDH_SUFFIXES = tuple(f"{SDH_NAME}{extension}" for extension in ("translated.srt", "claude.translated.srt", "translated.claude.srt"))

TAG = r"(?:<[^>]+>|\{[^}]*\})"
DESCRIPTION = re.compile(r"\[[^\[\]]*\]|\([^()]*\)")
SPEAKER_LABEL = re.compile(rf"^(?P<prefix>(?:{TAG}|[ \t-])*)(?:[A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ0-9'.#\- ]*[A-ZÀ-ÖØ-Þ0-9.])?[ \t]*:(?!\d)[ \t]*", re.MULTILINE)
LINE_EMPTY = re.compile(r"[\s\-–—♪♫]*")
EDGE_SPACES = re.compile(rf"^({TAG}*) +| +({TAG}*)$")
DIALOGUE_DASH = re.compile(rf"^({TAG}*)[ \t]*-[ \t]*")
EMPTY_TAGS = re.compile(r"<(\w+)[^>]*>\s*</\1>")

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Converts each SDH translation into a translation without SDH, next to it.")

# ------------------------------------------------------------------------------
# Functions
# ------------------------------------------------------------------------------
def remove_sdh(text: str) -> str:
    dashes_before = sum(1 for line in FORMATTING_TAGS.sub("", text).split("\n") if line.lstrip().startswith("-"))

    # descriptions and speaker labels
    text = SPEAKER_LABEL.sub(r"\g<prefix>", DESCRIPTION.sub(" ", text))

    # lines left without words
    lines: list[str] = []
    tags = ""
    for line in text.split("\n"):
        if LINE_EMPTY.fullmatch(FORMATTING_TAGS.sub("", line)):
            tags += "".join(FORMATTING_TAGS.findall(line))
            continue
        lines.append(EDGE_SPACES.sub(r"\1\2", tags + " ".join(line.split())))
        tags = ""
    if lines:
        lines[-1] += tags

    # dash of the one speaker left
    dash_lines = [n for n, line in enumerate(lines) if FORMATTING_TAGS.sub("", line).startswith("-")]
    if dashes_before >= 2 and len(dash_lines) == 1:
        lines[dash_lines[0]] = DIALOGUE_DASH.sub(r"\1", lines[dash_lines[0]], count=1)

    return EMPTY_TAGS.sub("", "\n".join(lines)).strip()

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    translations = [SubtitleFile.from_path(file) for file in find_files(create_parser(), extensions_file=SDH_SUFFIXES, extensions_folder=SDH_SUFFIXES)]

    with Batch() as batch:
        for translation in batch.walk(translations):
            with batch.attempt(translation.path):
                # skip translations already converted
                output = translation.path.with_name(translation.name.replace(SDH_NAME, NOSDH_NAME, 1))
                if output.exists():
                    Action.EXISTS.log(output.name)
                    continue

                # remove sdh from each entry, keeping the warnings other than sdh
                entries = parse_srt(translation.path)
                entries_warning = [entry for entry in entries if entry.index == 0 and entry.content != WARNING_SDH]
                entries_converted = [srt.Subtitle(index=0, start=entry.start, end=entry.end, content=remove_sdh(entry.content)) for entry in entries if entry.index != 0]
                entries_converted = [entry for entry in entries_converted if entry.content]
                for index, entry in enumerate(entries_converted, start=1):
                    entry.index = index

                # write
                write_text_replacing(output, srt.compose([*entries_warning, *entries_converted], reindex=False), newline="\n")
                Action.CONVERT.log(output.name)
