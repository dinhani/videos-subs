import importlib
from datetime import timedelta
from pathlib import Path

import pytest
import srt

check_translations = importlib.import_module("8-check-translations")
TranslationCheck = check_translations.TranslationCheck


def check(english: str, translated: str) -> TranslationCheck:
    def subtitle(content: str) -> srt.Subtitle:
        return srt.Subtitle(index=1, start=timedelta(0), end=timedelta(seconds=1), content=content)

    entry = check_translations.TranslationEntry.from_entries(subtitle(english), subtitle(translated))
    return TranslationCheck(subtitle=Path("movie.pob.translated.srt"), entries=[entry], problems_accepted={}, languagetool_server="")


BROKEN_LINE = check_translations.BrokenLineRule(enabled=True)


@pytest.mark.parametrize(("english", "translated", "flagged"), [
    ("You should be grateful.", "27", True),
    ("It's over.", '"]},', True),
    ("It's over.", ", acabou.", True),
    ("1999.", "1999.", False),
    ("It's over.", "Acabou.", False),
    ("♪ La la la ♪", "♪ La la la ♪", False),
])
def test_broken_line(english: str, translated: str, flagged: bool) -> None:
    assert bool(list(BROKEN_LINE.find_issues(check(english, translated)))) == flagged
