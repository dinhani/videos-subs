from datetime import timedelta

import srt

from shared.alignment import AlignedSubtitle


def entry(index: int, start: float, end: float, content: str) -> srt.Subtitle:
    return srt.Subtitle(index=index, start=timedelta(seconds=start), end=timedelta(seconds=end), content=content)


def test_aligned_subtitle_follows_a_constant_offset() -> None:
    english = [entry(1, 10, 12, "Okay."), entry(2, 12.5, 14, "Sit down.")]
    other = [entry(1, 9.667, 11.667, "Bien."), entry(2, 12.167, 13.667, "Siéntate.")]
    aligned = AlignedSubtitle.from_entries(other, english=english)
    assert [match.content for match in aligned.find_overlapping(english[1])] == ["Siéntate."]


def test_aligned_subtitle_matches_an_entry_split_in_two() -> None:
    english = [entry(1, 10, 14, "If you could..."), entry(2, 20, 22, "Yes.")]
    other = [entry(1, 10, 12, "Si pudieras"), entry(2, 12, 14, "ayudarme..."), entry(3, 20, 22, "Sí.")]
    aligned = AlignedSubtitle.from_entries(other, english=english)
    assert [match.content for match in aligned.find_overlapping(english[0])] == ["Si pudieras", "ayudarme..."]
