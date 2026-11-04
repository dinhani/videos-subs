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

    def to_suffixes(self, language: str) -> tuple[str, ...]:
        return tuple(f".{language}{kind}{self.extension}" for kind in SubtitleKind)


IMAGE_FORMATS = (SubtitleFormat.PGS, SubtitleFormat.VOBSUB)
IMAGE_EXTENSIONS = tuple(format.extension for format in IMAGE_FORMATS)
TRANSLATION_SUFFIXES = SubtitleFormat.TRANSLATION.to_suffixes(LANGUAGE_TRANSLATION)


@dataclass(frozen=True)
class SubtitleFile:
    folder: Path
    track: str
    language: str | None
    kind: SubtitleKind
    format: SubtitleFormat

    @classmethod
    def from_path(cls, file: Path) -> "SubtitleFile":
        format = SubtitleFormat.find(file)
        if format is None:
            raise ValueError(f"not a subtitle: {file.name}")
        stem = Path(file.name[:-len(format.extension)])
        kind = SubtitleKind.SDH if stem.suffix == SubtitleKind.SDH else SubtitleKind.BASE
        stem_language = Path(stem.stem) if kind == SubtitleKind.SDH else stem
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

    def is_identified(self) -> bool:
        return self.language is not None

    def to_identified(self, language: str, kind: SubtitleKind) -> "SubtitleFile":
        return replace(self, language=language, kind=kind)

    def to_format(self, format: SubtitleFormat) -> "SubtitleFile":
        return replace(self, format=format)

    def find_image_subtitle(self) -> "SubtitleFile | None":
        return next((image for image in (self.to_format(format) for format in IMAGE_FORMATS) if image.name in list_folder(self.folder)), None)

    def to_translation(self) -> "SubtitleFile":
        return SubtitleFile(folder=self.movie_dir, track=self.track, language=LANGUAGE_TRANSLATION, kind=self.kind, format=SubtitleFormat.TRANSLATION)

    def get_english_source(self) -> "SubtitleFile":
        if self.format != SubtitleFormat.TRANSLATION:
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
