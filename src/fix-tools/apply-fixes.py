# Applies fixes decided by hand to text subtitles. A fixes file lists, for each subtitle, the entries to fix: the entry number, its exact current text, and its new text or the issue kinds to ignore. Every fix is checked against its subtitle before any file is written.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import srt

from shared.console import Color, log_action
from shared.files import Backups, parse_srt, write_text_replacing
from shared.reports import IssueKind
from shared.subtitles import IGNORE_MARKER

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
IGNORE_KINDS = frozenset(kind.marker for kind in IssueKind)


# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class EntryFix:
    entry: int
    text_old: str
    text_new: str | None
    ignore: tuple[str, ...]

    @staticmethod
    def from_json(data: dict[str, Any]) -> "EntryFix":
        entry = data.get("entry")
        if not isinstance(entry, int):
            raise ValueError(f"entry is not a number: {data}")
        old = data.get("old")
        if not isinstance(old, list) or not old or not all(isinstance(line, str) for line in old):
            raise ValueError(f"entry {entry}: old is not a list of lines")
        new = data.get("new")
        if new is not None and (not isinstance(new, list) or not new or not all(isinstance(line, str) and line for line in new)):
            raise ValueError(f"entry {entry}: new is not a list of non-empty lines")
        ignore = data.get("ignore", [])
        if not isinstance(ignore, list) or any(kind not in IGNORE_KINDS for kind in ignore):
            raise ValueError(f"entry {entry}: ignore has a kind that is not an issue kind: {ignore}")
        if new is None and not ignore:
            raise ValueError(f"entry {entry}: has neither new nor ignore")
        if new == old:
            raise ValueError(f"entry {entry}: new is the same as old")
        return EntryFix(entry=entry, text_old="\n".join(old), text_new=None if new is None else "\n".join(new), ignore=tuple(ignore))

    def apply_to(self, subtitle: srt.Subtitle) -> srt.Subtitle:
        if subtitle.content != self.text_old:
            raise ValueError(f"entry {self.entry}: the text is not old:\n{subtitle.content}")

        # a new text is judged again, so it loses its marker
        proprietary = subtitle.proprietary
        if self.text_new is not None:
            proprietary = IGNORE_MARKER.sub("", proprietary).strip()

        # one marker holds every kind ignored in the entry
        if self.ignore:
            kinds_marked = [kind for match in IGNORE_MARKER.finditer(proprietary) for kind in match[1].split(",")]
            kinds = list(dict.fromkeys([*kinds_marked, *self.ignore]))
            proprietary = f"{IGNORE_MARKER.sub('', proprietary).strip()} ignore:{','.join(kinds)}".strip()

        content = subtitle.content if self.text_new is None else self.text_new
        return srt.Subtitle(index=subtitle.index, start=subtitle.start, end=subtitle.end, content=content, proprietary=proprietary)


@dataclass(frozen=True)
class SubtitleFixes:
    subtitle: Path
    fixes: tuple[EntryFix, ...]

    @staticmethod
    def from_json(data: dict[str, Any]) -> "SubtitleFixes":
        subtitle = data.get("subtitle")
        if not isinstance(subtitle, str) or not subtitle.lower().endswith(".srt"):
            raise ValueError(f"subtitle is not a .srt path: {subtitle}")
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"{subtitle}: entries is not a list of fixes")
        fixes = tuple(EntryFix.from_json(entry) for entry in entries)
        numbers = [fix.entry for fix in fixes]
        if len(numbers) != len(set(numbers)):
            raise ValueError(f"{subtitle}: an entry has more than one fix")
        return SubtitleFixes(subtitle=Path(subtitle), fixes=fixes)

    def compose(self) -> str:
        subtitles = parse_srt(self.subtitle)
        positions: dict[int, int] = {}
        for position, subtitle in enumerate(subtitles):
            if subtitle.index in positions:
                raise ValueError(f"{self.subtitle}: entry {subtitle.index} is in the file twice")
            positions[subtitle.index] = position

        for fix in self.fixes:
            if fix.entry not in positions:
                raise ValueError(f"{self.subtitle}: entry {fix.entry} not found")
            subtitles[positions[fix.entry]] = fix.apply_to(subtitles[positions[fix.entry]])
        return srt.compose(subtitles, reindex=False)


# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Applies fixes decided by hand to text subtitles, after checking every fix against its subtitle.")
    parser.add_argument("fixes", type=Path, help="JSON file: a list of {subtitle, entries: [{entry, old, new?, ignore?}]}")
    Backups.add_argument(parser)
    arguments = parser.parse_args()
    fixes_file: Path = arguments.fixes
    backups = Backups.start(arguments.backups)

    # check every fix before writing any subtitle
    subtitles_fixes = [SubtitleFixes.from_json(item) for item in json.loads(fixes_file.read_text(encoding="utf-8"))]
    if len({fixes.subtitle.resolve() for fixes in subtitles_fixes}) != len(subtitles_fixes):
        raise ValueError("a subtitle is in the fixes file more than once")
    texts = [(fixes, fixes.compose()) for fixes in subtitles_fixes]

    # back up each subtitle before writing it
    for fixes, text in texts:
        backups.save(fixes.subtitle)
        write_text_replacing(fixes.subtitle, text)
        log_action("fix", Color.GREEN, f"{fixes.subtitle.name} · {len(fixes.fixes)} entries")
