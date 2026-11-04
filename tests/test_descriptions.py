from pathlib import Path

import pytest

from shared.data_lists import FIX_SOUNDS, FIX_SUBJECTS
from shared.descriptions import DescriptionTable, MovieNames

TABLE = DescriptionTable.from_lists(FIX_SOUNDS, FIX_SUBJECTS)


@pytest.mark.parametrize(("description", "text", "name"), [
    ("GRUNTS", "GRUNHE", ""),
    ("Grunts", "Grunhe", ""),
    ("grunts", "grunhe", ""),
    ("MAN SCREAMS", "HOMEM GRITA", ""),
    ("crowd laughing", "multidão rindo", ""),
    ("JAKE GRUNTS", "JAKE GRUNHE", "JAKE"),
    ("Jake grunts", "Jake grunhe", "Jake"),
    ("GRUNTS, GASPS", "GRUNHE, ARFA", ""),
    ("JAKE GRUNTS, GASPS", "JAKE GRUNHE, ARFA", "JAKE"),
])
def test_find_translation(description: str, text: str, name: str) -> None:
    translation = TABLE.find_translation(description)
    assert translation is not None
    assert (translation.text, translation.name) == (text, name)


@pytest.mark.parametrize("description", [
    "JAKE'S GRUNTS",
    "PEOPLE SCREAMS",
    "jake grunts",
    "GRUNTS, WHIMPERS",
])
def test_find_translation_refused(description: str) -> None:
    assert TABLE.find_translation(description) is None


@pytest.mark.parametrize(("model", "names", "confirmed"), [
    ("JAKE GRUNHINDO", frozenset({"jake"}), True),
    ("JAKE GRUNHINDO", frozenset(), False),
    ("ELE GRUNHE", frozenset({"jake"}), False),
    ("JAKE GRUNTS", frozenset({"jake"}), False),
])
def test_is_confirmed_by(model: str, names: frozenset[str], confirmed: bool) -> None:
    translation = TABLE.find_translation("JAKE GRUNTS")
    assert translation is not None
    assert translation.is_confirmed_by(model, MovieNames(names_known=names, dialogue=())) == confirmed


@pytest.mark.parametrize(("model", "already"), [
    ("RISOS", True),
    ("OS RISOS", True),
    ("Risada", True),
    ("GARGALHADA", False),
])
def test_is_already_in(model: str, already: bool) -> None:
    translation = TABLE.find_translation("CHUCKLES")
    assert translation is not None
    assert translation.is_already_in(model) == already


def test_movie_names(tmp_path: Path) -> None:
    (tmp_path / "movie.embed.2.eng.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nSHURI: Look at this.\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nI talked to Peter Parker yesterday.\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\nTONY STARK IS HERE.\n",
        encoding="utf-8",
    )
    (tmp_path / "movie.embed.3.spa.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nRAMONA: Hola.\n", encoding="utf-8")

    movie_names = MovieNames.from_subs_folder(tmp_path)

    assert all(movie_names.is_name(word) for word in ("shuri", "peter", "parker", "tulkun"))
    assert not any(movie_names.is_name(word) for word in ("tony", "stark", "ramona"))
