import argparse
import functools
import os
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

import srt
from lingua import Language
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed

from shared.subtitles import LANGUAGE_SOURCE, LANGUAGE_TRANSLATION, LANGUAGE_UNDETECTED, SubtitleKind

DIR_MIDIAS = Path("Z:/midias")
DIR_FILMES = DIR_MIDIAS / "filmes"
DIR_SERIES = DIR_MIDIAS / "series"
DIRS_MIDIAS = (DIR_FILMES, DIR_SERIES)

SUBS_FOLDER = "_subs"
TRACES_FOLDER = "_traces"
CHECKS_FOLDER = "_checks"
BACKUPS_FOLDER = DIR_MIDIAS / "_backups"

REPLACE_ATTEMPTS = 10
REPLACE_WAIT_SECONDS = 0.5

TRACK_INDEX = re.compile(r"\.embed\.\d+$")
MOVIE_TITLE = re.compile(r"^(.*?) \((\d{4})\b.*$")
SEASON_PREFIX = "Season"

LANGUAGE_CODES = {language.iso_code_639_3.name.lower() for language in Language.all()} | {LANGUAGE_TRANSLATION, LANGUAGE_UNDETECTED}


def find_files(parser: argparse.ArgumentParser, extensions_file: tuple[str, ...], extensions_folder: tuple[str, ...], folder: str | None = None) -> list[Path]:
    parser.add_argument("paths", nargs="*", type=Path, help=f"folders (scanned for {'/'.join(extensions_folder)}) or {'/'.join(extensions_file)} files (default: {', '.join(map(str, DIRS_MIDIAS))})")
    paths: list[Path] = parser.parse_args().paths or list(DIRS_MIDIAS)

    files: list[Path] = []
    for path in paths:
        if path.is_file():
            if not path.name.lower().endswith(extensions_file):
                parser.error(f"not a {'/'.join(extensions_file)} file: {path}")
            files.append(path)
        elif path.is_dir():
            files += sorted(directory / name for directory, _, names in path.walk() if folder is None or directory.name == folder for name in names if name.lower().endswith(extensions_folder))
        else:
            parser.error(f"path not found: {path}")
    return files


def find_subtitles(parser: argparse.ArgumentParser, formats: tuple["SubtitleFormat", ...], language: str | None = None) -> list["SubtitleFile"]:
    extensions = tuple(format.extension for format in formats)
    suffixes = tuple(suffix for format in formats for suffix in format.to_suffixes(language)) if language else extensions
    return [SubtitleFile.from_path(file) for file in find_files(parser, extensions_file=extensions, extensions_folder=suffixes, folder=formats[0].folder)]


def parse_movie_dir(file: Path) -> Path:
    return file.parent.parent if file.parent.name == SUBS_FOLDER else file.parent


def parse_srt(file: Path) -> list[srt.Subtitle]:
    return list(srt.parse(file.read_text(encoding="utf-8-sig", errors="replace"), ignore_errors=True))


@functools.cache
def list_folder(folder: Path) -> frozenset[str]:
    return frozenset(os.listdir(folder))


def write_text_replacing(file: Path, text: str, newline: str | None = None) -> None:
    file_part = file.with_name(f"{file.name}.part")
    try:
        file_part.write_text(text, encoding="utf-8", newline=newline)
        for attempt in Retrying(retry=retry_if_exception_type(PermissionError), stop=stop_after_attempt(REPLACE_ATTEMPTS), wait=wait_fixed(REPLACE_WAIT_SECONDS), reraise=True):
            with attempt:
                file_part.replace(file)
    finally:
        file_part.unlink(missing_ok=True)


class SubtitleFormat(Enum):
    TRANSLATION = (".translated.srt", ".translated.json", ".translated.txt")
    TEXT = (".srt", ".ocr.json", ".ocr.txt")
    PGS = (".sup", ".ocr.json", ".ocr.txt")
    VOBSUB = (".mks", ".ocr.json", ".ocr.txt")

    def __init__(self, extension: str, trace_suffix: str, report_suffix: str) -> None:
        self.extension = extension
        self.trace_suffix = trace_suffix
        self.report_suffix = report_suffix

    @classmethod
    def find(cls, file: Path) -> "SubtitleFormat | None":
        return next((format for format in cls if file.name.lower().endswith(format.extension)), None)

    @property
    def folder(self) -> str | None:
        return None if self == SubtitleFormat.TRANSLATION else SUBS_FOLDER

    def is_image(self) -> bool:
        return self in (SubtitleFormat.PGS, SubtitleFormat.VOBSUB)

    def to_suffixes(self, language: str) -> tuple[str, ...]:
        return tuple(f".{language}{kind}{self.extension}" for kind in SubtitleKind)


@dataclass(frozen=True)
class SubtitleFile:
    folder: Path
    track: str
    language: str | None
    kind: SubtitleKind
    format: SubtitleFormat

    @classmethod
    def from_track(cls, video: Path, index: int, format: SubtitleFormat) -> "SubtitleFile":
        return cls(folder=video.parent / SUBS_FOLDER, track=f"{video.stem}.embed.{index}", language=None, kind=SubtitleKind.BASE, format=format)

    @classmethod
    def find(cls, file: Path) -> "SubtitleFile | None":
        if SubtitleFormat.find(file) is None:
            return None
        return cls.from_path(file)

    @classmethod
    def from_path(cls, file: Path) -> "SubtitleFile":
        format = SubtitleFormat.find(file)
        if format is None:
            raise ValueError(f"not a subtitle: {file.name}")
        stem = Path(file.name[:-len(format.extension)])
        kind = SubtitleKind.SDH if stem.suffix == SubtitleKind.SDH else SubtitleKind.BASE
        stem_language = Path(stem.stem) if kind.is_sdh() else stem
        language = stem_language.suffix.removeprefix(".")
        if language not in LANGUAGE_CODES:
            return cls(folder=file.parent, track=stem.name, language=None, kind=SubtitleKind.BASE, format=format)
        return cls(folder=file.parent, track=stem_language.stem, language=language, kind=kind, format=format)

    @property
    def stem(self) -> str:
        return self.track if self.language is None else f"{self.track}.{self.language}{self.kind}"

    @property
    def name(self) -> str:
        return f"{self.stem}{self.format.extension}"

    @property
    def path(self) -> Path:
        return self.folder / self.name

    @property
    def movie_dir(self) -> Path:
        return parse_movie_dir(self.path)

    @property
    def video_name(self) -> str:
        return TRACK_INDEX.sub("", self.track)

    @property
    def nfo(self) -> Path:
        return self.movie_dir / f"{self.video_name}.nfo"

    @property
    def movie_title(self) -> str:
        title = MOVIE_TITLE.sub(r"\1 (\2)", self.track)
        if self.movie_dir.name.startswith(SEASON_PREFIX):
            return f"{self.movie_dir.parent.name}, {title}"
        return title

    def is_identified(self) -> bool:
        return self.language is not None

    def is_english(self) -> bool:
        return self.language == LANGUAGE_SOURCE

    def is_sdh(self) -> bool:
        return self.kind.is_sdh()

    def is_text(self) -> bool:
        return self.format == SubtitleFormat.TEXT

    def is_image(self) -> bool:
        return self.format.is_image()

    def is_translation(self) -> bool:
        return self.format == SubtitleFormat.TRANSLATION

    def describe_language(self, language_name: str = "") -> str:
        if self.language is None:
            return f"{'':<31}"
        return f".{self.language:<3}{self.kind:<4} {language_name:<22}"

    def to_identified(self, language: str, kind: SubtitleKind) -> "SubtitleFile":
        return replace(self, language=language, kind=kind)

    def rename_identified(self, language: str, kind: SubtitleKind) -> "SubtitleFile":
        identified = self.to_identified(language, kind)
        if self.is_image():
            for companion, companion_new in ((self.to_text().path, identified.to_text().path), (self.to_trace(), identified.to_trace())):
                if companion.exists():
                    companion.replace(companion_new)
        self.path.replace(identified.path)
        return identified

    def to_format(self, format: SubtitleFormat) -> "SubtitleFile":
        return replace(self, format=format)

    def to_text(self) -> "SubtitleFile":
        return self.to_format(SubtitleFormat.TEXT)

    def find_image_subtitle(self) -> "SubtitleFile | None":
        return next((image for image in (self.to_format(format) for format in SubtitleFormat if format.is_image()) if image.name in list_folder(self.folder)), None)

    def to_translation(self) -> "SubtitleFile":
        return SubtitleFile(folder=self.movie_dir, track=self.track, language=LANGUAGE_TRANSLATION, kind=self.kind, format=SubtitleFormat.TRANSLATION)

    def get_english_source(self) -> "SubtitleFile":
        if not self.is_translation():
            raise ValueError(f"not a translation: {self.name}")
        source = SubtitleFile(folder=self.movie_dir / SUBS_FOLDER, track=self.track, language=LANGUAGE_SOURCE, kind=self.kind, format=SubtitleFormat.TEXT)
        if not source.path.exists():
            raise FileNotFoundError(f"English source not found: {source.path}")
        return source

    def to_trace(self) -> Path:
        return self.movie_dir / TRACES_FOLDER / f"{self.stem}{self.format.trace_suffix}"

    def to_report(self) -> Path:
        return self.movie_dir / CHECKS_FOLDER / f"{self.stem}{self.format.report_suffix}"


@dataclass(frozen=True)
class Backups:
    run: Path

    @staticmethod
    def add_argument(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--backups", type=Path, default=BACKUPS_FOLDER, help=f"folder that holds a folder of backups for each run (default: {BACKUPS_FOLDER})")

    @classmethod
    def start(cls, folder: Path) -> "Backups":
        return cls(run=folder / datetime.now().strftime("%Y-%m-%dT%H-%M-%S"))

    def save(self, file: Path) -> Path:
        # a copy under the path of the file from the library root, in a folder of its own for this run, so no backup is ever replaced
        file_absolute = file.absolute()
        backup = self.run / (file_absolute.relative_to(DIR_MIDIAS) if file_absolute.is_relative_to(DIR_MIDIAS) else file_absolute.relative_to(file_absolute.anchor))
        if backup.exists():
            raise FileExistsError(f"backup already exists: {backup}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, backup)
        if backup.read_bytes() != file.read_bytes():
            raise OSError(f"backup differs from the file: {backup}")
        return backup
