import importlib
from datetime import timedelta
from pathlib import Path

import pytest
import srt

from shared.data_lists import DataList, DataListFormat
from shared.descriptions import MovieNames

fix_translations = importlib.import_module("7-fix-translations")
TranslationEntry = fix_translations.TranslationEntry
TranslationFix = fix_translations.TranslationFix
WordsFix = fix_translations.WordsFix


def entry(english: str, translated: str) -> TranslationEntry:
    def subtitle(content: str) -> srt.Subtitle:
        return srt.Subtitle(index=1, start=timedelta(0), end=timedelta(seconds=1), content=content)

    return TranslationEntry.from_entries(subtitle(english), subtitle(translated))


def fix(fix: TranslationFix, english: str, translated: str, names: frozenset[str] = frozenset()) -> str:
    return "\n".join(fix.fix(entry(english, translated), MovieNames(names_known=names, dialogue=())))


def fix_all(english: str, translated: str, names: frozenset[str] = frozenset()) -> str:
    translation, _ = entry(english, translated).apply_fixes(fix_translations.FIXES, MovieNames(names_known=names, dialogue=()))
    return "\n".join(translation.target_lines)


TAGS = fix_translations.TagsFix(enabled=True)
LEFTOVERS = fix_translations.LeftoversFix(enabled=True)
BRACKETS = fix_translations.BracketsFix(enabled=True)
QUOTES = fix_translations.QuotesFix(enabled=True)
DESCRIPTIONS = fix_translations.DescriptionsFix(enabled=True, sounds=fix_translations.FIX_SOUND_LISTS, subjects=fix_translations.FIX_SUBJECTS, music_moods=fix_translations.FIX_MUSIC_MOODS, voice_moods=fix_translations.FIX_VOICE_MOODS, languages=fix_translations.FIX_LANGUAGES, channels=fix_translations.FIX_CHANNELS)
INTERJECTIONS = fix_translations.InterjectionsFix(enabled=True, interjections=fix_translations.FIX_INTERJECTIONS)


@pytest.mark.parametrize(("english", "translated", "expected"), [
    ("Hello.", "<b>Olá.</b>", "Olá."),
    ("Hello world.", "Olá<br>mundo.", "Olá mundo."),
    ("<i>Hi.</i>", "<i>Oi.</i>", "<i>Oi.</i>"),
    ("{\\an8}Up here.", "Aqui em cima.", "{\\an8}Aqui em cima."),
    ("<i>Hi.</i>\nBye.", "Oi.\nTchau.", "<i>Oi.</i>\nTchau."),
    ("<i>Hi.</i>\n<i>Bye.</i>", "<i>Oi.</i>\nTchau.", "<i>Oi.</i>\nTchau."),
])
def test_tags(english: str, translated: str, expected: str) -> None:
    assert fix(TAGS, english, translated) == expected


@pytest.mark.parametrize(("english", "translated", "expected"), [
    ("Hello.", 'Olá."]},', "Olá."),
    ('The end."', 'Fim."]', 'Fim."'),
    ("Look [inaudible]", "Olha [inaudível]", "Olha [inaudível]"),
    ('"and preserve knowledge.', 'e preservar o conhecimento."]},', 'e preservar o conhecimento."]},'),
])
def test_leftovers(english: str, translated: str, expected: str) -> None:
    assert fix(LEFTOVERS, english, translated) == expected


@pytest.mark.parametrize(("english", "translated", "expected"), [
    ("I know.", "(Eu sei.)", "Eu sei."),
    ("(SCOTT GASPS SOFTLY)", "(SCOTT ARFA COM O SUSTO]", "(SCOTT ARFA COM O SUSTO)"),
    ("(SIGHS)", "(SUSPIRA", "(SUSPIRA)"),
    ("(SIGHS)", "SUSPIRA)", "(SUSPIRA)"),
    ("(sighs) Okay.", "(suspira) Tudo bem.", "(suspira) Tudo bem."),
])
def test_brackets(english: str, translated: str, expected: str) -> None:
    assert fix(BRACKETS, english, translated) == expected


@pytest.mark.parametrize(("english", "translated", "expected"), [
    ("I know.", '"Eu sei.', "Eu sei."),
    ('Say "yes"\nnow.', '"Diga "sim"\nagora.', 'Diga "sim"\nagora.'),
    ('He said "no."', 'Ele disse "não."', 'Ele disse "não."'),
    ("They'd say, \"keep on steppin'.”", 'Eles diziam: "continue andando."', 'Eles diziam: "continue andando."'),
])
def test_quotes(english: str, translated: str, expected: str) -> None:
    assert fix(QUOTES, english, translated) == expected


@pytest.mark.parametrize(("english", "translated", "expected"), [
    ("Wow.", "Wow.", "Uau."),
    ("Uh-huh. Sure.", "UH-HUH. CLARO.", "AHAM. CLARO."),
    ("Ow, ow!", "Ow, ow!", "Ai, ai!"),
    ("♪ Wow, wow ♪", "♪ Wow, wow ♪", "♪ Wow, wow ♪"),
    ("Nice.", "Wow.", "Wow."),
    ("Wow.", "Nossa.", "Nossa."),
])
def test_interjections(english: str, translated: str, expected: str) -> None:
    assert fix(INTERJECTIONS, english, translated) == expected


@pytest.mark.parametrize(("english", "translated", "names", "expected"), [
    ("(GRUNTS)", "(GRUNNIDO)", frozenset(), "(GRUNHE)"),
    ("(grunts)", "(grunnido)", frozenset(), "(grunhe)"),
    ("(Grunts)", "(Grunnido)", frozenset(), "(Grunhe)"),
    ("[ GRUNTS ]", "[ GRUNNIDO ]", frozenset(), "[ GRUNHE ]"),
    ("[ GRUNTS ]", "[GRUNNIDO]", frozenset(), "[GRUNHE]"),
    ("- [ Screams ]\n- [ Shrieking ]", "- [Grita]\n- [Berrando]", frozenset(), "- [Grita]\n- [Berrando]"),
    ("(GRUNTS)", "GRUNNIDO", frozenset(), "(GRUNHE)"),
    ("(SIGHS) Okay.", "(SUSPIRANDO) Tudo bem.", frozenset(), "(SUSPIRA) Tudo bem."),
    ("(CHUCKLES)", "(RISOS)", frozenset(), "(RISOS)"),
    ("(CHUCKLES)", "(OS RISOS)", frozenset(), "(OS RISOS)"),
    ("(JAKE GRUNTS)", "(JAKE GRUNNINDO)", frozenset({"jake"}), "(JAKE GRUNHE)"),
    ("(JAKE GRUNTS)", "(JAKE GRUNNINDO)", frozenset(), "(JAKE GRUNNINDO)"),
    ("(JAKE GRUNTS)", "(ELE GRUNHINDO)", frozenset({"jake"}), "(ELE GRUNHINDO)"),
    ("(METAL STRAINING)", "(METAL FORÇANDO)", frozenset(), "(METAL FORÇANDO)"),
])
def test_descriptions(english: str, translated: str, names: frozenset[str], expected: str) -> None:
    assert fix(DESCRIPTIONS, english, translated, names) == expected


def write_words(tmp_path: Path, *lines: str) -> WordsFix:
    file = tmp_path / "fix-words.txt"
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return WordsFix(enabled=True, words=DataList(file=file, format=DataListFormat.TRIPLE))


@pytest.mark.parametrize(("english", "translated", "expected"), [
    ("MAYBE A CONCUSSION.", "TALVE UMA CONCUSSÃO.", "TALVEZ UMA CONCUSSÃO."),
    ("Maybe.", "Talve.", "Talvez."),
    ("well, maybe", "bem, talve", "bem, talvez"),
    ("I DON'T KNOW.", "TALVE.", "TALVE."),
])
def test_words(tmp_path: Path, english: str, translated: str, expected: str) -> None:
    words = write_words(tmp_path, "maybe = talve = talvez")
    assert fix(words, english, translated) == expected


def test_words_two_right_words_leave_the_word(tmp_path: Path) -> None:
    words = write_words(tmp_path, "grave = tumba = túmulo", "serious = tumba = sério")
    assert fix(words, "A serious grave.", "Uma tumba tumba.") == "Uma tumba tumba."


def test_words_word_also_in_english_is_left(tmp_path: Path) -> None:
    words = write_words(tmp_path, "hello = hola = olá")
    assert fix(words, "Hola means hello.", "Hola significa olá.") == "Hola significa olá."


REGRESSIONS = [
    ("THANOS:\n<i>You should be grateful.</i>", "THANOS: <i>Você deveria ser grata.</i>']},\n27", frozenset(), "THANOS: <i>Você deveria ser grata.</i>']},\n27"),
    ("<i>BEN:\nIt's the condenser tray.</i>", '<i>BEN:\n39": {"en": ["It\'s the condenser tray."]', frozenset(), '<i>BEN:\n39": {"en": ["It\'s the condenser tray."]'),
    ("(BOTH LAUGHING)", "(AMBAS RINDO)", frozenset(), "(AMBAS RINDO)"),
    ("(DOG WHIMPERING)", "(cachorro ganindo)", frozenset(), "(cachorro ganindo)"),
    ("[ Clicks, Hums ]", "[ Cliques, Zumbidos ]", frozenset(), "[ Cliques, Zumbidos ]"),
    ("(PAYAKAN BELLOWS)", "(BRAMIDO DO PAYAKAN)", frozenset({"payakan"}), "(BRAMIDO DO PAYAKAN)"),
    ("They'd say, \"keep on steppin'.”", 'Eles diziam: "continue andando."', frozenset(), 'Eles diziam: "continue andando."'),
    ('"and preserve knowledge.', 'e preservar o conhecimento."]},', frozenset(), 'e preservar o conhecimento."]},'),
    ("What?\n(LAUGHTER)", "O quê? (RISOS)\n", frozenset(), "O quê? (RISOS)\n"),
    ("(METAL STRAINING)", "(METAL FORÇANDO)", frozenset(), "(METAL FORÇANDO)"),
    ("(CHUCKLES)", "(RISOS)", frozenset(), "(RISOS)"),
    ("(GRUNTS)", "(GRUNNIDO)", frozenset(), "(GRUNHE)"),
    ("(JAKE GRUNTS)", "(JAKE GRUNNINDO)", frozenset({"jake"}), "(JAKE GRUNHE)"),
    ("[ GRUNTS ]", "[ GRUNNIDO ]", frozenset(), "[ GRUNHE ]"),
    ("MAYBE A CONCUSSION.", "TALVE UMA CONCUSSÃO.", frozenset(), "TALVEZ UMA CONCUSSÃO."),
    ("I DON'T KNOW.", "TALVE UMA CONCUSSÃO.", frozenset(), "TALVE UMA CONCUSSÃO."),
    ('The end."', 'Fim."]', frozenset(), 'Fim."'),
    ("(SCOTT GASPS SOFTLY)", "(SCOTT ARFA COM O SUSTO]", frozenset({"scott"}), "(SCOTT ARFA BAIXINHO)"),
]


@pytest.mark.parametrize(("english", "translated", "names", "expected"), REGRESSIONS)
def test_regressions(english: str, translated: str, names: frozenset[str], expected: str) -> None:
    assert fix_all(english, translated, names) == expected


@pytest.mark.parametrize(("english", "translated", "names", "expected"), REGRESSIONS)
def test_second_run_changes_nothing(english: str, translated: str, names: frozenset[str], expected: str) -> None:
    fixed = fix_all(english, translated, names)
    assert fix_all(english, fixed, names) == fixed
