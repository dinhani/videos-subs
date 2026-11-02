from datetime import timedelta
from pathlib import Path

import pytest
import srt

from shared.files import find_language_code, parse_subtitle_name
from shared.reports import Issue, IssueKind, Report, ReportScore
from shared.subtitles import SubtitleKind


@pytest.mark.parametrize(("name", "code", "subtitle_name"), [
    ("movie.embed.2.eng.srt", "eng", "movie.embed.2"),
    ("movie.embed.2.eng.sdh.srt", "eng", "movie.embed.2"),
    ("movie.embed.3.und.sup", "und", "movie.embed.3"),
    ("movie.embed.2.pob.srt", "pob", "movie.embed.2"),
    ("movie.embed.2.srt", None, "movie.embed.2"),
    ("movie.embed.2.sdh.srt", None, "movie.embed.2.sdh"),
    ("movie.embed.2.pob.translated.srt", None, "movie.embed.2.pob.translated"),
])
def test_language_code_and_name(name: str, code: str | None, subtitle_name: str) -> None:
    assert (find_language_code(Path(name)), parse_subtitle_name(Path(name))) == (code, subtitle_name)


@pytest.mark.parametrize(("name", "kind"), [
    ("movie.embed.2.eng.srt", SubtitleKind.BASE),
    ("movie.embed.2.eng.sdh.srt", SubtitleKind.SDH),
    ("movie.embed.3.eng.sdh.sup", SubtitleKind.SDH),
])
def test_kind_from_file(name: str, kind: SubtitleKind) -> None:
    assert SubtitleKind.from_file(Path(name)) == kind


@pytest.mark.parametrize(("name", "kind"), [
    ("movie.embed.2.pob.translated.srt", SubtitleKind.BASE),
    ("movie.embed.2.pob.sdh.translated.srt", SubtitleKind.SDH),
    ("movie.embed.2.eng.srt", None),
])
def test_kind_in_translation(name: str, kind: SubtitleKind | None) -> None:
    assert SubtitleKind.find_in_translation(Path(name)) == kind


def test_kind_from_texts_sdh() -> None:
    texts = [f"({sound}) Line {i}." for i, sound in enumerate(["GRUNTS", "GASPS", "SIGHS", "DOOR OPENS", "PHONE RINGING", "LAUGHS"] * 3)]
    assert SubtitleKind.from_texts(texts) == SubtitleKind.SDH


def test_kind_from_texts_commentary_names() -> None:
    texts = [f"({speaker}) Line {i}." for i, speaker in enumerate(["Avi", "Lauren"] * 10)]
    assert SubtitleKind.from_texts(texts) == SubtitleKind.BASE


def test_report_score_reads_back(tmp_path: Path) -> None:
    entries = [srt.Subtitle(index=index, start=timedelta(seconds=index), end=timedelta(seconds=index + 1), content=f"Linha {index}.") for index in range(1, 4)]
    subtitle = tmp_path / "movie.embed.2.pob.translated.srt"
    subtitle.write_text(srt.compose(entries), encoding="utf-8")
    issues = [Issue(entry=entries[1], kind=IssueKind.BROKEN_LINE, detail="line 1", quote=("pt  Linha 2.",))]
    report = Report(subtitle=subtitle, entries_count=len(entries), issues=issues, issues_ignored=0)

    score = report.write(tmp_path / "_checks" / "movie.embed.2.pob.translated.txt")

    assert ReportScore.find(score.report) == score
    assert (score.score, score.issues_count) == (4.0, 1)
