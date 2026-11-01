import functools
import itertools
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import simplemma
import srt
from rich.style import Style
from rich.text import Text
from wordfreq import zipf_frequency

from shared import FORMATTING_TAGS, Color, console, find_language_code, log_action, write_text_replacing

CHECKS_FOLDER = "_checks"
CHECK_WORKERS = 8

REPORT_HEADER = re.compile(r"^score ([\d.]+) .* · (\d+) issues$")

SCORE_ENTRIES_MIN = 100
SCORE_REVIEW = 5.0
SCORE_BAD = 15.0
RANKING_SIZE = 20

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
SENTENCE_START = re.compile(r"(?:^|[.!?…\-–—\"“(♪:\n])[ \t]*$")

LANGUAGE_VOTE_DIFFERENCE_MIN = 1.0
LANGUAGE_VOTE_FREQUENCY_MIN = 3.0


class IssueKind(Enum):
    OCR_FAILED = ("ocr failed", 10, "the entry has no text, the model never gave a usable reply")
    WRONG_ORDER = ("wrong order", 6, "the lines are in a different order than on the image")
    INCONSISTENT = ("inconsistent", 4, "another entry has the exact same image but was read differently; one of them is wrong")
    ARTIFACT = ("artifact", 3, "the model added markup, such as *word* or _word_ for italics, or a tag other than <i>")
    GLYPH_CONFUSION = ("glyph confusion", 2, "a letter read as a similar character, such as | for I or 0 for O")
    UNBALANCED = ("unbalanced", 2, "a bracket opened or closed without its pair, also not in the next two entries")
    ACCEPTED_WITH_PROBLEM = ("accepted with problem", 4, "the translation failed every retry and was accepted with a problem")
    UNTRANSLATED = ("untranslated", 6, "a line reads as English or is the English line unchanged")
    ADDED_SYMBOLS = ("added symbols", 6, "symbols, tags or letters of another script that the English entry does not have, such as pieces of the model reply")
    DUPLICATED_LINE = ("duplicated line", 6, "one line holds the translation of the whole entry and another line repeats part of it")
    BROKEN_LINE = ("broken line", 4, "a line without words or starting with a comma")
    GRAMMAR = ("grammar", 3, "LanguageTool found a grammar error, such as a verb or contraction broken at the line break, or a wrong gender")
    UNKNOWN_WORD = ("unknown word", 3, "a rare word missing from the Brazilian Portuguese dictionary: a typo, a broken word or a word of another language")
    UNKNOWN_WORD_CAPS = ("unknown word in caps", 3, "an unknown word in capital letters, usually a misspelled sound description such as GRUNNIDO")
    PORTUGAL_PORTUGUESE = ("portugal portuguese", 3, "a word used in Portugal, not in Brazil")

    def __init__(self, label: str, weight: int, description: str) -> None:
        self.label = label
        self.weight = weight
        self.description = description


class TextLanguage(Enum):
    ENGLISH = ("eng", "en")
    PORTUGUESE_BRAZIL = ("pob", "pt")
    PORTUGUESE_PORTUGAL = ("por", "pt")

    def __init__(self, code: str, wordfreq_code: str) -> None:
        self.code = code
        self.wordfreq_code = wordfreq_code

    @classmethod
    def from_file(cls, file: Path) -> "TextLanguage":
        code = find_language_code(file)
        language = next((language for language in cls if language.code == code), None)
        if language is None:
            raise ValueError(f"language code {code or 'missing'} is not one of {', '.join(language.code for language in cls)}")
        return language

    @property
    def counterpart(self) -> "TextLanguage":
        return TextLanguage.PORTUGUESE_BRAZIL if self == TextLanguage.ENGLISH else TextLanguage.ENGLISH

    @functools.cache
    def measure_frequency(self, word: str) -> float:
        word = word.lower()
        lemma = simplemma.lemmatize(word, lang=self.wordfreq_code)
        return max(zipf_frequency(word, self.wordfreq_code), zipf_frequency(lemma, self.wordfreq_code))

    def outweighs(self, other: "TextLanguage", words: list["Word"], votes_min: int) -> bool:
        votes = Counter(
            difference > 0
            for word in {word.key: word for word in words}.values()
            if not word.name
            and max(self.measure_frequency(word.text), other.measure_frequency(word.text)) >= LANGUAGE_VOTE_FREQUENCY_MIN
            and abs(difference := self.measure_frequency(word.text) - other.measure_frequency(word.text)) >= LANGUAGE_VOTE_DIFFERENCE_MIN
        )
        return votes[True] >= votes_min and votes[True] > 2 * votes[False]


@dataclass(frozen=True)
class Word:
    text: str
    name: bool

    @property
    def key(self) -> str:
        return "".join(character for character in unicodedata.normalize("NFKD", self.text.lower().replace("’", "'")) if not unicodedata.combining(character))


@dataclass(frozen=True)
class Issue:
    entry: srt.Subtitle
    kind: IssueKind
    detail: str
    quote: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    subtitle: Path
    entries_count: int
    issues: list[Issue]

    @property
    def score(self) -> float:
        return round(100 * sum(issue.kind.weight for issue in self.issues) / max(self.entries_count, SCORE_ENTRIES_MIN), 1)

    def compose(self) -> str:
        kinds = Counter(issue.kind for issue in self.issues)
        lines = [
            self.subtitle.name,
            f"score {self.score} (weighted issues per 100 entries) · {self.entries_count} entries · {len(self.issues)} issues",
            "",
            *(f"{count:>5}  {kind.label:<21} {kind.description}" for kind, count in kinds.most_common()),
        ]
        for _, entry_issues in itertools.groupby(sorted(self.issues, key=lambda issue: (issue.entry.index, list(IssueKind).index(issue.kind))), key=lambda issue: issue.entry.index):
            entry_issues = list(entry_issues)
            entry = entry_issues[0].entry
            start = int(entry.start.total_seconds())
            lines += ["", f"{start // 3600}:{start // 60 % 60:02}:{start % 60:02}  entry {entry.index}"]
            lines += [f"  {issue.kind.label}: {issue.detail}" for issue in entry_issues]
            lines += [f"      {quote}" for quote in entry_issues[0].quote]
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

    @property
    def color(self) -> Color:
        if self.score >= SCORE_BAD:
            return Color.RED
        if self.score >= SCORE_REVIEW:
            return Color.YELLOW
        return Color.GREEN

    def describe(self) -> Text:
        report = self.report.absolute()
        return Text.assemble(f"{self.score:5.1f} {self.issues_count:5} issues  ", (report.name, Style(link=report.as_uri())))


def parse_plain_text(entry: srt.Subtitle) -> str:
    return "\n".join(" ".join(line.split()) for line in FORMATTING_TAGS.sub("", entry.content).split("\n") if line.strip())


def parse_words(text: str) -> list[Word]:
    return [Word(text=match[0], name=match[0][0].isupper() and SENTENCE_START.search(text, 0, match.start()) is None) for match in WORD.finditer(text)]


@functools.cache
def list_folder(folder: Path) -> frozenset[str]:
    return frozenset(os.listdir(folder))


def log_ranking(scores: list[ReportScore]) -> None:
    if not scores:
        return
    console.print()
    console.print("Worst scores (weighted issues per 100 entries):", style=Color.CYAN)
    for score in sorted(scores, key=lambda score: score.score, reverse=True)[:RANKING_SIZE]:
        log_action("score", score.color, score.describe())
