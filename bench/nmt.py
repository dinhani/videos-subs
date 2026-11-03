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
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translates each entry of an English subtitle to Portuguese with a CTranslate2 model and writes the replies as JSON, by entry number.")
    parser.add_argument("--family", required=True, choices=("nllb", "madlad", "opus"))
    parser.add_argument("--model", required=True, type=Path, help="folder of the CTranslate2 model")
    parser.add_argument("source", type=Path)
    parser.add_argument("replies", type=Path)
    arguments = parser.parse_args()

    if ctranslate2.get_cuda_device_count() == 0:
        raise SystemExit("no CUDA device for CTranslate2")

    # each family has its own tokenizer and its own way to ask for Portuguese
    match arguments.family:
        case "nllb":
            tokenizer = AutoTokenizer.from_pretrained(arguments.model, src_lang="eng_Latn")
            encode = lambda text: tokenizer.convert_ids_to_tokens(tokenizer.encode(text))
            target_prefix = ["por_Latn"]
            decode = lambda tokens: tokenizer.decode(tokenizer.convert_tokens_to_ids(tokens[1:]), skip_special_tokens=True)
        case "madlad":
            pieces = SentencePieceProcessor(model_file=str(arguments.model / "spiece.model"))
            encode = lambda text: pieces.encode(f"<2pt> {text}", out_type=str) + ["</s>"]
            target_prefix = None
            decode = lambda tokens: pieces.decode(tokens)
        case "opus":
            tokenizer = AutoTokenizer.from_pretrained(arguments.model)
            encode = lambda text: tokenizer.convert_ids_to_tokens(tokenizer.encode(f">>pob<< {text}"))
            target_prefix = None
            decode = lambda tokens: tokenizer.decode(tokenizer.convert_tokens_to_ids(tokens), skip_special_tokens=True)

    entries = list(srt.parse(arguments.source.read_text(encoding="utf-8-sig")))
    texts = {entry.index: " ".join(FORMATTING_TAGS.sub("", entry.content).split()) for entry in entries}
    indexes = [index for index, text in texts.items() if text]

    translator = ctranslate2.Translator(str(arguments.model), device="cuda", compute_type="int8_float16")
    results = translator.translate_batch(
        [encode(texts[index]) for index in indexes],
        target_prefix=[target_prefix] * len(indexes) if target_prefix else None,
        beam_size=BEAM_SIZE,
        max_batch_size=BATCH_SIZE,
        max_decoding_length=DECODING_LENGTH_MAX,
    )
    replies = {str(index): decode(result.hypotheses[0]) for index, result in zip(indexes, results)}

    arguments.replies.parent.mkdir(parents=True, exist_ok=True)
    arguments.replies.write_text(json.dumps(replies, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  translate {len(replies)} entries with {arguments.model.name}")
