import base64
import functools
import io
import itertools
import re
from collections.abc import Buffer
from dataclasses import dataclass, field, replace
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import cast

import av
from av.subtitles.subtitle import BitmapSubtitle, SubtitleSet
from PIL import Image

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
VOBSUB_HEIGHT_MAX = 1080


class ScreenPosition(StrEnum):
    TOP = "{\\an8}"
    BOTTOM = ""


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
        return cls(width=int(size[1]), height=min(int(size[2]), VOBSUB_HEIGHT_MAX), colors=[bytes.fromhex(color.strip()) for color in palette[1].split(",")])

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
