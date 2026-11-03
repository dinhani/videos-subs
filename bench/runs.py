import contextlib
import itertools
import json
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, TypedDict

import questionary
import srt

from models import LlamaModel, NmtModel
from shared.console import Color, log_action
from shared.files import CHECKS_FOLDER, SUBS_FOLDER, TRACES_FOLDER, parse_srt, parse_subtitle_name, write_text_replacing
from shared.llm import ReplyFormat
from shared.subtitles import IGNORE_MARKER, SubtitleKind, parse_plain_text

BENCH_DIR = Path(__file__).parent
SRC_DIR = BENCH_DIR.parent / "src"
ENGLISH_DIR = BENCH_DIR / "1-english"
REFERENCES_DIR = BENCH_DIR / "2-references"
RUNS_DIR = BENCH_DIR / "3-runs"
SCORES_FOLDER = "_scores"
TIMINGS_FILE = "timings.json"
SCORE_JOBS_FILE = "score-jobs.json"
NMT_REPLIES_FILE = "nmt-replies.json"
LLAMA_LOG_FILE = "llama.log"

ALIGNMENT_OVERLAP_MIN = timedelta(milliseconds=200)
ALIGNMENT_OFFSET_MAX = timedelta(seconds=2)


@dataclass(frozen=True)
class PipelineTranslator:
    mode: ClassVar[str] = "pipeline"
    model: LlamaModel
    temperatures: tuple[float, ...] = ()
    prompt: Path | None = None
    reply: ReplyFormat = ReplyFormat.ENGLISH_AND_PORTUGUESE

    def serving(self, log: Path) -> contextlib.AbstractContextManager[None]:
        return self.model.serving(log)

    def translate(self, english: Path, translation: Path) -> None:
        options = [
            *(["--temperatures", *map(str, self.temperatures)] if self.temperatures else []),
            *(["--prompt", str(self.prompt)] if self.prompt else []),
            "--reply", self.reply,
        ]
        subprocess.run([sys.executable, str(SRC_DIR / "6-translate-subs.py"), str(english), *options], check=True)


@dataclass(frozen=True)
class NmtTranslator:
    mode: ClassVar[str] = "nmt"
    model: NmtModel

    def serving(self, log: Path) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def translate(self, english: Path, translation: Path) -> None:
        if not self.model.is_downloaded():
            raise FileNotFoundError(f"{self.model.name} not downloaded: {self.model.path}")
        entries = parse_srt(english)
        replies_file = RUNS_DIR / NMT_REPLIES_FILE
        subprocess.run(["uv", "run", "--script", str(BENCH_DIR / "nmt.py"), "--family", self.model.family, "--model", str(self.model.path), str(english), str(replies_file)], check=True)
        replies_by_index: dict[str, str] = json.loads(replies_file.read_text(encoding="utf-8"))
        replies_file.unlink()

        # split a reply with another line count by words, in proportion to the English lines
        records = []
        translated_entries = []
        for entry in entries:
            reply = replies_by_index.get(str(entry.index), "")
            entry_lines = [" ".join(line.split()) for line in entry.content.split("\n") if line.strip()]
            translated_lines = [line.strip() for line in reply.split("\n") if line.strip()]
            resplit = bool(entry_lines) and len(translated_lines) != len(entry_lines)
            if resplit:
                words = " ".join(translated_lines).split()
                characters = sum(len(line) for line in entry_lines)
                cuts = [0, *(round(len(words) * share / characters) for share in itertools.accumulate(len(line) for line in entry_lines))]
                translated_lines = [" ".join(words[start:end]) for start, end in itertools.pairwise(cuts)]
            records.append({"entry": entry.index, "en": entry_lines, "reply": reply, "resplit": resplit})
            content = "\n".join(translated_lines) if entry_lines else entry.content
            translated_entries.append(srt.Subtitle(index=entry.index, start=entry.start, end=entry.end, content=content, proprietary=IGNORE_MARKER.sub("", entry.proprietary).strip()))

        trace = translation.parent / TRACES_FOLDER / f"{translation.stem}.json"
        trace.parent.mkdir(exist_ok=True)
        write_text_replacing(trace, json.dumps(records, ensure_ascii=False, indent=2))
        write_text_replacing(translation, srt.compose(translated_entries, reindex=False))
        log_action("resplit", Color.YELLOW, f"{sum(record['resplit'] for record in records)} entries replied with another line count")


class ReferenceName(StrEnum):
    CLAUDE = "claude"
    OFFICIAL = "official"


@dataclass(frozen=True)
class Reference:
    name: ReferenceName
    file: Path


@dataclass(frozen=True)
class Source:
    library_english: Path
    library_official: Path | None = None

    @property
    def movie(self) -> str:
        return self.library_english.parent.parent.name

    @property
    def english(self) -> Path:
        return ENGLISH_DIR / self.movie / self.library_english.name

    @property
    def name(self) -> str:
        return parse_subtitle_name(self.english)

    @property
    def title(self) -> str:
        return self.name.split(" (")[0]

    @property
    def reference_claude(self) -> Reference:
        return Reference(name=ReferenceName.CLAUDE, file=REFERENCES_DIR / self.movie / f"{self.name}.claude.srt")

    @property
    def reference_official(self) -> Reference:
        return Reference(name=ReferenceName.OFFICIAL, file=REFERENCES_DIR / self.movie / f"{self.name}.official.srt")

    @property
    def score_claude_official(self) -> Path:
        return REFERENCES_DIR / self.movie / SCORES_FOLDER / f"{self.name}.claude.official.json"

    def copy_from_library(self) -> None:
        for library_file, copy in ((self.library_english, self.english), (self.library_official, self.reference_official.file)):
            if library_file is None or copy.exists():
                continue
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(library_file, copy)
            log_action("copy", Color.GREEN, copy.name)

    def find_references(self) -> list[Reference]:
        return [reference for reference in (self.reference_claude, self.reference_official) if reference.file.exists()]

    def find_scoring_claude(self) -> "Scoring | None":
        if not (self.reference_claude.file.exists() and self.reference_official.file.exists()):
            return None
        return Scoring(english=self.english, hypothesis=self.reference_claude.file, reference=self.reference_official, output=self.score_claude_official)

    def find_score_claude(self) -> dict[str, float] | None:
        return find_json(self.score_claude_official)


class ScoringSegment(TypedDict):
    src: str
    mt: str
    ref: str


class ScoringJob(TypedDict):
    output: str
    segments: list[ScoringSegment]


@dataclass(frozen=True)
class Scoring:
    english: Path
    hypothesis: Path
    reference: Reference
    output: Path

    def to_job(self) -> ScoringJob:
        hypothesis = {entry.index: entry for entry in parse_srt(self.hypothesis)}
        english_entries = parse_srt(self.english)
        reference_entries = parse_srt(self.reference.file)

        # shift the reference by its median offset from the English, then entries that overlap on screen form one segment
        reference_starts = [entry.start for entry in reference_entries]
        differences = [difference for entry in english_entries if abs(difference := min(reference_starts, key=lambda start: abs(start - entry.start)) - entry.start) < ALIGNMENT_OFFSET_MAX]
        offset = statistics.median(differences) if differences else timedelta(0)
        items = sorted([("en", entry.start, entry.end, entry) for entry in english_entries] + [("ref", entry.start - offset, entry.end - offset, entry) for entry in reference_entries], key=lambda item: item[1])
        blocks: list[list[tuple[str, timedelta, timedelta, srt.Subtitle]]] = []
        block_end = None
        for item in items:
            if block_end is None or item[1] >= block_end - ALIGNMENT_OVERLAP_MIN:
                blocks.append([])
                block_end = item[2]
            blocks[-1].append(item)
            block_end = max(block_end, item[2])

        segments: list[ScoringSegment] = []
        for block in blocks:
            english = [entry for side, _, _, entry in block if side == "en"]
            reference = [entry for side, _, _, entry in block if side == "ref"]
            if not english or not reference:
                continue
            segments.append({
                "src": " ".join(to_plain_line(entry) for entry in english),
                "mt": " ".join(to_plain_line(hypothesis[entry.index]) for entry in english if entry.index in hypothesis),
                "ref": " ".join(to_plain_line(entry) for entry in reference),
            })
        log_action("align", Color.GREEN, f"{self.hypothesis.name} with {self.reference.name}: {len(segments)} segments, offset {offset.total_seconds():.2f}s")
        return {"output": str(self.output), "segments": segments}


@dataclass
class Timings:
    file: Path
    seconds_by_translation: dict[str, int]

    @classmethod
    def from_file(cls, file: Path) -> "Timings":
        return cls(file=file, seconds_by_translation=find_json(file) or {})

    def find(self, translation: Path) -> int | None:
        return self.seconds_by_translation.get(translation.name)

    def record(self, translation: Path, seconds: int) -> None:
        self.seconds_by_translation[translation.name] = seconds
        write_text_replacing(self.file, json.dumps(self.seconds_by_translation, ensure_ascii=False, indent=2))


@dataclass(frozen=True)
class RunTranslation:
    run_dir: Path
    source: Source

    @property
    def english(self) -> Path:
        return self.run_dir / self.source.movie / SUBS_FOLDER / self.source.english.name

    @property
    def translation(self) -> Path:
        return self.run_dir / self.source.movie / f"{self.source.name}{SubtitleKind.from_file(self.english).to_translation_suffix()}"

    @property
    def report(self) -> Path:
        return self.translation.parent / CHECKS_FOLDER / f"{self.translation.stem}.txt"

    def is_translated(self) -> bool:
        return self.translation.exists()

    def is_checked(self) -> bool:
        return self.report.exists()

    def copy_english(self) -> None:
        self.english.parent.mkdir(parents=True, exist_ok=True)
        if not self.english.exists():
            shutil.copy2(self.source.english, self.english)

    def to_score(self, reference: Reference) -> Path:
        return self.translation.parent / SCORES_FOLDER / f"{self.translation.stem}.{reference.name}.json"

    def to_scorings(self) -> list[Scoring]:
        if not self.is_translated():
            return []
        return [Scoring(english=self.source.english, hypothesis=self.translation, reference=reference, output=self.to_score(reference)) for reference in self.source.find_references()]

    def find_scores(self, reference: Reference) -> dict[str, float]:
        return find_json(self.to_score(reference)) or {}

    def find_seconds(self) -> int | None:
        return Timings.from_file(self.run_dir / TIMINGS_FILE).find(self.translation)


@dataclass(frozen=True)
class Run:
    name: str
    translator: PipelineTranslator | NmtTranslator

    @classmethod
    def from_model(cls, model: LlamaModel | NmtModel) -> "Run":
        match model:
            case LlamaModel():
                return cls(name=model.name, translator=PipelineTranslator(model=model))
            case NmtModel():
                return cls(name=model.name, translator=NmtTranslator(model=model))

    @classmethod
    def from_prompt(cls, model: LlamaModel, prompt: Path) -> "Run":
        return cls(name=f"{model.name}+{prompt.stem}", translator=PipelineTranslator(model=model, prompt=prompt))

    @property
    def model(self) -> LlamaModel | NmtModel:
        return self.translator.model

    @property
    def dir(self) -> Path:
        return RUNS_DIR / self.name

    def get_translation(self, source: Source) -> RunTranslation:
        return RunTranslation(run_dir=self.dir, source=source)

    def count_translated(self, sources: Sequence[Source]) -> int:
        return sum(self.get_translation(source).is_translated() for source in sources)

    def is_translated(self, sources: Sequence[Source]) -> bool:
        return self.count_translated(sources) == len(sources)

    def translate(self, sources: Sequence[Source]) -> None:
        timings = Timings.from_file(self.dir / TIMINGS_FILE)
        with self.translator.serving(log=self.dir / LLAMA_LOG_FILE):
            for source in sources:
                translation = self.get_translation(source)
                if translation.is_translated():
                    log_action("exists", Color.BLUE, f"{self.name}: {translation.translation.name}")
                    continue
                log_action("translate", Color.GREEN, f"{self.name}: {translation.translation.name}")
                translation.copy_english()

                started = time.perf_counter()
                self.translator.translate(translation.english, translation.translation)
                timings.record(translation.translation, round(time.perf_counter() - started))

    def check(self) -> None:
        subprocess.run([sys.executable, str(SRC_DIR / "8-check-translations.py"), str(self.dir)], check=True)

    def to_scorings(self, sources: Sequence[Source]) -> list[Scoring]:
        return [scoring for source in sources for scoring in self.get_translation(source).to_scorings()]

    def to_choice(self, sources: Sequence[Source], name_width: int) -> questionary.Choice:
        size = self.model.find_bytes()
        title = f"{self.name:<{name_width}}  {self.translator.mode:<8}  {f'{size / 1e9:.1f} GB' if size else '-':>8}  translated {self.count_translated(sources)}/{len(sources)}"
        disabled = "translated" if self.is_translated(sources) else "not downloaded" if not self.model.is_downloaded() else None
        return questionary.Choice(title=title, value=self.name, disabled=disabled)

    def to_status_row(self, sources: Sequence[Source]) -> tuple[str, ...]:
        translations = [self.get_translation(source) for source in sources]
        checked = sum(translation.is_checked() for translation in translations)
        scores = [translation.to_score(reference) for translation in translations for reference in translation.source.find_references()]
        return (
            self.name,
            self.model.name,
            self.translator.mode,
            "yes" if self.model.is_downloaded() else "no",
            f"{self.count_translated(sources)}/{len(sources)}",
            f"{checked}/{len(sources)}",
            f"{sum(score.exists() for score in scores)}/{len(scores)}",
        )


def find_json(file: Path) -> Any | None:
    return json.loads(file.read_text(encoding="utf-8")) if file.exists() else None


def to_plain_line(entry: srt.Subtitle) -> str:
    return " ".join(parse_plain_text(entry).split())
