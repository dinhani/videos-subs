import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

from shared.console import Color, log_action
from shared.files import write_text_replacing

LLM_URL = "http://127.0.0.1:9931/v1"
LLM_PROPS_URL = "http://127.0.0.1:9931/props"


def connect_llama() -> OpenAI:
    llama = OpenAI(base_url=LLM_URL, api_key="none")
    try:
        llama.models.list()
    except Exception as error:
        log_action("failed", Color.RED, f"llama server unreachable at {LLM_URL}: {error}")
        sys.exit(1)
    return llama


@dataclass
class Trace:
    file: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.records.append(record)
            write_text_replacing(self.file, json.dumps(self.records, ensure_ascii=False, indent=2))
