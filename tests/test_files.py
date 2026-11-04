from datetime import timedelta
from pathlib import Path

import pytest
import srt

from shared.files import CHECKS_FOLDER, SUBS_FOLDER, TRACES_FOLDER, SubtitleFile, SubtitleFormat
from shared.reports import Issue, IssueKind, Report, ReportScore
from shared.subtitles import IgnoreMarker, SubtitleKind, replace_text


@pytest.mark.parametrize(("name", "track", "language", "kind", "format"), [
    ("movie.embed.2.eng.srt", "movie.embed.2", "eng", SubtitleKind.BASE, SubtitleFormat.TEXT),
    ("movie.embed.2.eng.sdh.srt", "movie.embed.2", "eng", SubtitleKind.SDH, SubtitleFormat.TEXT),
    ("movie.embed.3.eng.sdh.sup", "movie.embed.3", "eng", SubtitleKind.SDH, SubtitleFormat.PGS),
    ("movie.embed.3.und.mks", "movie.embed.3", "und", SubtitleKind.BASE, SubtitleFormat.VOBSUB),
    ("movie.embed.2.pob.srt", "movie.embed.2", "pob", SubtitleKind.BASE, SubtitleFormat.TEXT),
    ("movie.embed.2.srt", "movie.embed.2", None, SubtitleKind.BASE, SubtitleFormat.TEXT),
    ("movie.embed.2.sdh.srt", "movie.embed.2.sdh", None, SubtitleKind.BASE, SubtitleFormat.TEXT),
    ("movie.embed.2.pob.translated.srt", "movie.embed.2", "pob", SubtitleKind.BASE, SubtitleFormat.TRANSLATION),
    ("movie.embed.2.pob.sdh.translated.srt", "movie.embed.2", "pob", SubtitleKind.SDH, SubtitleFormat.TRANSLATION),
    ("Movie (2019, 2160p, 5.1, AAC).eng.srt", "Movie (2019, 2160p, 5.1, AAC)", "eng", SubtitleKind.BASE, SubtitleFormat.TEXT),
])
def test_subtitle_file_round_trip(name: str, track: str, language: str | None, kind: SubtitleKind, format: SubtitleFormat) -> None:
    file = SubtitleFile.from_path(Path("movie") / SUBS_FOLDER / name)
    assert (file.track, file.language, file.kind, file.format) == (track, language, kind, format)
    assert file.path == Path("movie") / SUBS_FOLDER / name


def test_subtitle_file_paths_of_ocr() -> None:
    image = SubtitleFile.from_path(Path("movie") / SUBS_FOLDER / "video.embed.3.sup").to_identified("eng", SubtitleKind.SDH)
    assert image.path == Path("movie") / SUBS_FOLDER / "video.embed.3.eng.sdh.sup"
    assert image.to_text().path == Path("movie") / SUBS_FOLDER / "video.embed.3.eng.sdh.srt"
    assert image.to_trace() == Path("movie") / TRACES_FOLDER / "video.embed.3.eng.sdh.ocr.json"
    assert image.to_text().to_report() == Path("movie") / CHECKS_FOLDER / "video.embed.3.eng.sdh.ocr.txt"


def test_subtitle_file_paths_of_translation() -> None:
    source = SubtitleFile.from_path(Path("movie") / SUBS_FOLDER / "video.embed.2.eng.sdh.srt")
    translation = source.to_translation()
    assert translation.path == Path("movie") / "video.embed.2.pob.sdh.translated.srt"
    assert translation.to_trace() == Path("movie") / TRACES_FOLDER / "video.embed.2.pob.sdh.translated.json"
    assert translation.to_report() == Path("movie") / CHECKS_FOLDER / "video.embed.2.pob.sdh.translated.txt"
    assert translation.video_name == "video"


def test_subtitle_file_from_track() -> None:
    track = SubtitleFile.from_track(Path("movie") / "video.mkv", 3, SubtitleFormat.PGS)
    assert track.path == Path("movie") / SUBS_FOLDER / "video.embed.3.sup"
    assert track.is_image() and not track.is_identified()


def test_subtitle_file_movie_title_of_episode() -> None:
    episode = SubtitleFile.from_path(Path("Show") / "Season 01" / SUBS_FOLDER / "S01E01 - Pilot (2020, Web-DL, 2160p).embed.2.eng.srt")
    assert episode.movie_title == "Show, S01E01 - Pilot (2020)"
    assert episode.nfo == Path("Show") / "Season 01" / "S01E01 - Pilot (2020, Web-DL, 2160p).nfo"


def test_rename_identified_takes_ocr_output_and_trace(tmp_path: Path) -> None:
    for folder, name in ((SUBS_FOLDER, "video.embed.3.sup"), (SUBS_FOLDER, "video.embed.3.srt"), (TRACES_FOLDER, "video.embed.3.ocr.json")):
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / folder / name).write_text("", encoding="utf-8")

    image = SubtitleFile.from_path(tmp_path / SUBS_FOLDER / "video.embed.3.sup").rename_identified("eng", SubtitleKind.SDH)

    assert sorted(path.name for path in tmp_path.rglob("*") if path.is_file()) == ["video.embed.3.eng.sdh.ocr.json", "video.embed.3.eng.sdh.srt", "video.embed.3.eng.sdh.sup"]
    assert image.is_sdh() and image.path.exists()


def test_replace_text_drops_ignore_marker() -> None:
    entry = srt.Subtitle(index=1, start=timedelta(0), end=timedelta(seconds=1), content="Linha.", proprietary="X1:0 ignore:untranslated")
    assert replace_text(entry, "Outra linha.").proprietary == "X1:0"


def test_ignore_marker_holds_every_kind_once() -> None:
    entry = srt.Subtitle(index=1, start=timedelta(0), end=timedelta(seconds=1), content="Linha.", proprietary="ignore:untranslated")
    marked = IgnoreMarker.from_entry(entry).add(["unknown-word", "untranslated"]).to_entry(entry)
    assert marked.proprietary == "ignore:untranslated,unknown-word"


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


def test_report_issues_by_label_read_back(tmp_path: Path) -> None:
    entries = [srt.Subtitle(index=index, start=timedelta(seconds=index), end=timedelta(seconds=index + 1), content=f"Linha {index}.") for index in range(1, 5)]
    subtitle = tmp_path / "movie.embed.2.pob.translated.srt"
    subtitle.write_text(srt.compose(entries), encoding="utf-8")
    kinds = (IssueKind.ACCEPTED_WITH_PROBLEM, IssueKind.UNKNOWN_WORD_CAPS, IssueKind.UNKNOWN_WORD, IssueKind.UNKNOWN_WORD)
    issues = [Issue(entry=entry, kind=kind, detail="", quote=()) for entry, kind in zip(entries, kinds)]
    report = Report(subtitle=subtitle, entries_count=len(entries), issues=issues, issues_ignored=2)

    score = report.write(tmp_path / "_checks" / "movie.embed.2.pob.translated.txt")

    assert score.find_issues_by_label() == {"accepted with problem": 1, "unknown word in caps": 1, "unknown word": 2}
