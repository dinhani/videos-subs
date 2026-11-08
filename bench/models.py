import os
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from shared.console import Action

MODELS_DIR = Path("Z:/apps/ai")
PYTORCH_ENV = {"HF_HUB_OFFLINE": "1"}

LLAMA_SERVER = Path("C:/_scoop/apps/llama.cpp-cu133/current/llama-server.exe")
LLAMA_ENV = {"GGML_CUDA_PDL": "1", "GGML_CUDA_GRAPH_OPT": "1"}
LLAMA_PORT = 9931
LLAMA_URL = f"http://127.0.0.1:{LLAMA_PORT}"
LLAMA_LOAD_SECONDS_MAX = 900


@dataclass(frozen=True)
class LlamaModel:
    name: str
    repo: str
    file: str
    flags: tuple[str, ...]

    @property
    def path(self) -> Path:
        return MODELS_DIR / self.repo / self.file

    def is_downloaded(self) -> bool:
        return self.path.exists()

    def find_bytes(self) -> int | None:
        return self.path.stat().st_size if self.is_downloaded() else None

    def download(self) -> None:
        if self.is_downloaded():
            Action.EXISTS.log(self.name)
            return
        from huggingface_hub import hf_hub_download

        Action.DOWNLOAD.log(f"{self.name}: {self.repo}/{self.file}")
        hf_hub_download(repo_id=self.repo, filename=self.file, local_dir=MODELS_DIR / self.repo)

    @contextmanager
    def serving(self, log: Path) -> Generator[None]:
        if not self.is_downloaded():
            raise FileNotFoundError(f"{self.name} not downloaded: {self.path}")
        try:
            props = httpx.get(f"{LLAMA_URL}/props", timeout=10).json()
            raise RuntimeError(f"port {LLAMA_PORT} is used by a llama server running {Path(props.get('model_path', '?')).name}; stop it first")
        except httpx.TransportError:
            pass

        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("wb") as output:
            Action.START.log(f"llama server for {self.name}, log in {log}")
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
                Action.STOP.log(f"llama server for {self.name}")


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
        return MODELS_DIR / self.repo

    def is_downloaded(self) -> bool:
        return (self.path / "model.bin").exists()

    def find_bytes(self) -> int | None:
        return sum(file.stat().st_size for file in self.path.rglob("*") if file.is_file()) if self.path.exists() else None

    def download(self) -> None:
        if self.is_downloaded():
            Action.EXISTS.log(self.name)
            return
        from huggingface_hub import snapshot_download

        Action.DOWNLOAD.log(f"{self.name}: {self.repo}")
        snapshot_download(repo_id=self.repo, local_dir=self.path)


@dataclass(frozen=True)
class ScoringModel:
    name: str
    repo: str
    files: tuple[str, ...]

    @property
    def path(self) -> Path:
        return MODELS_DIR / self.repo

    def is_downloaded(self) -> bool:
        return all((self.path / file).exists() for file in self.files)

    def download(self) -> None:
        if self.is_downloaded():
            Action.EXISTS.log(self.name)
            return
        from huggingface_hub import snapshot_download

        Action.DOWNLOAD.log(f"{self.name}: {self.repo}")
        snapshot_download(repo_id=self.repo, local_dir=self.path, allow_patterns=list(self.files))
