import json
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import Omit, OpenAI, omit
from openai.types.chat import ChatCompletionContentPartParam

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


@dataclass(frozen=True)
class JsonReply:
    data: Any
    content: str | None
    finish_reason: str
    reasoning: str | None

    @classmethod
    def request(cls, llama: OpenAI, schema_name: str, schema: dict[str, Any], system: str, user: str | list[ChatCompletionContentPartParam], temperature: float, max_tokens: int | Omit = omit) -> "JsonReply":
        resp = llama.chat.completions.create(
            model="local",
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        choice = resp.choices[0]
        try:
            data = json.loads(choice.message.content or "")
        except json.JSONDecodeError:
            data = None
        return cls(data=data, content=choice.message.content, finish_reason=choice.finish_reason, reasoning=getattr(choice.message, "reasoning_content", None))

    def find_problem(self, ids: list[str]) -> str:
        if self.finish_reason == "length":
            return "reply cut off by token limit"
        if not isinstance(self.data, dict):
            return "reply is not a JSON object"
        if set(self.data) != set(ids):
            return f"ids mismatch: missing {sorted(set(ids) - set(self.data), key=int)}, extra {sorted(set(self.data) - set(ids))}"
        return ""

    def to_record(self) -> dict[str, Any]:
        return {
            "reply": self.data if self.data is not None else self.content,
            **({"reasoning": self.reasoning} if self.reasoning else {}),
            "finish_reason": self.finish_reason,
        }


@dataclass
class Trace:
    file: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    @staticmethod
    def find_problems_accepted(file: Path) -> dict[int, str]:
        try:
            records = json.loads(file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        return {index: record[TRACE_ACCEPTED_WITH_PROBLEM] for record in records if TRACE_ACCEPTED_WITH_PROBLEM in record for index in record["entries"]}

    def record(self, record: dict[str, Any]) -> None:
        with self.lock:
            self.records.append(record)
            write_text_replacing(self.file, json.dumps(self.records, ensure_ascii=False, indent=2))

    def record_accepted_with_problem(self, entries: list[int], problem: str) -> None:
        self.record({"entries": entries, TRACE_ACCEPTED_WITH_PROBLEM: problem})
