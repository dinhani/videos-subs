import os
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, ProgressColumn, TextColumn
from rich.text import Text

console = Console(highlight=False, markup=False)


class Color(StrEnum):
    BLUE = "blue"
    CYAN = "cyan"
    GREEN = "green"
    RED = "red"
    YELLOW = "yellow"


def log_action(action: str, color: Color, details: str | Text) -> None:
    console.print(Text.assemble("  ", (f"{action:<9}", color), " ", details))


def progress_bar(*columns: ProgressColumn) -> Progress:
    return Progress(
        TextColumn("  "),
        BarColumn(bar_width=40, style=Color.BLUE, complete_style=Color.GREEN, finished_style=Color.GREEN),
        *columns,
        console=console,
        transient=True,
    )


@dataclass
class Batch:
    failures: list[tuple[Path, Exception]] = field(default_factory=list)
    movie_dir_logged: Path | None = None

    def log_movie(self, movie_dir: Path) -> None:
        if movie_dir == self.movie_dir_logged:
            return
        console.print(movie_dir.name, style=Color.CYAN)
        self.movie_dir_logged = movie_dir

    def clear_screen(self) -> None:
        os.system("cls")
        self.movie_dir_logged = None

    def fail(self, path: Path, error: Exception) -> None:
        log_action("failed", Color.RED, f"{path.name}: {error}")
        self.failures.append((path, error))

    def finish(self) -> None:
        if not self.failures:
            return
        console.print()
        console.print("Failed:", style=Color.RED)
        for path, error in self.failures:
            console.print(f"  {path}: {error}", style=Color.RED)
        sys.exit(1)
