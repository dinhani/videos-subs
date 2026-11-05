from pathlib import Path

import pytest

from shared.data_lists import FIX_CHANNELS, FIX_LANGUAGES, FIX_MUSIC_MOODS, FIX_SOUND_LISTS, FIX_SUBJECTS, FIX_VERBS, FIX_VOICE_MOODS
from shared.descriptions import DescriptionTable, MovieNames

TABLE = DescriptionTable.from_lists(sounds=FIX_SOUND_LISTS, subjects=FIX_SUBJECTS, verbs=FIX_VERBS, music_moods=FIX_MUSIC_MOODS, voice_moods=FIX_VOICE_MOODS, languages=FIX_LANGUAGES, channels=FIX_CHANNELS)


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
    ("GRIM MUSIC PLAYING", "MÚSICA SOMBRIA TOCANDO", ""),
    ("SPEAKING SWEDISH", "FALANDO SUECO", ""),
    ("SHOUTING IN GERMAN", "GRITANDO EM ALEMÃO", ""),
    ("MAN ON TV", "HOMEM NA TV", ""),
    ("MARY OVER RADIO", "MARY PELO RÁDIO", "MARY"),
    ("MAN 2", "HOMEM 2", ""),
    ("MAN 2 SCREAMS", "HOMEM 2 GRITA", ""),
    ("CONTINUES LAUGHING", "CONTINUA RINDO", ""),
    ("DISTANT EXPLOSION", "EXPLOSÃO AO LONGE", ""),
    ("IN A DEEP VOICE", "COM UMA VOZ GRAVE", ""),
    ("GROANS IN PAIN", "GEME DE DOR", ""),
    ("ALL LAUGHING", "TODO MUNDO RINDO", ""),
    ("ALL LAUGH", "TODO MUNDO RI", ""),
    ("PEOPLE SCREAM", "PESSOAS GRITAM", ""),
    ("crowd", "multidão", ""),
    ("Sighs in relief", "Suspira de alívio", ""),
    ("IN SIGN LANGUAGE", "EM LÍNGUA DE SINAIS", ""),
    ("whispering in normal voice", "sussurrando com voz normal", ""),
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
    "EMOTIONAL MUSIC PLAYING",
    "SOFT PENSIVE MUSIC PLAYING",
    "SOLDIERS ON RADIO SCREAMS",
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
