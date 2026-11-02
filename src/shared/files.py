import argparse
import functools
import os
from pathlib import Path

import srt
from lingua import Language
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_fixed

from shared.subtitles import SubtitleKind

DIR_FILMES = Path("Z:/midias/filmes/")
DIR_SERIES = Path("Z:/midias/series/")
DIRS_MIDIAS = (DIR_FILMES, DIR_SERIES)

SUBS_FOLDER = "_subs"
TRACES_FOLDER = "_traces"
CHECKS_FOLDER = "_checks"

REPLACE_ATTEMPTS = 10
REPLACE_WAIT_SECONDS = 0.5

IMAGE_EXTENSIONS = (".sup", ".mks")

LANGUAGE_CODES = {language.iso_code_639_3.name.lower() for language in Language.all()} | {"pob", "und"}


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


def find_language_code(file: Path) -> str | None:
    stem = Path(file.stem)
    if stem.suffix == SubtitleKind.SDH:
        stem = Path(stem.stem)
    code = stem.suffix.removeprefix(".")
    return code if code in LANGUAGE_CODES else None


def parse_subtitle_name(file: Path) -> str:
    stem = Path(file.stem)
    if find_language_code(file) is None:
        return stem.name
    if stem.suffix == SubtitleKind.SDH:
        stem = Path(stem.stem)
    return stem.stem


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
