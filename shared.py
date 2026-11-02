import argparse
import base64
import functools
import io
import itertools
import json
import os
import re
import sys
import threading
import unicodedata
from collections import Counter
from collections.abc import Buffer
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, cast

import av
import simplemma
import srt
from av.subtitles.subtitle import BitmapSubtitle, SubtitleSet
from lingua import Language, LanguageDetectorBuilder
from openai import OpenAI
from PIL import Image
from rich.console import Console
from rich.progress import BarColumn, Progress, ProgressColumn, TextColumn
from rich.style import Style
from rich.text import Text
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed
from wordfreq import zipf_frequency

DIR_FILMES = Path("Z:/midias/filmes/")
DIR_SERIES = Path("Z:/midias/series/")
DIRS_MIDIAS = (DIR_FILMES, DIR_SERIES)

SUBS_FOLDER = "_subs"
TRACES_FOLDER = "_traces"
CHECKS_FOLDER = "_checks"

DATA_FOLDER = Path(__file__).parent / "data"
DICTIONARY_HUNSPELL = Path(os.environ["LOCALAPPDATA"]) / "hunspell" / "pt_BR"
CHECK_WORDS = DATA_FOLDER / "check-words.txt"
CHECK_EXPRESSIONS = DATA_FOLDER / "check-expressions.txt"
FIX_SOUNDS = DATA_FOLDER / "fix-sounds.txt"
FIX_SUBJECTS = DATA_FOLDER / "fix-subjects.txt"
FIX_WORDS = DATA_FOLDER / "fix-words.txt"

REPLACE_ATTEMPTS = 10
REPLACE_WAIT_SECONDS = 0.5

LLM_URL = "http://127.0.0.1:9931/v1"
LLM_PROPS_URL = "http://127.0.0.1:9931/props"

IMAGE_EXTENSIONS = (".sup", ".mks")
IMAGE_PADDING = 8
IMAGE_BACKGROUND = 96
IMAGE_SMALL_WIDTH = 1280

ENTRY_LAST_DURATION = timedelta(seconds=5)
ENTRY_UNTIL_NEXT = timedelta.max
END_DISPLAY_UNDEFINED = 0xFFFFFFFF

PGS_PRESENTATION = 0x16
PGS_PALETTE = 0x14
PGS_SD_HEIGHT_MAX = 576
YCBCR_BT601 = (1.596, 0.392, 0.813, 2.017)
YCBCR_BT709 = (1.793, 0.213, 0.533, 2.112)

VOBSUB_SET_COLOR = 0x03
VOBSUB_SET_ALPHA = 0x04
VOBSUB_CHANGE_COLOR = 0x07
VOBSUB_END = 0xFF
VOBSUB_ARGUMENT_SIZES = {0x00: 0, 0x01: 0, 0x02: 0, VOBSUB_SET_COLOR: 2, VOBSUB_SET_ALPHA: 2, 0x05: 6, 0x06: 4}

FORMATTING_TAGS = re.compile(r"<[^>]+>|\{[^}]*\}")
IGNORE_MARKER = re.compile(r"\s*\bignore:(\S+)")

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

WORDS_PT_PT = re.compile(
    r"\b(autocarro|telemóvel|ecrã|chávena|gaj[oa]|fixe|"
    r"pequeno-almoço|casa de banho|betão|autoclismo|sandes|boleia|talho|agrafador|"
    r"est(?:ou|ás|á|amos|ão|ava|avas|ávamos|avam) a [a-záàâãéêíóôõúç]+r)\b",
    re.IGNORECASE,
)
MARKERS_PT_PT = re.compile(
    r"\b(tu|teus?|tuas?|contigo|vós|voss[oa]s?|"
    r"tens|queres|podes|sabes|vais|és|estás|fazes|dizes|vês|"
    r"factos?|actos?|óptim[oa]s?|acç(?:ão|ões)|direcç(?:ão|ões)|connosco|registo|bebé|génio|fenómeno|económic[oa]s?)\b",
    re.IGNORECASE,
)
MARKERS_PT_BR = re.compile(
    r"\b(vocês?|conosco|registro|bebê|gênio|fenômeno|econômic[oa]s?|"
    r"est(?:ou|ás|á|amos|ão|ava|avas|ávamos|avam|ive|eve|ivemos|iveram) [a-záàâãéêíóôõúç]+ndo)\b",
    re.IGNORECASE,
)
MARKERS_PT_PT_MIN = 3

TRANSLATION_LANGUAGE_CODE = "pob"

SOUND_DESCRIPTION = re.compile(r"\[([^\]]+)\]|\(([^)]+)\)")
DESCRIPTION_SIMPLE = re.compile(r"\[([^\[\]()<>{}]+)\]|\(([^\[\]()<>{}]+)\)")
DESCRIPTION_SUBJECT_WORDS_MAX = 2
DESCRIPTION_PUNCTUATION = re.compile(r"[\[\]()\-–—]")
DESCRIPTION_ARTICLE = re.compile(r"^(?:o|a|os|as)\s+")
SDH_ENTRIES_DESCRIBED_MIN = 0.05
SDH_DESCRIPTIONS_TOP = 3
SDH_DESCRIPTIONS_TOP_MAX = 0.60

LANGUAGE_CODES = {language.iso_code_639_3.name.lower() for language in Language.all()} | {"pob", "und"}

language_detector = LanguageDetectorBuilder.from_all_languages().build()


class Color(StrEnum):
    BLUE = "blue"
    CYAN = "cyan"
    GREEN = "green"
    RED = "red"
    YELLOW = "yellow"


class ScreenPosition(StrEnum):
    TOP = "{\\an8}"
    BOTTOM = ""


class SubtitleKind(StrEnum):
    BASE = ""
    SDH = ".sdh"

    @classmethod
    def from_file(cls, file: Path) -> "SubtitleKind":
        return cls.SDH if Path(file.stem).suffix == cls.SDH else cls.BASE

    @classmethod
    def find_in_translation(cls, file: Path) -> "SubtitleKind | None":
        return next((kind for kind in cls if file.name.endswith(kind.to_translation_suffix())), None)

    def to_translation_suffix(self) -> str:
        return f".{TRANSLATION_LANGUAGE_CODE}{self}.translated.srt"

    @classmethod
    def from_texts(cls, texts: list[str]) -> "SubtitleKind":
        texts = [FORMATTING_TAGS.sub("", text) for text in texts]
        entries_described = sum(1 for text in texts if SOUND_DESCRIPTION.search(text))
        if not texts or entries_described < SDH_ENTRIES_DESCRIBED_MIN * len(texts):
            return cls.BASE

        descriptions = Counter(" ".join((match[1] or match[2]).lower().split()) for text in texts for match in SOUND_DESCRIPTION.finditer(text))
        descriptions_top = sum(count for _, count in descriptions.most_common(SDH_DESCRIPTIONS_TOP))
        return cls.SDH if descriptions_top < SDH_DESCRIPTIONS_TOP_MAX * descriptions.total() else cls.BASE


console = Console(highlight=False, markup=False)


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
        return any(self.marker in match[1].split(",") for match in IGNORE_MARKER.finditer(entry.proprietary or ""))


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


def find_files(description: str, extensions_file: tuple[str, ...], extensions_folder: tuple[str, ...], folder: str | None = None, parser: argparse.ArgumentParser | None = None) -> list[Path]:
    parser = parser or argparse.ArgumentParser(description=description)
    parser.add_argument("paths", nargs="*", type=Path, help=f"folders (scanned for {'/'.join(extensions_folder)}) or {'/'.join(extensions_file)} files (default: {', '.join(map(str, DIRS_MIDIAS))})")
    paths: list[Path] = parser.parse_args().paths or list(DIRS_MIDIAS)

    files: list[Path] = []
    for path in paths:
        if path.is_file():
            if not path.name.lower().endswith(extensions_file):
                parser.error(f"not a {'/'.join(extensions_file)} file: {path}")
            files.append(path)
        elif path.is_dir():
            files += sorted(directory / name for directory, _, names in path.walk() if folder is None or directory.name == folder for name in names if name.lower().endswith(extensions_folder))
        else:
            parser.error(f"path not found: {path}")
    return files


def parse_movie_dir(file: Path) -> Path:
    return file.parent.parent if file.parent.name == SUBS_FOLDER else file.parent


def find_language_code(file: Path) -> str | None:
    stem = Path(file.stem)
    if stem.suffix == SubtitleKind.SDH:
        stem = Path(stem.stem)
    code = stem.suffix.removeprefix(".")
    return code if code in LANGUAGE_CODES else None


def parse_subtitle_name(file: Path) -> str:
    stem = Path(file.stem)
    if find_language_code(file) is None:
        return stem.name
    if stem.suffix == SubtitleKind.SDH:
        stem = Path(stem.stem)
    return stem.stem


def parse_srt(file: Path) -> list[srt.Subtitle]:
    return list(srt.parse(file.read_text(encoding="utf-8-sig", errors="replace"), ignore_errors=True))


@functools.cache
def parse_pairs(file: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(" = ")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"{file.name}: not a 'key = value' line: {line!r}")
        pairs[key.strip()] = value.strip()
    return pairs


def normalize_description(text: str) -> str:
    return " ".join(DESCRIPTION_PUNCTUATION.sub(" ", FORMATTING_TAGS.sub("", text)).lower().split())


def match_case(text: str, source: str) -> str:
    if source.isupper():
        return text.upper()
    if source[:1].isupper():
        return text[:1].upper() + text[1:]
    return text


def parse_plain_text(entry: srt.Subtitle) -> str:
    return "\n".join(" ".join(line.split()) for line in FORMATTING_TAGS.sub("", entry.content).split("\n") if line.strip())


def parse_words(text: str) -> list["Word"]:
    return [Word(text=match[0], name=match[0][0].isupper() and SENTENCE_START.search(text, 0, match.start()) is None) for match in WORD.finditer(text)]


def parse_image_subtitle(file: Path, entries_max: int | None = None) -> list["SubtitleEntry"]:
    with av.open(str(file)) as container:
        stream = container.streams.subtitles[0]
        decoder = stream.codec_context
        screen = PgsScreen() if decoder.name == "pgssub" else VobSubScreen.from_header(decoder.extradata or b"")

        # every decoded subtitle set replaces what is on screen: an entry lasts until the next set or its own end time, whichever comes first
        entries: list[SubtitleEntry] = []
        shown: SubtitleEntry | None = None
        for packet in container.demux(stream):
            if packet.size == 0:
                continue
            screen.read_packet(bytes(packet))
            subtitle_set = decoder.decode2(packet)
            if subtitle_set is None:
                continue
            start = timedelta(microseconds=subtitle_set.pts, milliseconds=subtitle_set.start_display_time)
            entry = SubtitleEntry.from_subtitle_set(subtitle_set, screen, start=start)
            if entry is not None and shown is not None and start < shown.end and entry.image == shown.image and entry.position == shown.position:
                continue
            if shown is not None:
                entries.append(shown.end_at(min(shown.end, start)))
                if len(entries) == entries_max:
                    return entries
            shown = entry
        if shown is not None:
            entries.append(shown.end_at(shown.start + ENTRY_LAST_DURATION) if shown.end == ENTRY_UNTIL_NEXT else shown)
        return entries


@functools.cache
def list_folder(folder: Path) -> frozenset[str]:
    return frozenset(os.listdir(folder))


def write_text_replacing(file: Path, text: str, newline: str | None = None) -> None:
    file_part = file.with_name(f"{file.name}.part")
    try:
        file_part.write_text(text, encoding="utf-8", newline=newline)
        for attempt in Retrying(retry=retry_if_exception_type(PermissionError), stop=stop_after_attempt(REPLACE_ATTEMPTS), wait=wait_fixed(REPLACE_WAIT_SECONDS), reraise=True):
            with attempt:
                file_part.replace(file)
    finally:
        file_part.unlink(missing_ok=True)


def connect_llama() -> OpenAI:
    llama = OpenAI(base_url=LLM_URL, api_key="none")
    try:
        llama.models.list()
    except Exception as error:
        log_action("failed", Color.RED, f"llama server unreachable at {LLM_URL}: {error}")
        sys.exit(1)
    return llama


def log_action(action: str, color: Color, details: str | Text) -> None:
    console.print(Text.assemble("  ", (f"{action:<9}", color), " ", details))


def log_ranking(scores: list["ReportScore"]) -> None:
    if not scores:
        return
    console.print()
    console.print("Worst scores (weighted issues per 100 entries):", style=Color.CYAN)
    for score in sorted(scores, key=lambda score: score.score, reverse=True)[:RANKING_SIZE]:
        log_action("score", score.color, score.describe())


def progress_bar(*columns: ProgressColumn) -> Progress:
    return Progress(
        TextColumn("  "),
        BarColumn(bar_width=40, style=Color.BLUE, complete_style=Color.GREEN, finished_style=Color.GREEN),
        *columns,
        console=console,
        transient=True,
    )


@dataclass(frozen=True)
class SubtitleLanguage:
    code: str
    name: str

    @classmethod
    def from_text(cls, text: str) -> "SubtitleLanguage":
        text = FORMATTING_TAGS.sub(" ", text)
        language = language_detector.detect_language_of(text)
        if language is None:
            return cls(code="und", name="unknown")
        if language != Language.PORTUGUESE:
            return cls(code=language.iso_code_639_3.name.lower(), name=language.name.lower())

        markers_portugal = len(WORDS_PT_PT.findall(text)) + len(MARKERS_PT_PT.findall(text))
        markers_brazil = len(MARKERS_PT_BR.findall(text))
        if markers_portugal >= MARKERS_PT_PT_MIN and markers_portugal > markers_brazil:
            return cls(code="por", name="portuguese (portugal)")
        return cls(code="pob", name="portuguese (brazil)")


@dataclass(frozen=True)
class SubtitleEntry:
    start: timedelta
    end: timedelta
    image: Image.Image
    position: ScreenPosition

    @classmethod
    def from_subtitle_set(cls, subtitle_set: SubtitleSet, screen: "PgsScreen | VobSubScreen", start: timedelta) -> "SubtitleEntry | None":
        # paint the bitmaps of the set on one canvas and keep only its visible pixels
        bitmaps = [bitmap for bitmap in subtitle_set if isinstance(bitmap, BitmapSubtitle) and bitmap.width and bitmap.height]
        if not bitmaps:
            return None
        canvas_left = min(bitmap.x for bitmap in bitmaps)
        canvas_top = min(bitmap.y for bitmap in bitmaps)
        canvas = Image.new("RGBA", (max(bitmap.x + bitmap.width for bitmap in bitmaps) - canvas_left, max(bitmap.y + bitmap.height for bitmap in bitmaps) - canvas_top))
        for bitmap in bitmaps:
            indexes = Image.frombytes("P", (bitmap.width, bitmap.height), bytes(cast(Buffer, bitmap.planes[0])))
            indexes.putpalette(screen.palette, rawmode="RGBA")
            canvas.alpha_composite(indexes.convert("RGBA"), (bitmap.x - canvas_left, bitmap.y - canvas_top))
        box = canvas.getbbox()
        if box is None:
            return None

        # pad the visible pixels on a gray background
        left, top, right, bottom = box
        image = Image.new("RGBA", (right - left + 2 * IMAGE_PADDING, bottom - top + 2 * IMAGE_PADDING), (IMAGE_BACKGROUND, IMAGE_BACKGROUND, IMAGE_BACKGROUND, 255))
        image.alpha_composite(canvas.crop(box), (IMAGE_PADDING, IMAGE_PADDING))
        if screen.width < IMAGE_SMALL_WIDTH:
            image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
        position = ScreenPosition.TOP if canvas_top + bottom < screen.height / 2 else ScreenPosition.BOTTOM
        end = ENTRY_UNTIL_NEXT if subtitle_set.end_display_time == END_DISPLAY_UNDEFINED else start + timedelta(milliseconds=subtitle_set.end_display_time)
        return cls(start=start, end=end, image=image.convert("RGB"), position=position)

    def end_at(self, end: timedelta) -> "SubtitleEntry":
        return replace(self, end=end)

    @property
    def image_key(self) -> int:
        return hash((self.image.size, self.image.tobytes()))

    def to_data_url(self) -> str:
        buffer = io.BytesIO()
        self.image.save(buffer, format="PNG")
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode('ascii')}"

    def to_black_on_white(self) -> Image.Image:
        gray = self.image.convert("L")
        brightest = max(value for value, count in enumerate(gray.histogram()) if count)
        threshold = (IMAGE_BACKGROUND + brightest) / 2
        return gray.point([0 if value > threshold else 255 for value in range(256)])


@dataclass
class PgsScreen:
    width: int = 0
    height: int = 0
    palette_id: int = 0
    palettes: dict[int, dict[int, tuple[int, int, int, int]]] = field(default_factory=dict)

    @property
    def palette(self) -> bytes:
        coefficients = YCBCR_BT601 if self.height <= PGS_SD_HEIGHT_MAX else YCBCR_BT709
        palette = bytearray(256 * 4)
        for index, color in self.palettes.get(self.palette_id, {}).items():
            palette[index * 4:index * 4 + 4] = parse_pgs_color(color, coefficients)
        return bytes(palette)

    def read_packet(self, segment: bytes) -> None:
        segment_type, payload = segment[0], segment[3:]
        if segment_type == PGS_PRESENTATION:
            self.width = int.from_bytes(payload[0:2])
            self.height = int.from_bytes(payload[2:4])
            self.palette_id = payload[9]
        elif segment_type == PGS_PALETTE:
            colors = self.palettes.setdefault(payload[0], {})
            for index, y, cr, cb, alpha in itertools.batched(payload[2:], 5):
                colors[index] = (y, cr, cb, alpha)


@functools.cache
def parse_pgs_color(color: tuple[int, int, int, int], coefficients: tuple[float, float, float, float]) -> bytes:
    y, cr, cb, alpha = color
    cr_red, cb_green, cr_green, cb_blue = coefficients
    luma = 1.164 * (y - 16)
    rgb = (luma + cr_red * (cr - 128), luma - cb_green * (cb - 128) - cr_green * (cr - 128), luma + cb_blue * (cb - 128))
    return bytes([*(max(0, min(255, round(value))) for value in rgb), alpha])


@dataclass
class VobSubScreen:
    width: int
    height: int
    colors: list[bytes]
    palette: bytes = bytes(4 * 4)

    @classmethod
    def from_header(cls, header: bytes) -> "VobSubScreen":
        text = header.decode("ascii", errors="replace")
        size = re.search(r"^size:\s*(\d+)x(\d+)", text, re.MULTILINE)
        palette = re.search(r"^palette:\s*(.+)$", text, re.MULTILINE)
        if size is None or palette is None:
            raise ValueError("VobSub header without size or palette")
        return cls(width=int(size[1]), height=int(size[2]), colors=[bytes.fromhex(color.strip()) for color in palette[1].split(",")])

    def read_packet(self, packet: bytes) -> None:
        # the control sequences of the packet pick 4 of the 16 header colors and their alpha, one per pixel code
        color_indexes = [0, 0, 0, 0]
        alphas = [0, 0, 0, 0]
        sequence = int.from_bytes(packet[2:4])
        while True:
            sequence_next = int.from_bytes(packet[sequence + 2:sequence + 4])
            position = sequence + 4
            while position < len(packet) and packet[position] != VOBSUB_END:
                command = packet[position]
                position += 1
                if command == VOBSUB_SET_COLOR:
                    color_indexes = [packet[position + 1] & 0x0F, packet[position + 1] >> 4, packet[position] & 0x0F, packet[position] >> 4]
                elif command == VOBSUB_SET_ALPHA:
                    alphas = [packet[position + 1] & 0x0F, packet[position + 1] >> 4, packet[position] & 0x0F, packet[position] >> 4]
                if command == VOBSUB_CHANGE_COLOR:
                    position += int.from_bytes(packet[position:position + 2])
                elif command in VOBSUB_ARGUMENT_SIZES:
                    position += VOBSUB_ARGUMENT_SIZES[command]
                else:
                    break
            if sequence_next <= sequence or sequence_next >= len(packet):
                break
            sequence = sequence_next
        self.palette = b"".join(self.colors[index] + bytes([alpha * 17]) for index, alpha in zip(color_indexes, alphas))


@dataclass(frozen=True)
class Word:
    text: str
    name: bool

    @property
    def key(self) -> str:
        return "".join(character for character in unicodedata.normalize("NFKD", self.text.lower().replace("’", "'")) if not unicodedata.combining(character))


@dataclass(frozen=True)
class DescriptionTranslation:
    source: str
    text: str
    name: str
    forms: tuple[frozenset[str], ...]

    def is_confirmed_by(self, translation: str, names: frozenset[str]) -> bool:
        if not self.name:
            return True
        words = normalize_description(translation)
        name = normalize_description(self.name)
        return all(word in names for word in name.split()) and words != normalize_description(self.source) and f" {name} " in f" {words} "

    def is_already_in(self, translation: str) -> bool:
        parts = [DESCRIPTION_ARTICLE.sub("", part.strip()) for part in normalize_description(translation).split(",")]
        return len(parts) == len(self.forms) and all(part in forms for part, forms in zip(parts, self.forms))


@dataclass(frozen=True)
class DescriptionTable:
    sounds: dict[str, tuple[str, ...]]
    subjects: dict[str, str]

    @classmethod
    def from_files(cls, sounds: Path, subjects: Path) -> "DescriptionTable":
        return cls(sounds={key: tuple(form.strip() for form in value.split("|")) for key, value in parse_pairs(sounds).items()}, subjects=parse_pairs(subjects))

    def find_translation(self, description: str) -> DescriptionTranslation | None:
        words = description.split()
        description = " ".join(words)
        key = " ".join(words).lower()
        if key in self.sounds:
            return DescriptionTranslation(source=description, text=match_case(self.sounds[key][0], description), name="", forms=(self.to_forms(self.sounds[key], ""),))

        # a list such as GRUNTS, GASPS: the first part may have a subject or a name, the others are phrases
        if "," in description:
            first, *others = (part.strip() for part in description.split(","))
            translation = self.find_translation(first)
            others_keys = [" ".join(other.split()).lower() for other in others]
            if translation is None or not all(other_key in self.sounds for other_key in others_keys):
                return None
            texts = [translation.text, *(match_case(self.sounds[other_key][0], other) for other_key, other in zip(others_keys, others))]
            forms = (*translation.forms, *(self.to_forms(self.sounds[other_key], "") for other_key in others_keys))
            return DescriptionTranslation(source=description, text=", ".join(texts), name=translation.name, forms=forms)

        # a subject or a name, then a phrase led by a verb in -ing or -s
        for subject_size in range(1, min(DESCRIPTION_SUBJECT_WORDS_MAX, len(words) - 1) + 1):
            subject, phrase = words[:subject_size], words[subject_size:]
            phrase_key = " ".join(phrase).lower()
            verb = phrase[0].lower()
            if phrase_key not in self.sounds or not verb.endswith(("ing", "s")):
                continue
            subject_key = " ".join(subject).lower()
            if subject_key in self.subjects:
                subject_portuguese = self.subjects[subject_key]
                if not verb.endswith("ing") and subject_portuguese.endswith("s"):
                    continue
                text = match_case(f"{subject_portuguese} {self.sounds[phrase_key][0]}", description)
                return DescriptionTranslation(source=description, text=text, name="", forms=(self.to_forms(self.sounds[phrase_key], subject_portuguese),))
            if any(word.lower().endswith(("'s", "’s")) or word.lower() in self.subjects or word.lower() in self.sounds or (not description.isupper() and word[0].islower()) for word in subject):
                continue
            name = " ".join(subject)
            phrase_portuguese = self.sounds[phrase_key][0].upper() if description.isupper() else self.sounds[phrase_key][0]
            return DescriptionTranslation(source=description, text=f"{name} {phrase_portuguese}", name=name, forms=(self.to_forms(self.sounds[phrase_key], name),))
        return None

    def to_forms(self, phrases: tuple[str, ...], subject: str) -> frozenset[str]:
        return frozenset(DESCRIPTION_ARTICLE.sub("", normalize_description(f"{subject} {phrase}")) for phrase in phrases)


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
    issues_ignored: int

    @property
    def score(self) -> float:
        return round(100 * sum(issue.kind.weight for issue in self.issues) / max(self.entries_count, SCORE_ENTRIES_MIN), 1)

    def compose(self) -> str:
        kinds = Counter(issue.kind for issue in self.issues)
        lines = [
            str(self.subtitle.absolute()),
            f"score {self.score} (weighted issues per 100 entries) · {self.entries_count} entries · {len(self.issues)} issues",
            "",
            *(f"  {count:<4} {kind.label:<21} {kind.description}" for kind, count in sorted(kinds.items(), key=lambda item: (-item[1], list(IssueKind).index(item[0])))),
            *([f"  {self.issues_ignored:<4} {'ignored':<21} flags on entries marked with ignore: in the subtitle, not counted"] if self.issues_ignored else []),
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


@dataclass
class Trace:
    file: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.records.append(record)
            write_text_replacing(self.file, json.dumps(self.records, ensure_ascii=False, indent=2))


@dataclass
class Batch:
    failures: list[tuple[Path, Exception]] = field(default_factory=list)
    movie_dir_logged: Path | None = None

    def log_movie(self, movie_dir: Path) -> None:
        if movie_dir == self.movie_dir_logged:
            return
        console.print(movie_dir.name, style=Color.CYAN)
        self.movie_dir_logged = movie_dir

    def fail(self, path: Path, error: Exception) -> None:
        log_action("failed", Color.RED, f"{path.name}: {error}")
        self.failures.append((path, error))

    def finish(self) -> None:
        if not self.failures:
            return
        console.print()
        console.print("Failed:", style=Color.RED)
        for path, error in self.failures:
            console.print(f"  {path}: {error}", style=Color.RED)
        sys.exit(1)
