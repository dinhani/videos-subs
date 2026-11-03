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
from collections import Counter
from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path

import httpx
import srt
from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from rich.text import Text

BENCH_DIR = Path(__file__).parent
SRC_DIR = BENCH_DIR.parent / "src"
sys.path.insert(0, str(SRC_DIR))

from shared.console import Batch, Color, console, log_action, progress_bar
from shared.files import CHECKS_FOLDER, SUBS_FOLDER, TRACES_FOLDER, parse_srt, parse_subtitle_name, write_text_replacing
from shared.reports import ReportScore
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
NMT_REPLIES_FILE = "nmt-replies.json"
LLAMA_LOG_FILE = "llama.log"
BASELINE_RUN = "gemma-4-26b"

ALIGNMENT_OVERLAP_MIN = timedelta(milliseconds=200)
ALIGNMENT_OFFSET_MAX = timedelta(seconds=2)

MODELS_GGUF_DIR = Path("D:/AI-Models-LMStudio")
MODELS_CTRANSLATE2_DIR = Path("D:/AI-Models-CTranslate2")
MODELS_PYTORCH_DIR = Path("D:/AI-Models-PyTorch")
PYTORCH_ENV = {"HF_HUB_CACHE": str(MODELS_PYTORCH_DIR), "HF_HUB_OFFLINE": "1"}
LLAMA_SERVER = Path("C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe")
LLAMA_ENV = {"GGML_CUDA_PDL": "1", "GGML_CUDA_GRAPH_OPT": "1"}
LLAMA_PORT = 9931
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"
LLAMA_LOAD_SECONDS_MAX = 900

NATIVE_WORKERS = 8
NATIVE_TOKENS_MAX = 256

FLAGS_PIPELINE = ("--jinja", "--ctx-size", "16384", "--parallel", "1")
FLAGS_THINKING_OFF = ("--reasoning-budget", "0")
FLAGS_THINKING_FULL = ("--reasoning-budget", "-1")
FLAGS_REASONING_LOW = ("--chat-template-kwargs", '{"reasoning_effort": "low"}')
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
        return MODELS_GGUF_DIR / self.repo / self.file

    def download(self) -> None:
        if self.path.exists():
            log_action("exists", Color.BLUE, self.name)
            return
        from huggingface_hub import hf_hub_download

        log_action("download", Color.GREEN, f"{self.name}: {self.repo}/{self.file}")
        hf_hub_download(repo_id=self.repo, filename=self.file, local_dir=MODELS_GGUF_DIR / self.repo)

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

    def is_downloaded(self) -> bool:
        return self.path.exists()


class NmtFamily(StrEnum):
    NLLB = "nllb"
    MADLAD = "madlad"
    OPUS = "opus"


@dataclass(frozen=True)
class NmtModel:
    name: str
    repo: str
    family: NmtFamily

    @property
    def path(self) -> Path:
        return MODELS_CTRANSLATE2_DIR / self.repo

    def download(self) -> None:
        if self.is_downloaded():
            log_action("exists", Color.BLUE, self.name)
            return
        from huggingface_hub import snapshot_download

        log_action("download", Color.GREEN, f"{self.name}: {self.repo}")
        snapshot_download(repo_id=self.repo, local_dir=self.path)

    def is_downloaded(self) -> bool:
        return (self.path / "model.bin").exists()


@dataclass(frozen=True)
class ScoringModel:
    name: str
    repo: str
    files: tuple[str, ...]

    def download(self) -> None:
        if self.is_downloaded():
            log_action("exists", Color.BLUE, self.name)
            return
        from huggingface_hub import snapshot_download

        log_action("download", Color.GREEN, f"{self.name}: {self.repo}")
        snapshot_download(repo_id=self.repo, cache_dir=MODELS_PYTORCH_DIR, allow_patterns=list(self.files))

    def is_downloaded(self) -> bool:
        snapshots = MODELS_PYTORCH_DIR / f"models--{self.repo.replace('/', '--')}" / "snapshots"
        return all(any(snapshots.glob(f"*/{file}")) for file in self.files)


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

        write_translation(entries=entries, replies=replies, translation=translation)


@dataclass(frozen=True)
class NmtTranslation:
    model: NmtModel

    def describe(self) -> str:
        return "nmt"

    def translate(self, source: Path, translation: Path) -> None:
        if not self.model.is_downloaded():
            raise FileNotFoundError(f"{self.model.name} not downloaded: {self.model.path}")
        entries = parse_srt(source)
        replies_file = RUNS_DIR / NMT_REPLIES_FILE
        subprocess.run(["uv", "run", "--script", str(BENCH_DIR / "nmt.py"), "--family", self.model.family, "--model", str(self.model.path), str(source), str(replies_file)], check=True)
        replies_by_index: dict[str, str] = json.loads(replies_file.read_text(encoding="utf-8"))
        replies_file.unlink()
        write_translation(entries=entries, replies=[replies_by_index.get(str(entry.index), "") for entry in entries], translation=translation)


def write_translation(entries: list[srt.Subtitle], replies: list[str], translation: Path) -> None:
    # split a reply with another line count by words, in proportion to the English lines
    records = []
    translated_entries = []
    for entry, reply in zip(entries, replies):
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

    def find_scorings_claude(self) -> list["Scoring"]:
        if not (self.reference_claude.file.exists() and self.reference_official.file.exists()):
            return []
        return [Scoring(english=self.english, hypothesis=self.reference_claude.file, reference=self.reference_official, output=self.score_claude_official)]


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
    subprocess.run(["uv", "run", "--script", str(BENCH_DIR / "score.py"), str(jobs_file)], env={**os.environ, **PYTORCH_ENV}, check=True)
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

    def find_scores(self, reference: Reference) -> dict[str, float]:
        score_file = self.to_score(reference)
        return json.loads(score_file.read_text(encoding="utf-8")) if score_file.exists() else {}

    def find_issues_by_kind(self) -> Counter[str]:
        report_lines = self.report.read_text(encoding="utf-8").splitlines()
        return Counter({line[7:28].strip(): int(line[2:6]) for line in itertools.takewhile(str.strip, report_lines[3:]) if line[7:28].strip() != "ignored"})

    def find_minutes(self) -> float | None:
        timings_file = self.translation.parent.parent / TIMINGS_FILE
        timings: dict[str, int] = json.loads(timings_file.read_text(encoding="utf-8")) if timings_file.exists() else {}
        return timings[self.translation.name] / 60 if self.translation.name in timings else None


@dataclass(frozen=True)
class Run:
    name: str
    model: Model | NmtModel
    translation: PipelineTranslation | NativeTranslation | NmtTranslation

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

    def is_translated(self) -> bool:
        return all(translation.translation.exists() for translation in self.find_translations())

    def translate(self) -> None:
        match self.model:
            case Model():
                with self.model.serving(log=self.dir / LLAMA_LOG_FILE):
                    self.translate_sources()
            case NmtModel():
                self.translate_sources()

    def translate_sources(self) -> None:
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

    def find_scorings(self) -> list[Scoring]:
        return [
            Scoring(english=translation.source.english, hypothesis=translation.translation, reference=reference, output=translation.to_score(reference))
            for translation in self.find_translations()
            if translation.translation.exists()
            for reference in translation.source.find_references()
        ]


GEMMA_4_26B = Model(name="gemma-4-26b", repo="lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF", file="gemma-4-26B-A4B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
GEMMA_4_26B_THINKING = Model(name="gemma-4-26b-thinking", repo="lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF", file="gemma-4-26B-A4B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_FULL, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
GEMMA_4_12B = Model(name="gemma-4-12b", repo="lmstudio-community/gemma-4-12B-it-QAT-GGUF", file="gemma-4-12B-it-QAT-Q4_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))
MISTRAL_SMALL_3_2_24B = Model(name="mistral-small-3.2-24b", repo="bartowski/mistralai_Mistral-Small-3.2-24B-Instruct-2506-GGUF", file="mistralai_Mistral-Small-3.2-24B-Instruct-2506-IQ4_XS.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8))
MINISTRAL_3_14B = Model(name="ministral-3-14b", repo="bartowski/mistralai_Ministral-3-14B-Instruct-2512-GGUF", file="mistralai_Ministral-3-14B-Instruct-2512-Q6_K.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY))
QWEN_3_14B = Model(name="qwen-3-14b", repo="bartowski/Qwen_Qwen3-14B-GGUF", file="Qwen_Qwen3-14B-Q6_K.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8, *FLAGS_QWEN))
QWEN_3_5_9B = Model(name="qwen-3.5-9b", repo="lmstudio-community/Qwen3.5-9B-GGUF", file="Qwen3.5-9B-Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_OFF, *FLAGS_GPU_ONLY, *FLAGS_QWEN))
GPT_OSS_20B = Model(name="gpt-oss-20b", repo="lmstudio-community/gpt-oss-20b-GGUF", file="gpt-oss-20b-MXFP4.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_REASONING_LOW, *FLAGS_GPU_ONLY))
ERNIE_4_5_21B = Model(name="ernie-4.5-21b", repo="lmstudio-community/ERNIE-4.5-21B-A3B-PT-GGUF", file="ERNIE-4.5-21B-A3B-PT-Q4_K_M.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8))
COMMAND_R7B = Model(name="command-r7b", repo="bartowski/c4ai-command-r7b-12-2024-GGUF", file="c4ai-command-r7b-12-2024-Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY))
AYA_EXPANSE_8B = Model(name="aya-expanse-8b", repo="bartowski/aya-expanse-8b-GGUF", file="aya-expanse-8b-Q8_0.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY))
MAGISTRAL_SMALL_24B = Model(name="magistral-small-24b", repo="bartowski/mistralai_Magistral-Small-2509-GGUF", file="mistralai_Magistral-Small-2509-IQ4_XS.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_THINKING_FULL, *FLAGS_GPU_ONLY, *FLAGS_CACHE_Q8))
GEMMA_3_12B = Model(name="gemma-3-12b", repo="lmstudio-community/gemma-3-12b-it-GGUF", file="gemma-3-12b-it-Q4_K_M.gguf", flags=(*FLAGS_PIPELINE, *FLAGS_GPU_ONLY, *FLAGS_GEMMA))

HY_MT2_7B = Model(name="hy-mt2-7b", repo="tencent/Hy-MT2-7B-GGUF", file="HY-MT2-7B-Q8_0.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))
TOWER_PLUS_9B = Model(name="tower-plus-9b", repo="mradermacher/Tower-Plus-9B-GGUF", file="Tower-Plus-9B.Q8_0.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))
TRANSLATEGEMMA_12B = Model(name="translategemma-12b", repo="mradermacher/translategemma-12b-it-GGUF", file="translategemma-12b-it.Q6_K.gguf", flags=(*FLAGS_NATIVE, *FLAGS_GPU_ONLY))

MADLAD_400_10B = NmtModel(name="madlad-400-10b", repo="SoybeanMilk/madlad400-10b-mt-ct2-int8_float16", family=NmtFamily.MADLAD)
MADLAD_400_7B = NmtModel(name="madlad-400-7b", repo="avans06/madlad400-7b-mt-bt-ct2-int8_float16", family=NmtFamily.MADLAD)
MADLAD_400_3B = NmtModel(name="madlad-400-3b", repo="SoybeanMilk/madlad400-3b-mt-ct2-int8_float16", family=NmtFamily.MADLAD)
NLLB_200_3_3B = NmtModel(name="nllb-200-3.3b", repo="OpenNMT/nllb-200-3.3B-ct2-int8", family=NmtFamily.NLLB)
NLLB_200_1_3B = NmtModel(name="nllb-200-1.3b", repo="OpenNMT/nllb-200-distilled-1.3B-ct2-int8", family=NmtFamily.NLLB)
NLLB_200_600M = NmtModel(name="nllb-200-600m", repo="JustFrederik/nllb-200-distilled-600M-ct2-int8", family=NmtFamily.NLLB)
OPUS_MT_EN_PT = NmtModel(name="opus-mt-en-pt", repo="ooeoeo/opus-mt-tc-big-en-pt-ct2-float16", family=NmtFamily.OPUS)
NMT_MODELS = (MADLAD_400_10B, MADLAD_400_7B, MADLAD_400_3B, NLLB_200_3_3B, NLLB_200_1_3B, NLLB_200_600M, OPUS_MT_EN_PT)

MODELS = (*NMT_MODELS, GEMMA_4_26B, GEMMA_4_26B_THINKING, GEMMA_4_12B, MISTRAL_SMALL_3_2_24B, MINISTRAL_3_14B, QWEN_3_14B, QWEN_3_5_9B, GPT_OSS_20B, ERNIE_4_5_21B, COMMAND_R7B, AYA_EXPANSE_8B, MAGISTRAL_SMALL_24B, GEMMA_3_12B, HY_MT2_7B, TOWER_PLUS_9B, TRANSLATEGEMMA_12B)

COMET_WMT22 = ScoringModel(name="comet-wmt22", repo="Unbabel/wmt22-comet-da", files=("checkpoints/model.ckpt", "hparams.yaml"))
XLM_ROBERTA_LARGE = ScoringModel(name="xlm-roberta-large", repo="xlm-roberta-large", files=("config.json", "sentencepiece.bpe.model", "tokenizer.json", "tokenizer_config.json"))
SCORING_MODELS = (COMET_WMT22, XLM_ROBERTA_LARGE)

RUNS = (
    *(Run(name=model.name, model=model, translation=NmtTranslation(model=model)) for model in NMT_MODELS),
    Run(
        name="hy-mt2-7b",
        model=HY_MT2_7B,
        translation=NativeTranslation(
            content=lambda text: f"Translate the following text into Brazilian Portuguese. Note that you should only output the translated result without any additional explanation:\n\n{text}",
            sampling={"temperature": 0.7, "top_p": 0.6, "top_k": 20, "repeat_penalty": 1.05},
        ),
    ),
    Run(name="command-r7b", model=COMMAND_R7B, translation=PipelineTranslation()),
    Run(name="aya-expanse-8b", model=AYA_EXPANSE_8B, translation=PipelineTranslation()),
    Run(name="qwen-3.5-9b", model=QWEN_3_5_9B, translation=PipelineTranslation()),
    Run(
        name="tower-plus-9b",
        model=TOWER_PLUS_9B,
        translation=NativeTranslation(content=lambda text: f"Translate the following English source text to Portuguese (Brazilian):\nEnglish: {text}\nPortuguese (Brazilian): "),
    ),
    Run(name="gemma-4-12b", model=GEMMA_4_12B, translation=PipelineTranslation()),
    Run(name="gemma-3-12b", model=GEMMA_3_12B, translation=PipelineTranslation()),
    Run(
        name="translategemma-12b",
        model=TRANSLATEGEMMA_12B,
        translation=NativeTranslation(content=lambda text: [{"type": "text", "source_lang_code": "en", "target_lang_code": "pt-BR", "text": text}]),
    ),
    Run(name="ministral-3-14b", model=MINISTRAL_3_14B, translation=PipelineTranslation()),
    Run(name="qwen-3-14b", model=QWEN_3_14B, translation=PipelineTranslation()),
    Run(name="gpt-oss-20b", model=GPT_OSS_20B, translation=PipelineTranslation()),
    Run(name="ernie-4.5-21b", model=ERNIE_4_5_21B, translation=PipelineTranslation()),
    Run(name="mistral-small-3.2-24b", model=MISTRAL_SMALL_3_2_24B, translation=PipelineTranslation()),
    Run(name="magistral-small-24b", model=MAGISTRAL_SMALL_24B, translation=PipelineTranslation()),
    Run(name="gemma-4-26b", model=GEMMA_4_26B, translation=PipelineTranslation()),
    Run(name="gemma-4-26b-thinking", model=GEMMA_4_26B_THINKING, translation=PipelineTranslation()),
)


# ------------------------------------------------------------------------------
# Report
# ------------------------------------------------------------------------------
class Better(StrEnum):
    HIGHER = "higher"
    LOWER = "lower"


@dataclass(frozen=True)
class Metric:
    name: str
    key: str
    better: Better
    decimals: int

    def to_header(self, reference: Reference | None = None) -> str:
        return f"{self.name} {reference.name}" if reference else self.name

    def to_cell(self, value: float | None, baseline: float | None) -> Text:
        if value is None:
            return Text("-", style="dim")
        cell = Text(f"{value:.{self.decimals}f}")
        if baseline is None:
            return cell
        difference = round(value - baseline, self.decimals)
        if difference == 0:
            return cell.append(f" {'=':>{self.decimals + 3}}", style="dim")
        improved = difference > 0 if self.better == Better.HIGHER else difference < 0
        return cell.append(f" {difference:+.{self.decimals}f}", style=Color.GREEN if improved else Color.RED)


METRIC_COMET = Metric(name="COMET", key="comet", better=Better.HIGHER, decimals=2)
METRIC_CHRF = Metric(name="chrF++", key="chrf", better=Better.HIGHER, decimals=2)
METRIC_CHECK = Metric(name="check", key="check", better=Better.LOWER, decimals=1)
METRIC_MINUTES = Metric(name="minutes", key="minutes", better=Better.LOWER, decimals=1)


# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    models = {model.name: model for model in (*MODELS, *SCORING_MODELS)}
    runs = {run.name: run for run in RUNS}

    parser = argparse.ArgumentParser(description="Benchmarks translation models on the same English sources.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("all", help="copy the sources and translate every run not translated yet, each with its own llama server; checks and scores are left to score")
    commands.add_parser("score", help="check and score every translation not checked or scored yet, without translating")
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

            batch = Batch()
            for run in RUNS:
                if run.is_translated():
                    log_action("translated", Color.BLUE, run.name)
                    continue
                log_action("translate", Color.GREEN, run.name)
                try:
                    run.translate()
                except Exception as error:
                    batch.fail(run.dir, error)
            batch.finish()

        case "score":
            batch = Batch()
            runs_translated = [run for run in RUNS if any(translation.translation.exists() for translation in run.find_translations())]
            for run in runs_translated:
                log_action("check", Color.GREEN, run.name)
                try:
                    run.check()
                except Exception as error:
                    batch.fail(run.dir, error)
            score_translations([scoring for source in Source.find_all() for scoring in source.find_scorings_claude()] + [scoring for run in runs_translated for scoring in run.find_scorings()])
            batch.finish()

        case "runs":
            table = Table("run", "model", "translation", "downloaded", "translated", "checked", "scored")
            for run in RUNS:
                translations = run.find_translations()
                translated = sum(translation.translation.exists() for translation in translations)
                checked = sum(translation.report.exists() for translation in translations)
                scores = [translation.to_score(reference) for translation in translations for reference in translation.source.find_references()]
                table.add_row(run.name, run.model.name, run.translation.describe(), "yes" if run.model.is_downloaded() else "no", f"{translated}/{len(translations)}", f"{checked}/{len(translations)}", f"{sum(score.exists() for score in scores)}/{len(scores)}")
            console.print(table)

        case "download":
            if unknown := set(arguments.models) - set(models):
                parser.error(f"unknown models: {', '.join(sorted(unknown))}")
            for name in arguments.models or models:
                models[name].download()

        case "report":
            console.print("COMET and chrF++: closeness to the reference translation, 0-100, higher is better.")
            console.print("check: check-translate score, weighted issues per 100 entries, lower is better. minutes: translation time, lower is better.")
            console.print(f"Each value shows its difference from {BASELINE_RUN}, the model of the translate step: green is better, red is worse.")
            for source in Source.find_all():
                references = [reference for reference in (source.reference_claude, source.reference_official) if reference.file.exists()]
                columns = [(metric, reference) for reference in references for metric in (METRIC_COMET, METRIC_CHRF)] + [(METRIC_CHECK, None), (METRIC_MINUTES, None)]

                values_by_run: dict[str, list[float | None]] = {}
                issues_by_run: dict[str, Counter[str]] = {}
                for run in RUNS:
                    translation = next(translation for translation in run.find_translations() if translation.source == source)
                    check = ReportScore.find(translation.report)
                    if check is None:
                        continue
                    values = {METRIC_CHECK.key: check.score, METRIC_MINUTES.key: translation.find_minutes()}
                    values_by_run[run.name] = [translation.find_scores(reference).get(metric.key) if reference else values[metric.key] for metric, reference in columns]
                    issues_by_run[run.name] = translation.find_issues_by_kind()
                runs_sorted = sorted(values_by_run, key=lambda name: (values_by_run[name][0] is None, -(values_by_run[name][0] or 0)))
                baseline = values_by_run.get(BASELINE_RUN, [None] * len(columns))

                english_entries = parse_srt(source.english)
                subtitle_kind = "SDH" if SubtitleKind.from_file(source.english) == SubtitleKind.SDH else "base"
                table_scores = Table("run", *(metric.to_header(reference) for metric, reference in columns), title=f"{source.title}, {subtitle_kind}, {len(english_entries)} entries", title_justify="left")
                for column in table_scores.columns[1:]:
                    column.justify = "right"
                for name in runs_sorted:
                    table_scores.add_row(name, *(metric.to_cell(value, None if name == BASELINE_RUN else baseline_value) for (metric, _), value, baseline_value in zip(columns, values_by_run[name], baseline)))
                if source.score_claude_official.exists() and source.reference_official in references:
                    score = json.loads(source.score_claude_official.read_text(encoding="utf-8"))
                    table_scores.add_section()
                    table_scores.add_row("claude reference", *(metric.to_cell(score.get(metric.key) if reference == source.reference_official else None, None) for metric, reference in columns))
                console.print()
                console.print(table_scores)

                kinds = [kind for kind, _ in sum(issues_by_run.values(), Counter()).most_common()]
                table_kinds = Table("run", *kinds, "total", title=f"{source.title}, issues by kind", title_justify="left")
                for column in table_kinds.columns[1:]:
                    column.justify = "right"
                for name in runs_sorted:
                    issues = issues_by_run[name]
                    table_kinds.add_row(name, *(Text(str(issues[kind]), style="" if issues[kind] else "dim") for kind in kinds), str(issues.total()))
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
