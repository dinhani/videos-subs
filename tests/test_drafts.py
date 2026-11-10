from datetime import timedelta
from pathlib import Path

import pytest
import srt

from shared.drafts import Draft, Reference, Session, Source, WorkFolder
from shared.files import SUBS_FOLDER, SubtitleFile


def entries(*contents: str, start: int = 0) -> list[srt.Subtitle]:
    return [srt.Subtitle(index=n, start=timedelta(seconds=start + 2 * n), end=timedelta(seconds=start + 2 * n + 1), content=content, proprietary="ignore:unknown-word") for n, content in enumerate(contents, start=1)]


def draft(text: str) -> Draft:
    return Draft.from_parts([text])


def test_work_folder_round_trip(tmp_path: Path) -> None:
    source = Source.from_entries(entries("Hi.\nYou okay?", "", "Bye."))
    (tmp_path / SUBS_FOLDER).mkdir()
    reference = Reference(file=SubtitleFile.from_path(tmp_path / SUBS_FOLDER / "video.embed.5.spa.srt"), lines={3: ("Adiós.",)}, entries_english=2)
    work = WorkFolder(path=tmp_path / "work")
    work.create(source=source, references=[reference])
    assert work.read_source() == source
    assert (work.path / "english.txt").read_text(encoding="utf-8") == "#1\nHi.\nYou okay?\n\n#3\nBye.\n"
    assert (work.path / "references" / "spa.embed.5.txt").read_text(encoding="utf-8") == "#3\nAdiós.\n"


def test_work_folder_session_round_trip(tmp_path: Path) -> None:
    work = WorkFolder(path=tmp_path)
    assert work.find_session() is None
    session = Session(session_id="abc", home=Path("C:/Users/me/.claude-2"))
    work.save_session(session)
    assert work.find_session() == session


def test_reference_lines_go_to_the_number_of_their_english_entry(tmp_path: Path) -> None:
    file = tmp_path / SUBS_FOLDER / "video.embed.3.eng.sdh.srt"
    file.parent.mkdir()
    file.write_text(srt.compose(entries("[DOOR OPENS]", "MAN: Bye."), reindex=False), encoding="utf-8", newline="\n")
    reference = Reference.from_file(SubtitleFile.from_path(file), english_entries=entries("Hi.", "", "Bye.", start=-2))
    assert reference.name == "eng.sdh.embed.3.txt"
    assert reference.lines == {3: ("MAN: Bye.",)}


@pytest.mark.parametrize(("translation", "problem"), [
    ("#1\n<i>Oi, 42.</i>\nTudo bem?", "#1: 2 lines, the English has 1"),
    ("#1\n<i>Oi, 42.", "#1: tags <i>, the English has <i> </i>"),
    ("#1\n<i>Oi, 4.</i>", "#1: digits 4, the English has 42"),
    ("#1\n<i>Oi, 42.</i>\n#1\n<i>Oi, 42.</i>", "#1: written more than once"),
    ("#3\nOi.", "#3: not in the source"),
])
def test_draft_problems(translation: str, problem: str) -> None:
    source = Source.from_entries(entries("<i>Hi, 42.</i>"))
    assert draft(translation).find_problems(source) == [problem]


def test_draft_right_has_no_problems() -> None:
    source = Source.from_entries(entries("<i>Hi, 42.</i>", "- Go.\n- Now!"))
    assert draft("#1\n<i>Oi, 42.</i>\n\n#2\n- Vai.\n- Agora!\n").find_problems(source) == []


def test_draft_missing_entries() -> None:
    source = Source.from_entries(entries("Hi.", "", "Bye."))
    assert draft("#1\nOi.").find_missing(source) == [3]


def test_draft_joins_parts_in_order() -> None:
    assert Draft.from_parts(["#1\nOi.\n", "#3\nTchau.\n"]).translated == {1: ("Oi.",), 3: ("Tchau.",)}


def test_draft_compose_keeps_numbers_and_timings() -> None:
    sub_entries = entries("Hi.", "", "Bye.")
    composed = list(srt.parse(draft("#1\nOi.\n#3\nTchau.").compose(sub_entries)))
    assert [(entry.index, entry.start, entry.end, entry.content, entry.proprietary) for entry in composed] == [(entry.index, entry.start, entry.end, text, "") for entry, text in zip(sub_entries, ("Oi.", "", "Tchau."))]
