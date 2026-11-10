# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "ctranslate2>=4.6",
#     "sentencepiece>=0.2",
#     "srt>=3.5",
#     "transformers>=4.46",
# ]
# ///

# Translates the entries of an English subtitle to Portuguese with a neural machine translation model on CTranslate2, in its own environment.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import ctranslate2
import srt
from sentencepiece import SentencePieceProcessor
from transformers import AutoTokenizer

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
BEAM_SIZE = 4
BATCH_SIZE = 32
DECODING_LENGTH_MAX = 256
FORMATTING_TAGS = re.compile(r"<[^>]+>|\{[^}]*\}")

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translates each entry of an English subtitle to Portuguese with a CTranslate2 model and writes the replies as JSON, by entry number.")
    parser.add_argument("--family", required=True, choices=("nllb", "madlad", "opus"))
    parser.add_argument("--model", required=True, type=Path, help="folder of the CTranslate2 model")
    parser.add_argument("source", type=Path)
    parser.add_argument("replies", type=Path)
    return parser

# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class Tokenization:
    encode: Callable[[str], list[str]]
    decode: Callable[[list[str]], str]
    target_prefix: list[str] | None

    @classmethod
    def from_family(cls, family: str, model: Path) -> "Tokenization":
        # each family has its own tokenizer and its own way to ask for Portuguese
        match family:
            case "nllb":
                tokenizer = AutoTokenizer.from_pretrained(model, src_lang="eng_Latn")
                return cls(
                    encode=lambda text: tokenizer.convert_ids_to_tokens(tokenizer.encode(text)),
                    decode=lambda tokens: tokenizer.decode(tokenizer.convert_tokens_to_ids(tokens[1:]), skip_special_tokens=True),
                    target_prefix=["por_Latn"],
                )
            case "madlad":
                pieces = SentencePieceProcessor(model_file=str(model / "spiece.model"))
                return cls(
                    encode=lambda text: pieces.encode(f"<2pt> {text}", out_type=str) + ["</s>"],
                    decode=lambda tokens: pieces.decode(tokens),
                    target_prefix=None,
                )
            case "opus":
                tokenizer = AutoTokenizer.from_pretrained(model)
                return cls(
                    encode=lambda text: tokenizer.convert_ids_to_tokens(tokenizer.encode(f">>pob<< {text}")),
                    decode=lambda tokens: tokenizer.decode(tokenizer.convert_tokens_to_ids(tokens), skip_special_tokens=True),
                    target_prefix=None,
                )
        raise ValueError(f"unknown family: {family}")

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    arguments = create_parser().parse_args()

    if ctranslate2.get_cuda_device_count() == 0:
        raise SystemExit("no CUDA device for CTranslate2")
    tokenization = Tokenization.from_family(arguments.family, arguments.model)

    entries = list(srt.parse(arguments.source.read_text(encoding="utf-8-sig", errors="replace"), ignore_errors=True))
    texts = {entry.index: " ".join(FORMATTING_TAGS.sub("", entry.content).split()) for entry in entries}
    indexes = [index for index, text in texts.items() if text]

    translator = ctranslate2.Translator(str(arguments.model), device="cuda", compute_type="int8_float16")
    results = translator.translate_batch(
        [tokenization.encode(texts[index]) for index in indexes],
        target_prefix=[tokenization.target_prefix] * len(indexes) if tokenization.target_prefix else None,
        beam_size=BEAM_SIZE,
        max_batch_size=BATCH_SIZE,
        max_decoding_length=DECODING_LENGTH_MAX,
    )
    replies = {str(index): tokenization.decode(result.hypotheses[0]) for index, result in zip(indexes, results)}

    arguments.replies.parent.mkdir(parents=True, exist_ok=True)
    replies_part = arguments.replies.with_name(f"{arguments.replies.name}.part")
    replies_part.write_text(json.dumps(replies, ensure_ascii=False, indent=2), encoding="utf-8")
    replies_part.replace(arguments.replies)
    print(f"  translate {len(replies)} entries with {arguments.model.name}")
