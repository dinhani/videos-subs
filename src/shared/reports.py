import itertools
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import srt
from rich.style import Style
from rich.text import Text

from shared.console import Action, Color, log_heading
from shared.files import write_text_replacing
from shared.subtitles import IgnoreMarker

CHECK_WORKERS = 8

REPORT_HEADER = re.compile(r"^score ([\d.]+) .* · (\d+) issues$")
REPORT_SUMMARY_START = 3
KIND_LABEL_WIDTH = 21
IGNORED_LABEL = "ignored"

SCORE_ENTRIES_MIN = 100
SCORE_REVIEW = 5.0
SCORE_BAD = 15.0
RANKING_SIZE = 20


class IssueKind(Enum):
    OCR_FAILED = ("ocr failed", 10, "1.1", "the entry has no text, the model never gave a usable reply")
    WRONG_ORDER = ("wrong order", 6, "1.2", "the lines are in a different order than on the image")
    INCONSISTENT = ("inconsistent", 4, "1.3", "another entry has the exact same image but was read differently; one of them is wrong")
    ARTIFACT = ("artifact", 3, "1.4", "the model added markup, such as *word* or _word_ for italics, or a tag other than <i>")
    GLYPH_CONFUSION = ("glyph confusion", 2, "1.5", "a letter read as a similar character, such as | for I or 0 for O")
    UNBALANCED = ("unbalanced", 2, "1.6", "a bracket opened or closed without its pair, also not in the next two entries")
    ACCEPTED_WITH_PROBLEM = ("accepted with problem", 4, "2.1", "the translation failed every retry and was accepted with a problem")
    UNTRANSLATED = ("untranslated", 6, "2.2", "a line reads as English or is the English line unchanged")
    ADDED_SYMBOLS = ("added symbols", 6, "2.3", "symbols, tags or letters of another script that the English entry does not have, such as pieces of the model reply")
    DUPLICATED_LINE = ("duplicated line", 6, "2.4", "one line holds the translation of the whole entry and another line repeats part of it")
    BROKEN_LINE = ("broken line", 4, "2.5", "a line without words or starting with a comma")
    GRAMMAR_VERB = ("grammar verb", 3, "2.6", "a verb broken at the line break, such as vão / mate-o")
    GRAMMAR_CONTRACTION = ("grammar contraction", 3, "2.7", "a contraction broken at the line break, such as de / a esposa")
    GRAMMAR_QUE = ("grammar que", 3, "2.8", "que at the end of a question, which must be quê")
    GRAMMAR_GENDER = ("grammar gender", 3, "2.9", "an article or adjective of another gender than its noun, such as um trincheira")
    UNKNOWN_WORD = ("unknown word", 3, "2.10", "a rare word missing from the Brazilian Portuguese dictionary: a typo, a broken word or a word of another language")
    UNKNOWN_WORD_CAPS = ("unknown word in caps", 3, "2.11", "an unknown word in capital letters, usually a misspelled sound description such as GRUNNIDO")
    PORTUGAL_PORTUGUESE = ("portugal portuguese", 3, "2.12", "a word used in Portugal, not in Brazil")
    UNBALANCED_TRANSLATION = ("unbalanced", 3, "2.13", "a bracket or quote that pairs in the English entry and not in the translation")

    def __init__(self, label: str, weight: int, rule: str, description: str) -> None:
        self.label = label
        self.weight = weight
        self.rule = rule
        self.description = description

    @property
    def marker(self) -> str:
        return self.label.replace(" ", "-")

    def is_ignored_in(self, entry: srt.Subtitle) -> bool:
        return self.marker in IgnoreMarker.from_entry(entry).kinds


def quote_entries(entries: Sequence[tuple[int, Sequence[str]]], i: int, context: int) -> tuple[str, ...]:
    quote: list[str] = []
    for j in range(max(0, i - context), min(len(entries), i + context + 1)):
        index, lines = entries[j]
        mark = ">>" if j == i else "  "
        quote += [f"{mark} {str(index) if n == 0 else '':>4}  {line}" for n, line in enumerate(lines)]
    return tuple(quote)


def log_ranking(scores: list["ReportScore"]) -> None:
    if not scores:
        return
    log_heading("Worst scores (weighted issues per 100 entries):", Color.CYAN)
    for score in sorted(scores, key=lambda score: score.score, reverse=True)[:RANKING_SIZE]:
        Action.SCORE.log(score.describe())


@dataclass(frozen=True)
class Issue:
    entry: srt.Subtitle
    kind: IssueKind
    detail: str
    quote: tuple[str, ...]

    def is_ignored(self) -> bool:
        return self.kind.is_ignored_in(self.entry)


@dataclass(frozen=True)
class Report:
    subtitle: Path
    entries_count: int
    issues: list[Issue]
    issues_ignored: int

    @classmethod
    def from_issues(cls, subtitle: Path, entries_count: int, issues: Iterable[Issue]) -> "Report":
        issues = list(issues)
        issues_kept = [issue for issue in issues if not issue.is_ignored()]
        return cls(subtitle=subtitle, entries_count=entries_count, issues=issues_kept, issues_ignored=len(issues) - len(issues_kept))

    @property
    def score(self) -> float:
        return round(100 * sum(issue.kind.weight for issue in self.issues) / max(self.entries_count, SCORE_ENTRIES_MIN), 1)

    def compose(self) -> str:
        kinds = Counter(issue.kind for issue in self.issues)
        lines = [
            str(self.subtitle.absolute()),
            f"score {self.score} (weighted issues per 100 entries) · {self.entries_count} entries · {len(self.issues)} issues",
            "",
            *(f"  {count:<4} {kind.label:<{KIND_LABEL_WIDTH}} {kind.description}" for kind, count in sorted(kinds.items(), key=lambda item: (-item[1], list(IssueKind).index(item[0])))),
            *([f"  {self.issues_ignored:<4} {IGNORED_LABEL:<{KIND_LABEL_WIDTH}} flags on entries marked with ignore: in the subtitle, not counted"] if self.issues_ignored else []),
        ]
        subtitle_lines = self.subtitle.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        entry_line_numbers = {int(line): number for number, (line, next_line) in enumerate(itertools.pairwise(subtitle_lines), start=1) if line.strip().isdigit() and "-->" in next_line}
        for _, entry_issues in itertools.groupby(sorted(self.issues, key=lambda issue: (issue.entry.index, list(IssueKind).index(issue.kind))), key=lambda issue: issue.entry.index):
            entry_issues = list(entry_issues)
            entry = entry_issues[0].entry
            lines += ["", f"entry {entry.index} · line {entry_line_numbers[entry.index]} · {entry.to_srt().splitlines()[1]}"]
            lines += [f"  {issue.kind.label} · rule {issue.kind.rule} · ignore:{issue.kind.marker} · {issue.detail}" for issue in entry_issues]
            lines += [f"  {quote}" for quote in entry_issues[0].quote]
        return "\n".join(lines) + "\n"

    def write(self, file: Path) -> "ReportScore":
        file.parent.mkdir(exist_ok=True)
        write_text_replacing(file, self.compose())
        return ReportScore(report=file, score=self.score, issues_count=len(self.issues))


@dataclass(frozen=True)
class ReportScore:
    report: Path
    score: float
    issues_count: int

    @classmethod
    def find(cls, file: Path) -> "ReportScore | None":
        try:
            with file.open(encoding="utf-8") as report:
                header = REPORT_HEADER.match(next(itertools.islice(report, 1, None), "").rstrip("\n"))
        except FileNotFoundError:
            return None
        if header is None:
            return None
        return cls(report=file, score=float(header[1]), issues_count=int(header[2]))

    def find_issues_by_label(self) -> Counter[str]:
        issues: Counter[str] = Counter()
        lines = self.report.read_text(encoding="utf-8").splitlines()
        for line in itertools.takewhile(str.strip, lines[REPORT_SUMMARY_START:]):
            count, text = line.split(maxsplit=1)
            label = text[:KIND_LABEL_WIDTH].strip()
            if label != IGNORED_LABEL:
                issues[label] = int(count)
        return issues

    @property
    def color(self) -> Color:
        if self.score >= SCORE_BAD:
            return Color.RED
        if self.score >= SCORE_REVIEW:
            return Color.YELLOW
        return Color.GREEN

    def describe(self) -> Text:
        report = self.report.absolute()
        return Text.assemble((f"{self.score:5.1f}", self.color), f" {self.issues_count:5} issues  ", (report.name, Style(link=report.as_uri())))
