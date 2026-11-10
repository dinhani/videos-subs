import contextlib
import itertools
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, ClassVar, TypedDict

import questionary
import srt

from models import LlamaModel, NmtModel
from shared.alignment import ALIGNMENT_OVERLAP_MIN, AlignedSubtitle
from shared.console import Action
from shared.files import SUBS_FOLDER, SubtitleFile, SubtitleFormat, move_replacing, parse_srt, write_text_replacing
from shared.subtitles import parse_plain_text, replace_text

BENCH_DIR = Path(__file__).parent
SRC_DIR = BENCH_DIR.parent / "src"
SOURCES_DIR = BENCH_DIR / "1-sources"
REFERENCES_DIR = BENCH_DIR / "2-references"
RUNS_DIR = BENCH_DIR / "3-runs"
SCORES_FOLDER = "_scores"
TIMINGS_FILE = "timings.json"
SCORE_JOBS_FILE = "score-jobs.json"
NMT_REPLIES_FILE = "nmt-replies.json"
LLAMA_LOG_FILE = "llama.log"
RETRANSLATION_FOLDER = "_retranslation"
BACKUPS_FOLDER = "_backups"


@dataclass(frozen=True)
class PipelineTranslator:
    mode: ClassVar[str] = "pipeline"
    model: LlamaModel
    temperatures: tuple[float, ...] = ()
    justify: bool = False

    def serving(self, log: Path) -> contextlib.AbstractContextManager[None]:
        return self.model.serving(log)

    def translate(self, english: Path, translation: Path) -> None:
        options = [
            *(["--temperatures", *map(str, self.temperatures)] if self.temperatures else []),
            *(["--justify"] if self.justify else []),
        ]
        subprocess.run([sys.executable, str(SRC_DIR / "6-translate-subs-llama.py"), str(english), *options], check=True)


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
            translated_entries.append(replace_text(entry, content))

        trace = SubtitleFile.from_path(translation).to_trace()
        trace.parent.mkdir(exist_ok=True)
        write_text_replacing(trace, json.dumps(records, ensure_ascii=False, indent=2))
        write_text_replacing(translation, srt.compose(translated_entries, reindex=False))
        Action.RESPLIT.log(f"{sum(record['resplit'] for record in records)} entries replied with another line count")


@dataclass(frozen=True)
class FixTranslator:
    mode: ClassVar[str] = "fix"
    model: LlamaModel | NmtModel
    base: str

    def serving(self, log: Path) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()

    def translate(self, english: Path, translation: Path) -> None:
        base_translation = self.find_base(translation)
        if not base_translation.exists():
            raise FileNotFoundError(f"{self.base} has not translated {translation.name} yet")
        shutil.copy2(base_translation, translation)
        subprocess.run([sys.executable, str(SRC_DIR / "7-fix-translations.py"), str(translation), "--yes", "--backups", str(RUNS_DIR / BACKUPS_FOLDER)], check=True)

    def find_base(self, translation: Path) -> Path:
        return RUNS_DIR / self.base / translation.parent.name / translation.name

    def find_base_seconds(self, translation: Path) -> int | None:
        return Timings.from_file(RUNS_DIR / self.base / TIMINGS_FILE).find(self.find_base(translation))


class Existing(Enum):
    KEEP = "keep"
    REPLACE = "replace"


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
    library_second_languages: tuple[Path, ...] = ()

    @property
    def movie(self) -> str:
        return self.library_english.parent.parent.name

    @property
    def english(self) -> Path:
        return SOURCES_DIR / self.movie / self.library_english.name

    @property
    def name(self) -> str:
        return SubtitleFile.from_path(self.english).track

    @property
    def second_languages(self) -> tuple[Path, ...]:
        return tuple(SOURCES_DIR / self.movie / file.name for file in self.library_second_languages)

    @property
    def library_nfo(self) -> Path:
        return SubtitleFile.from_path(self.library_english).nfo

    @property
    def nfo(self) -> Path:
        return SOURCES_DIR / self.movie / self.library_nfo.name

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
        for library_file, copy in ((self.library_english, self.english), (self.library_nfo, self.nfo), (self.library_official, self.reference_official.file), *zip(self.library_second_languages, self.second_languages)):
            if library_file is None or copy.exists():
                continue
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(library_file, copy)
            Action.COPY.log(copy.name)

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

        # shift the reference by its offset from the English, then entries that overlap on screen form one segment
        aligned = AlignedSubtitle.from_entries(reference_entries, english=english_entries)
        items = sorted([("en", entry.start, entry.end, entry) for entry in english_entries] + [("ref", entry.start, entry.end, entry) for entry in aligned.entries], key=lambda item: item[1])
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
        Action.ALIGN.log(f"{self.hypothesis.name} with {self.reference.name}: {len(segments)} segments, offset {aligned.offset.total_seconds():.2f}s")
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
        return SubtitleFile.from_path(self.english).to_translation().path

    @property
    def report(self) -> Path:
        return SubtitleFile.from_path(self.translation).to_report()

    def is_translated(self) -> bool:
        return self.translation.exists()

    def is_checked(self) -> bool:
        return self.report.exists()

    def copy_english(self) -> None:
        self.english.parent.mkdir(parents=True, exist_ok=True)
        for source_file in (self.source.english, *self.source.second_languages):
            if not (copy := self.english.parent / source_file.name).exists():
                shutil.copy2(source_file, copy)
        nfo = self.translation.parent / self.source.nfo.name
        if self.source.nfo.exists() and not nfo.exists():
            shutil.copy2(self.source.nfo, nfo)

    def to_retranslation(self) -> "RunTranslation":
        return RunTranslation(run_dir=self.run_dir / RETRANSLATION_FOLDER, source=self.source)

    def replace_with(self, retranslation: "RunTranslation", seconds: int) -> None:
        trace = SubtitleFile.from_path(retranslation.translation).to_trace()
        trace_old = SubtitleFile.from_path(self.translation).to_trace()
        if trace.exists():
            trace_old.parent.mkdir(exist_ok=True)
            move_replacing(trace, trace_old)
        else:
            trace_old.unlink(missing_ok=True)
        for file in (self.report, *(self.to_score(reference) for reference in self.source.find_references())):
            file.unlink(missing_ok=True)
        Timings.from_file(self.run_dir / TIMINGS_FILE).record(self.translation, seconds)
        move_replacing(retranslation.translation, self.translation)

        retranslation.english.unlink()
        if not any(retranslation.run_dir.rglob(f"*{SubtitleFormat.TRANSLATION.extension}")):
            shutil.rmtree(retranslation.run_dir)

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
    translator: PipelineTranslator | NmtTranslator | FixTranslator

    @classmethod
    def from_model(cls, model: LlamaModel | NmtModel) -> "Run":
        match model:
            case LlamaModel():
                return cls(name=model.name, translator=PipelineTranslator(model=model))
            case NmtModel():
                return cls(name=model.name, translator=NmtTranslator(model=model))

    @classmethod
    def from_justification(cls, model: LlamaModel) -> "Run":
        return cls(name=f"{model.name}+why", translator=PipelineTranslator(model=model, justify=True))

    @classmethod
    def from_fixes(cls, base: "Run") -> "Run":
        return cls(name=f"{base.name}-fix", translator=FixTranslator(model=base.model, base=base.name))

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

    def translate(self, sources: Sequence[Source], existing: Existing) -> None:
        with self.translator.serving(log=self.dir / LLAMA_LOG_FILE):
            for source in sources:
                translation = self.get_translation(source)
                if translation.is_translated() and existing is Existing.KEEP:
                    Action.EXISTS.log(f"{self.name}: {translation.translation.name}")
                    continue
                target = translation.to_retranslation() if translation.is_translated() else translation

                seconds = target.find_seconds() if target.is_translated() else None
                if seconds is None:
                    Action.TRANSLATE.log(f"{self.name}: {translation.translation.name}")
                    target.translation.unlink(missing_ok=True)
                    target.copy_english()
                    started = time.perf_counter()
                    self.translator.translate(target.english, target.translation)
                    seconds = round(time.perf_counter() - started)
                    if isinstance(self.translator, FixTranslator):
                        seconds = self.translator.find_base_seconds(target.translation) or seconds
                    Timings.from_file(target.run_dir / TIMINGS_FILE).record(target.translation, seconds)
                else:
                    Action.EXISTS.log(f"{self.name}: {target.translation.name} translated in {RETRANSLATION_FOLDER}")

                if target != translation:
                    translation.replace_with(target, seconds)

    def find_unchecked(self, sources: Sequence[Source]) -> list[Path]:
        return [translation.translation for source in sources if (translation := self.get_translation(source)).is_translated() and not translation.is_checked()]

    def to_scorings(self, sources: Sequence[Source]) -> list[Scoring]:
        return [scoring for source in sources for scoring in self.get_translation(source).to_scorings()]

    def to_choice(self, sources: Sequence[Source], name_width: int) -> questionary.Choice:
        size = self.model.find_bytes()
        title = f"{self.name:<{name_width}}  {self.translator.mode:<8}  {f'{size / 1e9:.1f} GB' if size else '-':>8}  translated {self.count_translated(sources)}/{len(sources)}"
        disabled = "not downloaded" if not self.model.is_downloaded() else None
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
