# Checks the text subtitles made by OCR against their image subtitles and writes a report of each one to _checks, skipping ones with a current report.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

import srt
from PIL import ImageFont, ImageOps

from shared.console import Action, Batch, cancel_on_interrupt
from shared.files import SubtitleFile, SubtitleFormat, find_subtitles, parse_srt
from shared.image_subtitles import SubtitleEntry, parse_image_subtitle
from shared.languages import LANGUAGES_OCR
from shared.reports import NAS_WORKERS, Issue, IssueKind, Report, ReportScore, log_ranking, quote_entries
from shared.subtitles import OCR_FAILED_TEXT, parse_plain_text

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
ENTRY_START_TOLERANCE = timedelta(milliseconds=2)

FONT_SIZE = 100
FONT = ImageFont.truetype("arial.ttf", FONT_SIZE)
LINE_HEIGHT_MIN = 0.45

QUOTE_CONTEXT_ENTRIES = 1

QUOTES_PLAIN = str.maketrans("‘’‚“”„", "'''\"\"\"")

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(description="Checks text subtitles made by OCR against their image subtitles; only ones next to an image subtitle, folders only English and second-language ones.")

# ------------------------------------------------------------------------------
# Rules
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class OcrFailedRule:
    kind: ClassVar[IssueKind] = IssueKind.OCR_FAILED
    enabled: bool

    def find_issues(self, check: "OcrCheck") -> Iterator[tuple["OcrEntry", str]]:
        for entry in check.entries:
            if entry.failed():
                yield entry, "the model gave no usable reply"


@dataclass(frozen=True)
class WrongOrderRule:
    kind: ClassVar[IssueKind] = IssueKind.WRONG_ORDER
    enabled: bool
    line_length_scale_min: int
    tolerance_em: float
    tolerance_share: float

    def find_issues(self, check: "OcrCheck") -> Iterator[tuple["OcrEntry", str]]:
        entries_read = [entry for entry in check.entries if not entry.failed()]

        # the font is the same in the whole file, so long lines with a line on the image each set the pixels per em
        ratios = [
            width / measure_em(line)
            for entry in entries_read
            if len(entry.image_lines.widths) == len(entry.lines)
            for width, line in zip(entry.image_lines.widths, entry.lines)
            if len(line) >= self.line_length_scale_min
        ]
        if not ratios:
            return
        pixels_per_em = statistics.median(ratios)

        for entry in entries_read:
            image = [width / pixels_per_em for width in entry.image_lines.widths]
            text = [measure_em(line) for line in entry.lines]
            if len(image) != len(text) or len(text) < 2:
                continue
            if self.fits(image, text[::-1]) and not self.fits(image, text):
                yield entry, "the image shows these lines in the other order"

    def fits(self, image: list[float], text: list[float]) -> bool:
        return all(abs(image_em - text_em) <= max(self.tolerance_em, self.tolerance_share * text_em) for image_em, text_em in zip(image, text))


@dataclass(frozen=True)
class InconsistentRule:
    kind: ClassVar[IssueKind] = IssueKind.INCONSISTENT
    enabled: bool

    def find_issues(self, check: "OcrCheck") -> Iterator[tuple["OcrEntry", str]]:
        # the same image must have the same text everywhere in the file
        entries_by_image: defaultdict[int, list[OcrEntry]] = defaultdict(list)
        for entry in check.entries:
            if not entry.failed():
                entries_by_image[entry.image_key].append(entry)
        for entries_same in entries_by_image.values():
            text_common = Counter(entry.text_compared for entry in entries_same).most_common(1)[0][0]
            reference = next(entry for entry in entries_same if entry.text_compared == text_common)
            for entry in entries_same:
                if entry.text_compared != text_common:
                    yield entry, f"entry {reference.subtitle.index} has the same image and reads \"{' / '.join(reference.lines)}\""


@dataclass(frozen=True)
class ArtifactRule:
    kind: ClassVar[IssueKind] = IssueKind.ARTIFACT
    MARKDOWN_EMPHASIS: ClassVar[re.Pattern[str]] = re.compile(r"(?<![\w*_])([*_])(\S(?:.*?\S)?)\1(?![\w*_])")
    ARTIFACT: ClassVar[re.Pattern[str]] = re.compile(r"[`{}\\<>�]|\bImage \d+:")
    TAG_UNEXPECTED: ClassVar[re.Pattern[str]] = re.compile(r"<(?!/?i>)[^<>]*>|\{(?!\\an8\})[^{}]*\}")
    enabled: bool

    def find_issues(self, check: "OcrCheck") -> Iterator[tuple["OcrEntry", str]]:
        for entry in check.entries:
            if entry.failed():
                continue
            if artifacts := [match[0] for pattern in (self.MARKDOWN_EMPHASIS, self.ARTIFACT) for match in pattern.finditer(entry.text)] + [match[0] for match in self.TAG_UNEXPECTED.finditer(entry.subtitle.content)]:
                yield entry, "markup in the text: " + ", ".join(f'"{artifact}"' for artifact in dict.fromkeys(artifacts))


@dataclass(frozen=True)
class GlyphConfusionRule:
    kind: ClassVar[IssueKind] = IssueKind.GLYPH_CONFUSION
    GLYPH_CONFUSION: ClassVar[re.Pattern[str]] = re.compile(r"\||[^\W\d_][015][^\W\d_]|\bl[A-Z]|\bl'(?:ll|m|ve|d|re)\b|(?<![^\s-])!(?=--|'(?:ll|m|ve|d|re)\b)|(?<!\S)---(?= )|\b[a-z]+I[a-z]*\b")
    enabled: bool

    def find_issues(self, check: "OcrCheck") -> Iterator[tuple["OcrEntry", str]]:
        for entry in check.entries:
            if entry.failed():
                continue
            if confusions := [match[0] for match in self.GLYPH_CONFUSION.finditer(entry.text)]:
                yield entry, "probably misread: " + ", ".join(f'"{confusion}"' for confusion in dict.fromkeys(confusions))


@dataclass(frozen=True)
class UnbalancedRule:
    kind: ClassVar[IssueKind] = IssueKind.UNBALANCED
    PAIRS: ClassVar[tuple[tuple[str, str], ...]] = (("(", ")"), ("[", "]"))
    NOT_BRACKET: ClassVar[re.Pattern[str]] = re.compile(r"[:;]-?[()]")
    LIST_NUMBER: ClassVar[re.Pattern[str]] = re.compile(r"^([^(\n]*?)(?<!\S)\d\)(?=\s)", re.MULTILINE)
    enabled: bool
    entries_max: int

    def find_issues(self, check: "OcrCheck") -> Iterator[tuple["OcrEntry", str]]:
        texts = [self.LIST_NUMBER.sub(r"\1", self.NOT_BRACKET.sub("", entry.text)) for entry in check.entries]
        for opening, closing in self.PAIRS:
            # a bracket may close up to entries_max - 1 entries later, as in a caption split over entries
            entries_open: list[int] = []
            unclosed: Counter[int] = Counter()
            unopened: Counter[int] = Counter()
            for i, text in enumerate(texts):
                for character in text:
                    if character == opening:
                        entries_open.append(i)
                    elif character == closing and entries_open:
                        entries_open.pop()
                    elif character == closing:
                        unopened[i] += 1
                while entries_open and entries_open[0] + self.entries_max <= i + 1:
                    unclosed[entries_open.pop(0)] += 1
            unclosed.update(entries_open)
            for i in sorted(unclosed.keys() | unopened.keys()):
                if unclosed[i]:
                    yield check.entries[i], f'{unclosed[i]} "{opening}" without "{closing}"'
                if unopened[i]:
                    yield check.entries[i], f'{unopened[i]} "{closing}" without "{opening}"'


OcrRule = OcrFailedRule | WrongOrderRule | InconsistentRule | ArtifactRule | GlyphConfusionRule | UnbalancedRule

RULES: tuple[OcrRule, ...] = (
    OcrFailedRule(enabled=True),
    WrongOrderRule(enabled=True, line_length_scale_min=10, tolerance_em=2.5, tolerance_share=0.3),
    InconsistentRule(enabled=True),
    ArtifactRule(enabled=True),
    GlyphConfusionRule(enabled=True),
    UnbalancedRule(enabled=True, entries_max=3),
)

# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class ImageLines:
    widths: tuple[int, ...]

    @classmethod
    def from_entry(cls, entry: SubtitleEntry) -> "ImageLines":
        # rows with text fill form bands; a band much shorter than the tallest is an accent or a mark of the nearest line
        text = ImageOps.invert(entry.to_black_on_white())
        bands: list[list[int]] = []
        for y, filled in enumerate(text.getprojection()[1]):
            if not filled:
                continue
            if bands and bands[-1][1] == y:
                bands[-1][1] = y + 1
            else:
                bands.append([y, y + 1])
        if not bands:
            return cls(widths=())
        tallest = max(end - start for start, end in bands)
        lines = [band for band in bands if band[1] - band[0] >= LINE_HEIGHT_MIN * tallest]
        for start, end in bands:
            if end - start >= LINE_HEIGHT_MIN * tallest:
                continue
            nearest = min(lines, key=lambda line: max(line[0] - end, start - line[1]))
            nearest[0], nearest[1] = min(nearest[0], start), max(nearest[1], end)

        widths: list[int] = []
        for start, end in lines:
            columns = [x for x, filled in enumerate(text.crop((0, start, text.width, end)).getprojection()[0]) if filled]
            widths.append(columns[-1] - columns[0] + 1)
        return cls(widths=tuple(widths))


@dataclass(frozen=True)
class OcrEntry:
    subtitle: srt.Subtitle
    lines: tuple[str, ...]
    image_lines: ImageLines
    image_key: int

    @classmethod
    def from_entries(cls, subtitle: srt.Subtitle, image: SubtitleEntry) -> "OcrEntry":
        return cls(
            subtitle=subtitle,
            lines=tuple(line for line in parse_plain_text(subtitle).split("\n") if line),
            image_lines=ImageLines.from_entry(image),
            image_key=image.image_key,
        )

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def text_compared(self) -> str:
        return "".join(self.text.split()).translate(QUOTES_PLAIN)

    def failed(self) -> bool:
        return OCR_FAILED_TEXT in self.text


@dataclass
class OcrCheck:
    subtitle: Path
    entries: list[OcrEntry]

    @classmethod
    def from_file(cls, subtitle: SubtitleFile) -> "OcrCheck":
        image_file = subtitle.find_image_subtitle()
        if image_file is None:
            raise FileNotFoundError(f"image subtitle not found next to {subtitle.name}")
        image = image_file.path
        texts = parse_srt(subtitle.path)

        # srt drops entries without duration when it writes a subtitle, so they are dropped here too
        images = [entry for entry in parse_image_subtitle(image) if entry.end > entry.start]
        if len(images) != len(texts):
            raise ValueError(f"{len(images)} entries in {image.name}, {len(texts)} in the text subtitle")
        shifted = next((text.index for text, entry in zip(texts, images) if abs(text.start - entry.start) > ENTRY_START_TOLERANCE), None)
        if shifted is not None:
            raise ValueError(f"entry {shifted} starts at a different time than in {image.name}")
        return cls(subtitle=subtitle.path, entries=[OcrEntry.from_entries(text, entry) for text, entry in zip(texts, images)])

    def quote(self, entry: "OcrEntry") -> tuple[str, ...]:
        return quote_entries([(entry_listed.subtitle.index, entry_listed.subtitle.content.split("\n")) for entry_listed in self.entries], self.entries.index(entry), QUOTE_CONTEXT_ENTRIES)

    def to_report(self) -> Report:
        issues: list[Issue] = []
        for rule in RULES:
            if not rule.enabled:
                continue
            issues += [Issue(entry=entry.subtitle, kind=rule.kind, detail=detail, quote=self.quote(entry)) for entry, detail in rule.find_issues(self)]
        return Report.from_issues(subtitle=self.subtitle, entries_count=len(self.entries), issues=issues)

# ------------------------------------------------------------------------------
# Functions
# ------------------------------------------------------------------------------
def measure_em(text: str) -> float:
    return FONT.getlength(text) / FONT_SIZE


def check_subtitle(subtitle: SubtitleFile) -> ReportScore:
    return OcrCheck.from_file(subtitle).to_report().write(subtitle.to_report())

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = [
        sub
        for sub in find_subtitles(create_parser(), formats=(SubtitleFormat.TEXT,), languages=LANGUAGES_OCR)
        if sub.find_image_subtitle() is not None
    ]
    scores_current = {sub: score for sub in subs if (score := ReportScore.find(sub.to_report())) is not None}
    scores = list(scores_current.values())

    with Batch() as batch, cancel_on_interrupt(ProcessPoolExecutor(max_workers=NAS_WORKERS)) as executor:
        # check every sub without a report, NAS_WORKERS at a time across movies
        checks: dict[SubtitleFile, Future[ReportScore]] = {sub: executor.submit(check_subtitle, sub) for sub in subs if sub not in scores_current}

        # report every pending sub of a movie as its check arrives, skipping subs with a report
        for sub, check in batch.walk_ahead(subs, checks):
            if check is None:
                Action.EXISTS.log(scores_current[sub].describe())
                continue

            with batch.attempt(sub.path):
                score = check.result()
                Action.CHECK.log(score.describe())
                scores.append(score)

        log_ranking(scores)
