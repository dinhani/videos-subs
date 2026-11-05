import contextlib
import itertools
import os
import sys
from collections.abc import Generator, Iterable, Iterator
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from types import TracebackType

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, ProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.text import Text

from shared.files import SubtitleFile

console = Console(highlight=False, markup=False)


class Color(StrEnum):
    BLUE = "blue"
    CYAN = "cyan"
    GREEN = "green"
    RED = "red"
    YELLOW = "yellow"


class Action(Enum):
    EXISTS = ("exists", Color.BLUE)
    SKIP = ("skip", Color.BLUE)

    EXTRACT = ("extract", Color.GREEN)
    RENAME = ("rename", Color.GREEN)
    OCR = ("ocr", Color.GREEN)
    CHECK = ("check", Color.GREEN)
    TRANSLATE = ("translate", Color.GREEN)
    FIX = ("fix", Color.GREEN)
    WARN = ("warn", Color.GREEN)
    ADD = ("add", Color.GREEN)
    SCORE = ("score", Color.GREEN)
    COUNT = ("count", Color.GREEN)
    DOWNLOAD = ("download", Color.GREEN)
    START = ("start", Color.GREEN)
    STOP = ("stop", Color.GREEN)
    COPY = ("copy", Color.GREEN)
    ALIGN = ("align", Color.GREEN)
    COMPARE = ("compare", Color.GREEN)
    PROMPT = ("prompt", Color.GREEN)

    UNKNOWN = ("unknown", Color.YELLOW)
    EMPTY = ("empty", Color.YELLOW)
    DISCARD = ("discard", Color.YELLOW)
    RETRY = ("retry", Color.YELLOW)
    SPLIT = ("split", Color.YELLOW)
    ACCEPT = ("accept", Color.YELLOW)
    NFO = ("nfo", Color.YELLOW)
    DECLINED = ("declined", Color.YELLOW)
    RESPLIT = ("resplit", Color.YELLOW)

    FAILED = ("failed", Color.RED)

    def __init__(self, label: str, color: Color) -> None:
        self.label = label
        self.color = color

    def log(self, details: str | Text) -> None:
        console.print(Text.assemble("  ", (f"{self.label:<{ACTION_WIDTH}}", self.color), " ", details))


ACTION_WIDTH = max(len(action.label) for action in Action)


def log_heading(text: str, color: Color) -> None:
    console.print()
    console.print(text, style=color)


def progress_bar(*columns: ProgressColumn) -> Progress:
    return Progress(
        TextColumn("  "),
        BarColumn(bar_width=40, style=Color.BLUE, complete_style=Color.GREEN, finished_style=Color.GREEN),
        *columns,
        console=console,
        transient=True,
    )


def track[T](items: Iterable[T], total: int | None = None) -> Iterator[T]:
    with progress_bar(MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn()) as progress:
        yield from progress.track(items, total=total)


@contextlib.contextmanager
def cancel_on_interrupt[E: Executor](executor: E) -> Generator[E]:
    with executor:
        try:
            yield executor
        except KeyboardInterrupt:
            executor.shutdown(cancel_futures=True)
            raise


@dataclass
class Batch:
    failures: list[tuple[Path, Exception]] = field(default_factory=list)
    movie_dir_logged: Path | None = None

    def __enter__(self) -> "Batch":
        return self

    def __exit__(self, error_type: type[BaseException] | None, error: BaseException | None, traceback: TracebackType | None) -> None:
        if error_type is None:
            self.finish()

    def walk(self, files: Iterable[SubtitleFile]) -> Iterator[SubtitleFile]:
        for file in files:
            self.log_movie(file.movie_dir)
            yield file

    def walk_ahead[T](self, files: Iterable[SubtitleFile], futures: dict[SubtitleFile, Future[T]]) -> Iterator[tuple[SubtitleFile, Future[T] | None]]:
        for movie_dir, movie_files in itertools.groupby(files, key=lambda file: file.movie_dir):
            self.log_movie(movie_dir)
            files_pending: list[SubtitleFile] = []
            for file in movie_files:
                if file not in futures:
                    yield file, None
                    continue
                files_pending.append(file)
            if not files_pending:
                continue
            for file in track(files_pending):
                yield file, futures[file]

    @contextlib.contextmanager
    def attempt(self, path: Path) -> Generator[None]:
        try:
            yield
        except Exception as error:
            self.fail(path, error)

    def log_movie(self, movie_dir: Path) -> None:
        if movie_dir == self.movie_dir_logged:
            return
        console.print(movie_dir.name, style=Color.CYAN)
        self.movie_dir_logged = movie_dir

    def clear_screen(self) -> None:
        os.system("cls")
        if console.is_terminal and not console.legacy_windows:
            console.file.write("\x1b[3J")
            console.file.flush()
        movie_dir, self.movie_dir_logged = self.movie_dir_logged, None
        if movie_dir is not None:
            self.log_movie(movie_dir)

    def fail(self, path: Path, error: Exception) -> None:
        Action.FAILED.log(f"{path.name}: {error}")
        self.failures.append((path, error))

    def finish(self) -> None:
        if not self.failures:
            return
        log_heading("Failed:", Color.RED)
        for path, error in self.failures:
            console.print(f"  {path}: {error}", style=Color.RED)
        sys.exit(1)
