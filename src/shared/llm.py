import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import OpenAI

from shared.console import Action
from shared.files import write_text_replacing

LLM_URL = "http://127.0.0.1:9931/v1"
LLM_PROPS_URL = "http://127.0.0.1:9931/props"

TRACE_ACCEPTED_WITH_PROBLEM = "accepted_with_problem"


def connect_llama() -> OpenAI:
    llama = OpenAI(base_url=LLM_URL, api_key="none")
    try:
        llama.models.list()
    except Exception as error:
        Action.FAILED.log(f"llama server unreachable at {LLM_URL}: {error}")
        sys.exit(1)
    return llama


@dataclass
class Trace:
    file: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @classmethod
    def find(cls, file: Path) -> "Trace | None":
        try:
            return cls(file=file, records=json.loads(file.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None

    def record(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.records.append(record)
            write_text_replacing(self.file, json.dumps(self.records, ensure_ascii=False, indent=2))

    def record_accepted_with_problem(self, entries: list[int], problem: str) -> None:
        self.record({"entries": entries, TRACE_ACCEPTED_WITH_PROBLEM: problem})

    def find_problems_accepted(self) -> dict[int, str]:
        return {index: record[TRACE_ACCEPTED_WITH_PROBLEM] for record in self.records if TRACE_ACCEPTED_WITH_PROBLEM in record for index in record["entries"]}
