# Benchmarks translation models on the same English sources, through the translate step or through the translation prompt of the model card.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import itertools
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import httpx
import srt
from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table

BENCH_DIR = Path(__file__).parent
SRC_DIR = BENCH_DIR.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from shared.console import Batch, Color, console, log_action, progress_bar
from shared.files import CHECKS_FOLDER, SUBS_FOLDER, TRACES_FOLDER, parse_srt, parse_subtitle_name, write_text_replacing
from shared.reports import REPORT_HEADER
from shared.subtitles import IGNORE_MARKER, SubtitleKind, parse_plain_text

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
SOURCES = (
    Path("Z:/midias/filmes/Fall - 1 - Fall (2022)/_subs/Fall (2022, Web-DL, 1080p, SDR, h264, 5.1, EAC3).embed.3.eng.sdh.srt"),
    Path("Z:/midias/filmes/Hannibal Lecter - 1 - Silence of the Lambs, The (1991)/_subs/The Silence of the Lambs (1991, Web-DL, 2160p, SDR, h265, 5.1, AAC).embed.2.eng.srt"),
)
OFFICIAL_REFERENCES = {
    SOURCES[1]: Path("Z:/midias/filmes/Hannibal Lecter - 1 - Silence of the Lambs, The (1991)/_subs/The Silence of the Lambs (1991, Web-DL, 2160p, SDR, h265, 5.1, AAC).embed.21.pob.srt"),
}
ENGLISH_DIR = BENCH_DIR / "1-english"
REFERENCES_DIR = BENCH_DIR / "2-references"
RUNS_DIR = BENCH_DIR / "3-runs"
COMPARE_DIR = BENCH_DIR / "4-compare"
SCORES_FOLDER = "_scores"
TIMINGS_FILE = "timings.json"
SCORE_JOBS_FILE = "score-jobs.json"
LLAMA_LOG_FILE = "llama.log"

ALIGNMENT_OVERLAP_MIN = timedelta(milliseconds=200)
ALIGNMENT_OFFSET_MAX = timedelta(seconds=2)

MODELS_DIR = Path("D:/AI-Models-LMStudio")
LLAMA_SERVER = Path("C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe")
LLAMA_ENV = {"GGML_CUDA_PDL": "1", "GGML_CUDA_GRAPH_OPT": "1"}
LLAMA_PORT = 9931
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"
LLAMA_LOAD_SECONDS_MAX = 900

NATIVE_WORKERS = 8
NATIVE_TOKENS_MAX = 256

FLAGS_PIPELINE = ("--jinja", "--ctx-size", "16384", "--parallel", "1")
FLAGS_THINKING_OFF = ("--reasoning-budget", "0")
FLAGS_THINKING_SHORT = ("--reasoning-budget", "2048")
FLAGS_NATIVE = ("--jinja", "--ctx-size", "8192", "--parallel", str(NATIVE_WORKERS))
FLAGS_GPU_ONLY = ("--n-gpu-layers", "all", "--fit", "off")
FLAGS_CACHE_Q8 = ("--cache-type-k", "q8_0", "--cache-type-v", "q8_0")
FLAGS_GEMMA = ("--no-mmproj", "--top-p", "0.95", "--top-k", "64")
FLAGS_QWEN = ("--no-mmproj", "--top-p", "0.8", "--top-k", "20", "--min-p", "0")


# ------------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Model:
    name: str
    repo: str
    file: str
    flags: tuple[str, ...]

    @property
    def path(self) -> Path:
        return MODELS_DIR / self.repo / self.file

    def download(self) -> None:
        if self.path.exists():
            log_action("exists", Color.BLUE, self.name)
            return
        from huggingface_hub import hf_hub_download

        log_action("download", Color.GREEN, f"{self.name}: {self.repo}/{self.file}")
        hf_hub_download(repo_id=self.repo, filename=self.file, local_dir=MODELS_DIR / self.repo)

    @contextmanager
    def serving(self, log: Path) -> Generator[None]:
        if not self.path.exists():
            raise FileNotFoundError(f"{self.name} not downloaded: {self.path}")
        try:
            props = httpx.get(f"{LLAMA_URL}/props", timeout=10).json()
            raise RuntimeError(f"port {LLAMA_PORT} is used by a llama server running {Path(props.get('model_path', '?')).name}; stop it first")
        except httpx.TransportError:
            pass

        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as output:
            log_action("start", Color.GREEN, f"llama server for {self.name}, log in {log}")
            server = subprocess.Popen(
                [str(LLAMA_SERVER), "--model", str(self.path), "--device", "CUDA0", "--flash-attn", "on", "--port", str(LLAMA_PORT), *self.flags],
                env={**os.environ, **LLAMA_ENV},
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                started = time.monotonic()
                while True:
                    try:
                        if httpx.get(f"{LLAMA_URL}/health", timeout=5).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    if server.poll() is not None:
                        raise RuntimeError(f"llama server exited with code {server.returncode}, see {log}")
                    if time.monotonic() - started > LLAMA_LOAD_SECONDS_MAX:
                        raise TimeoutError(f"llama server did not load {self.name} in {LLAMA_LOAD_SECONDS_MAX} seconds, see {log}")
                    time.sleep(2)
                yield
            finally:
                server.terminate()
                server.wait()
                log_action("stop", Color.GREEN, f"llama server for {self.name}")


# ------------------------------------------------------------------------------
# Translations
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class PipelineTranslation:
    def describe(self) -> str:
        return "pipeline"

    def translate(self, source: Path, translation: Path) -> None:
        subprocess.run([sys.executable, str(SRC_DIR / "6-translate-subs.py"), str(source)], check=True)


@dataclass(frozen=True)
class NativeTranslation:
    content: Callable[[str], str | list[dict[str, str]]]
    sampling: dict[str, float] = field(default_factory=lambda: {"temperature": 0.0})

    def describe(self) -> str:
        return "native"

    def translate(self, source: Path, translation: Path) -> None:
        entries = parse_srt(source)
        entries_lines = [[" ".join(line.split()) for line in entry.content.split("\n") if line.strip()] for entry in entries]

        # translate each entry on its own
        with httpx.Client(base_url=LLAMA_URL, timeout=600) as client, ThreadPoolExecutor(NATIVE_WORKERS) as pool:
            def complete(entry_lines: list[str]) -> str:
                if not entry_lines:
                    return ""
                resp = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": self.content("\n".join(entry_lines))}], "max_tokens": NATIVE_TOKENS_MAX, **self.sampling})
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"] or ""

            with progress_bar(MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn()) as progress:
                replies = list(progress.track(pool.map(complete, entries_lines), total=len(entries_lines)))

        # split a reply with another line count by words, in proportion to the English lines
        records = []
        translated_entries = []
        for entry, entry_lines, reply in zip(entries, entries_lines, replies):
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


# ------------------------------------------------------------------------------
# Sources and references
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Reference:
    name: str
    file: Path


@dataclass(frozen=True)
class Source:
    english: Path

    @classmethod
    def find_all(cls) -> list["Source"]:
        return [cls(english=english) for english in sorted(ENGLISH_DIR.glob("*/*.srt"))]

    @property
    def movie(self) -> str:
        return self.english.parent.name

    @property
    def name(self) -> str:
        return parse_subtitle_name(self.english)

    @property
    def title(self) -> str:
        return self.name.split(" (")[0]

    @property
    def reference_official(self) -> Reference:
        return Reference(name="official", file=REFERENCES_DIR / self.movie / f"{self.name}.official.srt")

    @property
    def reference_claude(self) -> Reference:
        return Reference(name="claude", file=REFERENCES_DIR / self.movie / f"{self.name}.claude.srt")

    @property
    def score_claude_official(self) -> Path:
        return REFERENCES_DIR / self.movie / SCORES_FOLDER / f"{self.name}.claude.official.json"

    def find_references(self) -> list[Reference]:
        return [reference for reference in (self.reference_official, self.reference_claude) if reference.file.exists()]


@dataclass(frozen=True)
class Scoring:
    english: Path
    hypothesis: Path
    reference: Reference
    output: Path


def score_translations(scorings: list[Scoring]) -> None:
    jobs = []
    for scoring in scorings:
        if scoring.output.exists():
            continue
        hypothesis = {entry.index: entry for entry in parse_srt(scoring.hypothesis)}
        english_entries = parse_srt(scoring.english)
        reference_entries = parse_srt(scoring.reference.file)

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

        segments = []
        for block in blocks:
            english = [entry for side, _, _, entry in block if side == "en"]
            reference = [entry for side, _, _, entry in block if side == "ref"]
            if not english or not reference:
                continue
            segments.append({
                "src": " ".join(" ".join(parse_plain_text(entry).split()) for entry in english),
                "mt": " ".join(" ".join(parse_plain_text(hypothesis[entry.index]).split()) for entry in english if entry.index in hypothesis),
                "ref": " ".join(" ".join(parse_plain_text(entry).split()) for entry in reference),
            })
        jobs.append({"output": str(scoring.output), "segments": segments})
        log_action("align", Color.GREEN, f"{scoring.hypothesis.name} with {scoring.reference.name}: {len(segments)} segments, offset {offset.total_seconds():.2f}s")

    if not jobs:
        return
    jobs_file = RUNS_DIR / SCORE_JOBS_FILE
    write_text_replacing(jobs_file, json.dumps(jobs, ensure_ascii=False))
    subprocess.run(["uv", "run", "--script", str(BENCH_DIR / "score.py"), str(jobs_file)], check=True)
    jobs_file.unlink()


# ------------------------------------------------------------------------------
# Runs
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class RunTranslation:
    source: Source
    english: Path
    translation: Path
    report: Path

    def to_score(self, reference: Reference) -> Path:
        return self.translation.parent / SCORES_FOLDER / f"{self.translation.stem}.{reference.name}.json"


@dataclass(frozen=True)
class Run:
    name: str
    model: Model
    translation: PipelineTranslation | NativeTranslation

    @property
    def dir(self) -> Path:
        return RUNS_DIR / self.name

    def find_translations(self) -> list[RunTranslation]:
        translations = []
        for source in Source.find_all():
            english = self.dir / source.movie / SUBS_FOLDER / source.english.name
            translation = english.parent.parent / f"{source.name}{SubtitleKind.from_file(english).to_translation_suffix()}"
            translations.append(RunTranslation(source=source, english=english, translation=translation, report=translation.parent / CHECKS_FOLDER / f"{translation.stem}.txt"))
        return translations

    def is_evaluated(self) -> bool:
        return all(translation.report.exists() and all(translation.to_score(reference).exists() for reference in translation.source.find_references()) for translation in self.find_translations())

    def evaluate(self) -> None:
        if not all(translation.translation.exists() for translation in self.find_translations()):
            with self.model.serving(log=self.dir / LLAMA_LOG_FILE):
                self.translate()
        self.check()
        self.score()

    def translate(self) -> None:
        timings_file = self.dir / TIMINGS_FILE
        timings: dict[str, int] = json.loads(timings_file.read_text(encoding="utf-8")) if timings_file.exists() else {}

        for translation in self.find_translations():
            if translation.translation.exists():
                log_action("exists", Color.BLUE, f"{self.name}: {translation.translation.name}")
                continue
            log_action("translate", Color.GREEN, f"{self.name}: {translation.translation.name}")
            translation.english.parent.mkdir(parents=True, exist_ok=True)
            if not translation.english.exists():
                shutil.copy2(translation.source.english, translation.english)

            started = time.perf_counter()
            self.translation.translate(translation.english, translation.translation)
            timings[translation.translation.name] = round(time.perf_counter() - started)
            write_text_replacing(timings_file, json.dumps(timings, ensure_ascii=False, indent=2))

    def check(self) -> None:
        subprocess.run([sys.executable, str(SRC_DIR / "8-check-translations.py"), str(self.dir)], check=True)

    def score(self) -> None:
        score_translations([
            Scoring(english=translation.source.english, hypothesis=translation.translation, reference=reference, output=translation.to_score(reference))
            for translation in self.find_translations()
            if translation.translation.exists()
            for reference in translation.source.find_references()
        ])


GEMMA_4_26B = Model(name="gemma-4-26b", repo="lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF", file="gemma-4-26B-A4B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
GEMMA_4_26B_THINKING = Model(name="gemma-4-26b-thinking", repo="lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF", file="gemma-4-26B-A4B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_SHORT, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
GEMMA_4_12B = Model(name="gemma-4-12b", repo="lmstudio-community/gemma-4-12B-it-QAT-GGUF", file="gemma-4-12B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
MISTRAL_SMALL_3_2_24B = Model(name="mistral-small-3.2-24b", repo="bartowski/mistralai_Mistral-Small-3.2-24B-Instruct-2506-GGUF", file="mistralai_Mistral-Small-3.2-24B-Instruct-2506-IQ4_XS.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8))
MINISTRAL_3_14B = Model(name="ministral-3-14b", repo="bartowski/mistralai_Ministral-3-14B-Instruct-2512-GGUF", file="mistralai_Ministral-3-14B-Instruct-2512-Q6_K.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY))
QWEN_3_14B = Model(name="qwen-3-14b", repo="bartowski/Qwen_Qwen3-14B-GGUF", file="Qwen_Qwen3-14B-Q6_K.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8, *FLAGS_QWEN))
QWEN_3_5_9B = Model(name="qwen-3.5-9b", repo="lmstudio-community/Qwen3.5-9B-GGUF", file="Qwen3.5-9B-Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_QWEN))

HY_MT2_7B = Model(name="hy-mt2-7b", repo="tencent/Hy-MT2-7B-GGUF", file="HY-MT2-7B-Q8_0.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))
TOWER_PLUS_9B = Model(name="tower-plus-9b", repo="mradermacher/Tower-Plus-9B-GGUF", file="Tower-Plus-9B.Q8_0.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))
TRANSLATEGEMMA_12B = Model(name="translategemma-12b", repo="mradermacher/translategemma-12b-it-GGUF", file="translategemma-12b-it.Q6_K.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))

MODELS = (GEMMA_4_26B, GEMMA_4_26B_THINKING, GEMMA_4_12B, MISTRAL_SMALL_3_2_24B, MINISTRAL_3_14B, QWEN_3_14B, QWEN_3_5_9B, HY_MT2_7B, TOWER_PLUS_9B, TRANSLATEGEMMA_12B)

RUNS = (
    Run(name="gemma-4-26b", model=GEMMA_4_26B, translation=PipelineTranslation()),
    Run(name="gemma-4-26b-thinking", model=GEMMA_4_26B_THINKING, translation=PipelineTranslation()),
    Run(name="gemma-4-12b", model=GEMMA_4_12B, translation=PipelineTranslation()),
    Run(name="mistral-small-3.2-24b", model=MISTRAL_SMALL_3_2_24B, translation=PipelineTranslation()),
    Run(name="ministral-3-14b", model=MINISTRAL_3_14B, translation=PipelineTranslation()),
    Run(name="qwen-3-14b", model=QWEN_3_14B, translation=PipelineTranslation()),
    Run(name="qwen-3.5-9b", model=QWEN_3_5_9B, translation=PipelineTranslation()),
    Run(
        name="hy-mt2-7b",
        model=HY_MT2_7B,
        translation=NativeTranslation(
            content=lambda text: f"Translate the following text into Brazilian Portuguese. Note that you should only output the translated result without any additional explanation:\n\n{text}",
            sampling={"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05},
        ),
    ),
    Run(
        name="tower-plus-9b",
        model=TOWER_PLUS_9B,
        translation=NativeTranslation(content=lambda text: f"Translate the following English source text to Portuguese (Brazilian):\nEnglish: {text}\nPortuguese (Brazilian): "),
    ),
    Run(
        name="translategemma-12b",
        model=TRANSLATEGEMMA_12B,
        translation=NativeTranslation(content=lambda text: [{"type": "text", "source_lang_code": "en", "target_lang_code": "pt-BR", "text": text}]),
    ),
)


# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    models = {model.name: model for model in MODELS}
    runs = {run.name: run for run in RUNS}

    parser = argparse.ArgumentParser(description="Benchmarks translation models on the same English sources.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("all", help="copy the sources and evaluate every run not evaluated yet, each with its own llama server")
    commands.add_parser("runs", help="list the runs and what each one has done")
    command_download = commands.add_parser("download", help="download models from Hugging Face")
    command_download.add_argument("models", nargs="*", help=f"models to download: {', '.join(models)} (default: all)")
    commands.add_parser("report", help="show the scores, issues and time of every run")
    commands.add_parser("compare", help="write the entries of every run side by side")
    arguments = parser.parse_args()

    match arguments.command:
        case "all":
            for library_file, snapshot in [(english, ENGLISH_DIR / english.parent.parent.name / english.name) for english in SOURCES] + [(official, Source(english=ENGLISH_DIR / english.parent.parent.name / english.name).reference_official.file) for english, official in OFFICIAL_REFERENCES.items()]:
                if not snapshot.exists():
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(library_file, snapshot)
                    log_action("copy", Color.GREEN, snapshot.name)

            score_translations([
                Scoring(english=source.english, hypothesis=source.reference_claude.file, reference=source.reference_official, output=source.score_claude_official)
                for source in Source.find_all()
                if source.reference_claude.file.exists() and source.reference_official.file.exists()
            ])

            batch = Batch()
            for run in RUNS:
                if run.is_evaluated():
                    log_action("evaluated", Color.BLUE, run.name)
                    continue
                log_action("evaluate", Color.GREEN, run.name)
                try:
                    run.evaluate()
                except Exception as error:
                    batch.fail(run.dir, error)
            batch.finish()

        case "runs":
            table = Table("run", "model", "translation", "downloaded", "translated", "checked", "scored")
            for run in RUNS:
                translations = run.find_translations()
                translated = sum(translation.translation.exists() for translation in translations)
                checked = sum(translation.report.exists() for translation in translations)
                scores = [translation.to_score(reference) for translation in translations for reference in translation.source.find_references()]
                table.add_row(run.name, run.model.name, run.translation.describe(), "yes" if run.model.path.exists() else "no", f"{translated}/{len(translations)}", f"{checked}/{len(translations)}", f"{sum(score.exists() for score in scores)}/{len(scores)}")
            console.print(table)

        case "download":
            if unknown := set(arguments.models) - set(models):
                parser.error(f"unknown models: {', '.join(sorted(unknown))}")
            for name in arguments.models or models:
                models[name].download()

        case "report":
            table_scores = Table("run", "movie", "check score", "issues", "minutes", "comet official", "chrf++ official", "comet claude", "chrf++ claude")
            issues_by_kind: dict[str, dict[str, int]] = {}
            for run in RUNS:
                timings_file = run.dir / TIMINGS_FILE
                timings: dict[str, int] = json.loads(timings_file.read_text(encoding="utf-8")) if timings_file.exists() else {}
                for translation in run.find_translations():
                    if not translation.report.exists():
                        continue
                    report_lines = translation.report.read_text(encoding="utf-8").splitlines()
                    header = REPORT_HEADER.match(report_lines[1])
                    if header is None:
                        raise ValueError(f"report without a score: {translation.report}")
                    scores = []
                    for reference in (translation.source.reference_official, translation.source.reference_claude):
                        score_file = translation.to_score(reference)
                        score = json.loads(score_file.read_text(encoding="utf-8")) if score_file.exists() else {}
                        scores += [str(score.get("comet", "-")), str(score.get("chrf", "-"))]
                    minutes = f"{timings[translation.translation.name] / 60:.1f}" if translation.translation.name in timings else "-"
                    table_scores.add_row(run.name, translation.source.title, header[1], header[2], minutes, *scores)
                    for line in itertools.takewhile(str.strip, report_lines[3:]):
                        issues_by_kind.setdefault(line[7:28].strip(), {})[run.name] = issues_by_kind.get(line[7:28].strip(), {}).get(run.name, 0) + int(line[2:6])
            for source in Source.find_all():
                if source.score_claude_official.exists():
                    score = json.loads(source.score_claude_official.read_text(encoding="utf-8"))
                    table_scores.add_row("claude", source.title, "-", "-", "-", str(score["comet"]), str(score["chrf"]), "-", "-")
            console.print(table_scores)

            runs_reported = [run.name for run in RUNS if any(run.name in counts for counts in issues_by_kind.values())]
            table_kinds = Table("issue", *runs_reported)
            for kind, counts in issues_by_kind.items():
                table_kinds.add_row(kind, *(str(counts.get(name, 0)) for name in runs_reported))
            console.print(table_kinds)

        case "compare":
            COMPARE_DIR.mkdir(exist_ok=True)
            width = max(len(name) for name in [*runs, "official", "claude"])
            for source in Source.find_all():
                translated_by_run: dict[str, dict[int, str]] = {}
                for run in RUNS:
                    translation = next(translation.translation for translation in run.find_translations() if translation.source == source)
                    if translation.exists():
                        translated_by_run[run.name] = {entry.index: " / ".join(entry.content.splitlines()) for entry in parse_srt(translation)}
                if source.reference_claude.file.exists():
                    translated_by_run["claude"] = {entry.index: " / ".join(entry.content.splitlines()) for entry in parse_srt(source.reference_claude.file)}
                official = parse_srt(source.reference_official.file) if source.reference_official.file.exists() else []

                lines = []
                for entry in parse_srt(source.english):
                    lines += ["", f"{entry.index}  {srt.timedelta_to_srt_timestamp(entry.start)}", f"  {'en':<{width}}  {' / '.join(entry.content.splitlines())}"]
                    if official:
                        lines.append(f"  {'official':<{width}}  {' | '.join(' / '.join(other.content.splitlines()) for other in official if entry.start <= other.start + (other.end - other.start) / 2 < entry.end)}")
                    lines += [f"  {name:<{width}}  {translated.get(entry.index, '')}" for name, translated in translated_by_run.items()]
                compare = COMPARE_DIR / f"{source.name}.txt"
                write_text_replacing(compare, "\n".join(lines).lstrip() + "\n")
                log_action("compare", Color.GREEN, f"{compare.name}: {', '.join([*(['official'] if official else []), *translated_by_run])}")
