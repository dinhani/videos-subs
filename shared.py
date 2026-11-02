import argparse
import base64
import functools
import io
import itertools
import json
import re
import sys
import threading
from collections import Counter
from collections.abc import Buffer
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import av
import srt
from av.subtitles.subtitle import BitmapSubtitle, SubtitleSet
from lingua import Language, LanguageDetectorBuilder
from openai import OpenAI
from PIL import Image
from rich.console import Console
from rich.progress import BarColumn, Progress, ProgressColumn, TextColumn
from rich.text import Text
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed

DIR_FILMES = Path("Z:/midias/filmes/")
DIR_SERIES = Path("Z:/midias/series/")
DIRS_MIDIAS = (DIR_FILMES, DIR_SERIES)

SUBS_FOLDER = "_subs"
TRACES_FOLDER = "_traces"
CHECKS_FOLDER = "_checks"

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


def find_files(description: str, extensions_file: tuple[str, ...], extensions_folder: tuple[str, ...], folder: str | None = None) -> list[Path]:
    parser = argparse.ArgumentParser(description=description)
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


def write_text_replacing(file: Path, text: str) -> None:
    file_part = file.with_name(f"{file.name}.part")
    try:
        file_part.write_text(text, encoding="utf-8")
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
