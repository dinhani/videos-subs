# Checks the text subtitles made by OCR against their image subtitles and writes a report of each one to _checks, skipping ones with a current report.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import itertools
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import srt
from PIL import ImageFont, ImageOps
from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn

from checks import CHECK_WORKERS, CHECKS_FOLDER, Issue, IssueKind, Report, ReportScore, list_folder, log_ranking, parse_plain_text
from shared import IMAGE_EXTENSIONS, SUBS_FOLDER, Batch, Color, SubtitleEntry, find_files, log_action, parse_image_subtitle, parse_movie_dir, parse_srt, progress_bar

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
LANGUAGES = ("eng",)

OCR_FAILED_TEXT = "[OCR failed]"
ENTRY_START_TOLERANCE = timedelta(milliseconds=2)

FONT_SIZE = 100
FONT = ImageFont.truetype("arial.ttf", FONT_SIZE)
LINE_HEIGHT_MIN = 0.45
LINE_LENGTH_SCALE_MIN = 10
LINE_TOLERANCE_EM = 2.5
LINE_TOLERANCE_SHARE = 0.3

MARKDOWN_EMPHASIS = re.compile(r"(?<![\w*_])([*_])(\S(?:.*?\S)?)\1(?![\w*_])")
ARTIFACT = re.compile(r"[`{}\\<>�]|\bImage \d+:")
TAG_UNEXPECTED = re.compile(r"<(?!/?i>)[^<>]*>|\{(?!\\an8\})[^{}]*\}")
GLYPH_CONFUSION = re.compile(r"\||[^\W\d_][015][^\W\d_]|\bl[A-Z]|\bl'(?:ll|m|ve|d|re)\b|\b[a-z]+I[a-z]*\b")
PAIRS = (("(", ")"), ("[", "]"))
QUOTES_PLAIN = str.maketrans("‘’‚“”„", "'''\"\"\"")
NOT_BRACKET = re.compile(r"[:;]-?[()]")
LIST_NUMBER = re.compile(r"^([^(\n]*?)(?<!\S)\d\)(?=\s)", re.MULTILINE)
BRACKET_ENTRIES_MAX = 3

# ------------------------------------------------------------------------------
# Checks
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

    def reads_reversed(self, pixels_per_em: float) -> bool:
        image = [width / pixels_per_em for width in self.image_lines.widths]
        text = [measure_em(line) for line in self.lines]
        if len(image) != len(text) or len(text) < 2:
            return False
        fits = all(measure_excess(image_em, text_em) <= 0 for image_em, text_em in zip(image, text))
        fits_reversed = all(measure_excess(image_em, text_em) <= 0 for image_em, text_em in zip(image, reversed(text)))
        return fits_reversed and not fits


@dataclass
class OcrCheck:
    subtitle: Path
    entries: list[OcrEntry]
    issues: list[Issue] = field(default_factory=list)

    @classmethod
    def from_file(cls, subtitle: Path) -> "OcrCheck":
        image = next(subtitle.with_suffix(extension) for extension in IMAGE_EXTENSIONS if subtitle.with_suffix(extension).exists())
        texts = parse_srt(subtitle)

        # srt drops entries without duration when it writes a subtitle, so they are dropped here too
        images = [entry for entry in parse_image_subtitle(image) if entry.end > entry.start]
        if len(images) != len(texts):
            raise ValueError(f"{len(images)} entries in {image.name}, {len(texts)} in the text subtitle")
        shifted = next((text.index for text, entry in zip(texts, images) if abs(text.start - entry.start) > ENTRY_START_TOLERANCE), None)
        if shifted is not None:
            raise ValueError(f"entry {shifted} starts at a different time than in {image.name}")
        return cls(subtitle=subtitle, entries=[OcrEntry.from_entries(text, entry) for text, entry in zip(texts, images)])

    def flag(self, entry: OcrEntry, kind: IssueKind, detail: str) -> None:
        self.issues.append(Issue(entry=entry.subtitle, kind=kind, detail=detail, quote=tuple(entry.subtitle.content.split("\n"))))

    def check_entries(self) -> None:
        for entry in self.entries:
            if entry.failed():
                self.flag(entry, IssueKind.OCR_FAILED, "the model gave no usable reply")
                continue
            if artifacts := [match[0] for pattern in (MARKDOWN_EMPHASIS, ARTIFACT) for match in pattern.finditer(entry.text)] + [match[0] for match in TAG_UNEXPECTED.finditer(entry.subtitle.content)]:
                self.flag(entry, IssueKind.ARTIFACT, "markup in the text: " + ", ".join(f'"{artifact}"' for artifact in dict.fromkeys(artifacts)))
            if confusions := [match[0] for match in GLYPH_CONFUSION.finditer(entry.text)]:
                self.flag(entry, IssueKind.GLYPH_CONFUSION, "probably misread: " + ", ".join(f'"{confusion}"' for confusion in dict.fromkeys(confusions)))

    def check_brackets(self) -> None:
        texts = [LIST_NUMBER.sub(r"\1", NOT_BRACKET.sub("", entry.text)) for entry in self.entries]
        for opening, closing in PAIRS:
            # a bracket may close up to BRACKET_ENTRIES_MAX - 1 entries later, as in a caption split over entries
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
                while entries_open and entries_open[0] + BRACKET_ENTRIES_MAX <= i + 1:
                    unclosed[entries_open.pop(0)] += 1
            unclosed.update(entries_open)
            for i in sorted(unclosed.keys() | unopened.keys()):
                if unclosed[i]:
                    self.flag(self.entries[i], IssueKind.UNBALANCED, f'{unclosed[i]} "{opening}" without "{closing}"')
                if unopened[i]:
                    self.flag(self.entries[i], IssueKind.UNBALANCED, f'{unopened[i]} "{closing}" without "{opening}"')

    def check_images(self) -> None:
        entries_read = [entry for entry in self.entries if not entry.failed()]

        # the font is the same in the whole file, so long lines with a line on the image each set the pixels per em
        ratios = [
            width / measure_em(line)
            for entry in entries_read
            if len(entry.image_lines.widths) == len(entry.lines)
            for width, line in zip(entry.image_lines.widths, entry.lines)
            if len(line) >= LINE_LENGTH_SCALE_MIN
        ]
        if ratios:
            pixels_per_em = statistics.median(ratios)
            for entry in entries_read:
                if entry.reads_reversed(pixels_per_em):
                    self.flag(entry, IssueKind.WRONG_ORDER, "the image shows these lines in the other order")

        # the same image must have the same text everywhere in the file
        entries_by_image: defaultdict[int, list[OcrEntry]] = defaultdict(list)
        for entry in entries_read:
            entries_by_image[entry.image_key].append(entry)
        for entries_same in entries_by_image.values():
            text_common = Counter(entry.text_compared for entry in entries_same).most_common(1)[0][0]
            reference = next(entry for entry in entries_same if entry.text_compared == text_common)
            for entry in entries_same:
                if entry.text_compared != text_common:
                    self.flag(entry, IssueKind.INCONSISTENT, f"entry {reference.subtitle.index} has the same image and reads \"{' / '.join(reference.lines)}\"")

    def to_report(self) -> Report:
        return Report(subtitle=self.subtitle, entries_count=len(self.entries), issues=self.issues)


def measure_em(text: str) -> float:
    return FONT.getlength(text) / FONT_SIZE


def measure_excess(image_em: float, text_em: float) -> float:
    return abs(image_em - text_em) - max(LINE_TOLERANCE_EM, LINE_TOLERANCE_SHARE * text_em)


def check_subtitle(subtitle: Path, report: Path) -> ReportScore:
    check = OcrCheck.from_file(subtitle)
    check.check_entries()
    check.check_brackets()
    check.check_images()
    return check.to_report().write(report)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = [
        sub
        for sub in find_files("Checks text subtitles made by OCR against their image subtitles; only ones next to an image subtitle, folders only in the wanted languages.", extensions_file=(".srt",), extensions_folder=tuple(f".{language}.srt" for language in LANGUAGES), folder=SUBS_FOLDER)
        if any(f"{sub.stem}{extension}" in list_folder(sub.parent) for extension in IMAGE_EXTENSIONS)
    ]
    reports = {sub: parse_movie_dir(sub) / CHECKS_FOLDER / f"{sub.stem}.ocr.txt" for sub in subs}
    scores_current = {sub: ReportScore.find(reports[sub]) for sub in subs}
    batch = Batch()
    scores = [score for score in scores_current.values() if score is not None]

    with ProcessPoolExecutor(max_workers=CHECK_WORKERS) as executor:
        # check every sub without a report, CHECK_WORKERS at a time across movies
        checks: dict[Path, Future[ReportScore]] = {sub: executor.submit(check_subtitle, sub, reports[sub]) for sub in subs if scores_current[sub] is None}

        try:
            for movie_dir, movie_subs in itertools.groupby(subs, key=parse_movie_dir):
                batch.log_movie(movie_dir)

                # skip subs with a report
                movie_subs_pending: list[Path] = []
                for sub in movie_subs:
                    score = scores_current[sub]
                    if score is not None:
                        log_action("exists", Color.BLUE, score.describe())
                        continue
                    movie_subs_pending.append(sub)
                if not movie_subs_pending:
                    continue

                # report every pending sub of the movie as its check arrives, showing progress
                with progress_bar(MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn()) as progress:
                    for sub in progress.track(movie_subs_pending):
                        try:
                            score = checks[sub].result()
                            log_action("check", score.color, score.describe())
                            scores.append(score)
                        except Exception as error:
                            batch.fail(sub, error)
        except KeyboardInterrupt:
            executor.shutdown(cancel_futures=True)
            raise

    log_ranking(scores)
    batch.finish()
