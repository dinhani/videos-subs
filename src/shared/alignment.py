import bisect
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta

import srt

ALIGNMENT_OFFSET_MAX = timedelta(seconds=2)
ALIGNMENT_OFFSET_WINDOW = 50
ALIGNMENT_OVERLAP_MIN = timedelta(milliseconds=200)


@dataclass(frozen=True)
class AlignedSubtitle:
    entries: tuple[srt.Subtitle, ...]
    offset: timedelta

    @classmethod
    def from_entries(cls, entries: Sequence[srt.Subtitle], english: Sequence[srt.Subtitle]) -> "AlignedSubtitle":
        # closest start differences
        starts = sorted(entry.start for entry in entries)
        differences: list[timedelta | None] = []
        for entry in english:
            position = bisect.bisect_left(starts, entry.start)
            closest = min(starts[max(0, position - 1):position + 1], key=lambda start: abs(start - entry.start), default=None)
            differences.append(closest - entry.start if closest is not None and abs(closest - entry.start) < ALIGNMENT_OFFSET_MAX else None)

        # local offsets
        offsets = []
        for i in range(len(english)):
            window = [difference.total_seconds() for difference in differences[max(0, i - ALIGNMENT_OFFSET_WINDOW // 2):i + ALIGNMENT_OFFSET_WINDOW // 2 + 1] if difference is not None]
            offsets.append(timedelta(seconds=statistics.median(window)) if window else timedelta(0))

        # shift entries
        english_starts = [entry.start + offset for entry, offset in zip(english, offsets)]
        order = sorted(range(len(english)), key=lambda i: english_starts[i])
        order_starts = [english_starts[i] for i in order]
        shifted = []
        for entry in entries:
            position = bisect.bisect_left(order_starts, entry.start)
            nearby = order[max(0, position - 1):position + 1]
            offset = offsets[min(nearby, key=lambda i: abs(english_starts[i] - entry.start))] if nearby else timedelta(0)
            shifted.append(srt.Subtitle(index=entry.index, start=entry.start - offset, end=entry.end - offset, content=entry.content, proprietary=entry.proprietary))
        return cls(entries=tuple(shifted), offset=timedelta(seconds=statistics.median(offset.total_seconds() for offset in offsets)) if offsets else timedelta(0))

    def find_overlapping(self, entry: srt.Subtitle) -> list[srt.Subtitle]:
        return [other for other in self.entries if min(entry.end, other.end) - max(entry.start, other.start) > ALIGNMENT_OVERLAP_MIN]
