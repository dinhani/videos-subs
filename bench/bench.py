# Benchmarks translation models on the same English sources, through the translate step or through the translation prompt of the model card.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import itertools
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

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
from shared.subtitles import IGNORE_MARKER, SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
SOURCES = (
    Path("Z:/midias/filmes/Fall - 1 - Fall (2022)/_subs/Fall (2022, Web-DL, 1080p, SDR, h264, 5.1, EAC3).embed.3.eng.sdh.srt"),
)
SOURCES_DIR = BENCH_DIR / "sources"
RUNS_DIR = BENCH_DIR / "runs"
COMPARE_DIR = BENCH_DIR / "compare"
BACKUPS_DIR = BENCH_DIR / "_backups"
TIMINGS_FILE = "timings.json"
LLAMA_LOG_FILE = "llama.log"

MODELS_DIR = Path("D:/AI-Models-LMStudio")
LLAMA_SERVER = Path("C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe")
LLAMA_ENV = {"GGML_CUDA_PDL": "1", "GGML_CUDA_GRAPH_OPT": "1"}
LLAMA_PORT = 9931
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"
LLAMA_LOAD_SECONDS_MAX = 900

NATIVE_WORKERS = 8
NATIVE_TOKENS_MAX = 256

FLAGS_PIPELINE = ("--jinja", "--reasoning-budget", "0", "--ctx-size", "16384", "--parallel", "1")
FLAGS_NATIVE = ("--jinja", "--ctx-size", "8192", "--parallel", str(NATIVE_WORKERS))
FLAGS_GPU_ONLY = ("--n-gpu-layers", "all", "--fit", "off")
FLAGS_GPU_FIT = ("--fit", "on")
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

    def start(self, log: IO[bytes] | None = None) -> subprocess.Popen[bytes]:
        if not self.path.exists():
            raise FileNotFoundError(f"{self.name} not downloaded: {self.path}")
        return subprocess.Popen(
            [str(LLAMA_SERVER), "--model", str(self.path), "--device", "CUDA0", "--flash-attn", "on", "--port", str(LLAMA_PORT), *self.flags],
            env={**os.environ, **LLAMA_ENV},
            stdout=log,
            stderr=subprocess.STDOUT if log else None,
        )

    @contextmanager
    def serving(self, log: Path) -> Generator[None]:
        served = find_served_file()
        if served == self.file:
            yield
            return
        if served is not None:
            raise RuntimeError(f"port {LLAMA_PORT} is used by a llama server running {served}; stop it first")

        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as output:
            log_action("start", Color.GREEN, f"llama server for {self.name}, log in {log}")
            server = self.start(log=output)
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

    def ensure_served(self) -> None:
        served = find_served_file()
        if served is None:
            raise SystemExit(f"llama server unreachable at {LLAMA_URL}; start it with: just llama {self.name}")
        if served != self.file:
            raise SystemExit(f"llama server runs {served}, not {self.file}; start it with: just llama {self.name}")


def find_served_file() -> str | None:
    try:
        props = httpx.get(f"{LLAMA_URL}/props", timeout=10).json()
        return Path(props["model_path"]).name if "model_path" in props else None
    except httpx.TransportError:
        return None


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
# Runs
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Run:
    name: str
    model: Model
    translation: PipelineTranslation | NativeTranslation

    @property
    def dir(self) -> Path:
        return RUNS_DIR / self.name

    def find_translations(self) -> list[tuple[Path, Path, Path]]:
        triples = []
        for snapshot in sorted(SOURCES_DIR.glob(f"*/{SUBS_FOLDER}/*.srt")):
            source = self.dir / snapshot.parent.parent.name / SUBS_FOLDER / snapshot.name
            translation = source.parent.parent / f"{parse_subtitle_name(source)}{SubtitleKind.from_file(source).to_translation_suffix()}"
            triples.append((source, translation, translation.parent / CHECKS_FOLDER / f"{translation.stem}.txt"))
        return triples

    def is_evaluated(self) -> bool:
        return all(report.exists() for _, _, report in self.find_translations())

    def evaluate(self) -> None:
        with self.model.serving(log=self.dir / LLAMA_LOG_FILE):
            self.translate()
        self.fix()
        self.check()

    def translate(self) -> None:
        self.model.ensure_served()
        timings_file = self.dir / TIMINGS_FILE
        timings: dict[str, int] = json.loads(timings_file.read_text(encoding="utf-8")) if timings_file.exists() else {}

        for source, translation, _ in self.find_translations():
            if translation.exists():
                log_action("exists", Color.BLUE, f"{self.name}: {translation.name}")
                continue
            log_action("translate", Color.GREEN, f"{self.name}: {translation.name}")
            source.parent.mkdir(parents=True, exist_ok=True)
            if not source.exists():
                shutil.copy2(SOURCES_DIR / source.parent.parent.name / SUBS_FOLDER / source.name, source)

            started = time.perf_counter()
            self.translation.translate(source, translation)
            timings[translation.name] = round(time.perf_counter() - started)
            write_text_replacing(timings_file, json.dumps(timings, ensure_ascii=False, indent=2))

    def fix(self) -> None:
        subprocess.run([sys.executable, str(SRC_DIR / "7-fix-translations.py"), "--yes", "--backups", str(BACKUPS_DIR), str(self.dir)], check=True)

    def check(self) -> None:
        subprocess.run([sys.executable, str(SRC_DIR / "8-check-translations.py"), str(self.dir)], check=True)


GEMMA_4_26B = Model(name="gemma-4-26b", repo="lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF", file="gemma-4-26B-A4B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
GEMMA_4_31B = Model(name="gemma-4-31b", repo="lmstudio-community/gemma-4-31B-it-QAT-GGUF", file="gemma-4-31B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_FIT, *FLAGS_GEMMA))
GEMMA_4_12B = Model(name="gemma-4-12b", repo="lmstudio-community/gemma-4-12B-it-QAT-GGUF", file="gemma-4-12B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
QWEN_3_6_35B = Model(name="qwen-3.6-35b", repo="lmstudio-community/Qwen3.6-35B-A3B-GGUF", file="Qwen3.6-35B-A3B-Q4_K_M.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_FIT, *FLAGS_QWEN))

HY_MT2_7B = Model(name="hy-mt2-7b", repo="tencent/Hy-MT2-7B-GGUF", file="HY-MT2-7B-Q8_0.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))
TOWER_PLUS_9B = Model(name="tower-plus-9b", repo="mradermacher/Tower-Plus-9B-GGUF", file="Tower-Plus-9B.Q8_0.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))
TRANSLATEGEMMA_12B = Model(name="translategemma-12b", repo="mradermacher/translategemma-12b-it-GGUF", file="translategemma-12b-it.Q6_K.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))

MODELS = (GEMMA_4_26B, GEMMA_4_31B, GEMMA_4_12B, QWEN_3_6_35B, HY_MT2_7B, TOWER_PLUS_9B, TRANSLATEGEMMA_12B)

RUNS = (
    Run(name="gemma-4-26b", model=GEMMA_4_26B, translation=PipelineTranslation()),
    Run(name="gemma-4-31b", model=GEMMA_4_31B, translation=PipelineTranslation()),
    Run(name="gemma-4-12b", model=GEMMA_4_12B, translation=PipelineTranslation()),
    Run(name="qwen-3.6-35b", model=QWEN_3_6_35B, translation=PipelineTranslation()),
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
    commands.add_parser("all", help="evaluate every run not evaluated yet, each with its own llama server")
    commands.add_parser("runs", help="list the runs and what each one has done")
    commands.add_parser("sources", help="copy the English sources from the library")
    command_download = commands.add_parser("download", help="download models from Hugging Face")
    command_download.add_argument("models", nargs="*", help=f"models to download: {', '.join(models)} (default: all)")
    command_serve = commands.add_parser("serve", help="start the llama server of a model")
    command_serve.add_argument("model", choices=models)
    command_translate = commands.add_parser("translate", help="translate the sources with a run")
    command_translate.add_argument("run", choices=runs)
    command_fix = commands.add_parser("fix", help="fix the translations of a run")
    command_fix.add_argument("run", choices=runs)
    command_check = commands.add_parser("check", help="check the translations of a run")
    command_check.add_argument("run", choices=runs)
    commands.add_parser("report", help="show the score, issues and time of every run")
    commands.add_parser("compare", help="write the entries of every run side by side")
    arguments = parser.parse_args()

    match arguments.command:
        case "all":
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
            table = Table("run", "model", "translation", "downloaded", "translated", "checked")
            for run in RUNS:
                triples = run.find_translations()
                translated = sum(translation.exists() for _, translation, _ in triples)
                checked = sum(report.exists() for _, _, report in triples)
                table.add_row(run.name, run.model.name, run.translation.describe(), "yes" if run.model.path.exists() else "no", f"{translated}/{len(triples)}", f"{checked}/{len(triples)}")
            console.print(table)

        case "sources":
            for source in SOURCES:
                snapshot = SOURCES_DIR / source.parent.parent.name / SUBS_FOLDER / source.name
                if snapshot.exists():
                    log_action("exists", Color.BLUE, snapshot.name)
                    continue
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, snapshot)
                log_action("copy", Color.GREEN, snapshot.name)

        case "download":
            if unknown := set(arguments.models) - set(models):
                parser.error(f"unknown models: {', '.join(sorted(unknown))}")
            for name in arguments.models or models:
                models[name].download()

        case "serve":
            sys.exit(models[arguments.model].start().wait())

        case "translate":
            runs[arguments.run].translate()

        case "fix":
            runs[arguments.run].fix()

        case "check":
            runs[arguments.run].check()

        case "report":
            table_scores = Table("run", "translation", "score", "issues", "minutes")
            issues_by_kind: dict[str, dict[str, int]] = {}
            for run in RUNS:
                timings_file = run.dir / TIMINGS_FILE
                timings: dict[str, int] = json.loads(timings_file.read_text(encoding="utf-8")) if timings_file.exists() else {}
                for _, translation, report in run.find_translations():
                    if not report.exists():
                        continue
                    report_lines = report.read_text(encoding="utf-8").splitlines()
                    header = REPORT_HEADER.match(report_lines[1])
                    if header is None:
                        raise ValueError(f"report without a score: {report}")
                    minutes = f"{timings[translation.name] / 60:.1f}" if translation.name in timings else "-"
                    table_scores.add_row(run.name, translation.name.split(".embed.")[0], header[1], header[2], minutes)
                    for line in itertools.takewhile(str.strip, report_lines[3:]):
                        issues_by_kind.setdefault(line[7:28].strip(), {})[run.name] = issues_by_kind.get(line[7:28].strip(), {}).get(run.name, 0) + int(line[2:6])
            console.print(table_scores)

            runs_reported = [run.name for run in RUNS if any(run.name in counts for counts in issues_by_kind.values())]
            table_kinds = Table("issue", *runs_reported)
            for kind, counts in issues_by_kind.items():
                table_kinds.add_row(kind, *(str(counts.get(name, 0)) for name in runs_reported))
            console.print(table_kinds)

        case "compare":
            COMPARE_DIR.mkdir(exist_ok=True)
            width = max(len(run.name) for run in RUNS)
            for snapshot in sorted(SOURCES_DIR.glob(f"*/{SUBS_FOLDER}/*.srt")):
                translated_by_run: dict[str, dict[int, str]] = {}
                for run in RUNS:
                    translation = next(translation for source, translation, _ in run.find_translations() if source.name == snapshot.name)
                    if translation.exists():
                        translated_by_run[run.name] = {entry.index: " / ".join(entry.content.splitlines()) for entry in parse_srt(translation)}
                lines = []
                for entry in parse_srt(snapshot):
                    lines += ["", f"{entry.index}  {srt.timedelta_to_srt_timestamp(entry.start)}", f"  {'en':<{width}}  {' / '.join(entry.content.splitlines())}"]
                    lines += [f"  {name:<{width}}  {translated.get(entry.index, '')}" for name, translated in translated_by_run.items()]
                compare = COMPARE_DIR / f"{parse_subtitle_name(snapshot)}.txt"
                write_text_replacing(compare, "\n".join(lines).lstrip() + "\n")
                log_action("compare", Color.GREEN, f"{compare.name}: {', '.join(translated_by_run)}")
