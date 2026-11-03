# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "srt>=3.5",
#     "torch>=2.7",
#     "unbabel-comet>=2.2",
# ]
#
# [tool.uv.sources]
# torch = { index = "pytorch-cu128" }
#
# [[tool.uv.index]]
# name = "pytorch-cu128"
# url = "https://download.pytorch.org/whl/cu128"
# explicit = true
# ///

# Finds error spans in translations with xCOMET, without a reference, entry by entry, to judge whether it can be a check-translate rule.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
import re
from pathlib import Path

import srt
import torch
from comet import download_model, load_from_checkpoint

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
XCOMET_MODEL = "Unbabel/XCOMET-XL"
XCOMET_BATCH_SIZE = 16
FORMATTING_TAGS = re.compile(r"<[^>]+>|\{[^}]*\}")

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Finds error spans in translations with xCOMET, without a reference. Each job gives an English subtitle, its translation and the file to write the spans to.")
    parser.add_argument("jobs", type=Path, help="JSON file with a list of jobs: {\"english\": path, \"translation\": path, \"output\": path}")
    arguments = parser.parse_args()

    jobs = [job for job in json.loads(arguments.jobs.read_text(encoding="utf-8")) if not Path(job["output"]).exists()]
    if not jobs:
        raise SystemExit(0)

    model = load_from_checkpoint(download_model(XCOMET_MODEL))
    for job in jobs:
        # pair each English entry with the translated entry of the same number, as plain text on one line
        english = {entry.index: " ".join(FORMATTING_TAGS.sub("", entry.content).split()) for entry in srt.parse(Path(job["english"]).read_text(encoding="utf-8-sig"))}
        translated = {entry.index: " ".join(FORMATTING_TAGS.sub("", entry.content).split()) for entry in srt.parse(Path(job["translation"]).read_text(encoding="utf-8-sig"))}
        indexes = [index for index in english if english[index] and translated.get(index)]
        segments = [{"src": english[index], "mt": translated[index]} for index in indexes]

        prediction = model.predict(segments, batch_size=XCOMET_BATCH_SIZE, gpus=1 if torch.cuda.is_available() else 0, progress_bar=True)
        entries = [
            {"entry": index, "en": english[index], "pt": translated[index], "score": round(score, 3), "spans": [{"text": span["text"], "severity": span["severity"], "confidence": round(span["confidence"], 3)} for span in spans]}
            for index, score, spans in zip(indexes, prediction.scores, prediction.metadata.error_spans)
        ]

        output = Path(job["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"system_score": round(prediction.system_score, 3), "entries": entries}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  spans     {output.name}: {sum(bool(entry['spans']) for entry in entries)} of {len(entries)} entries with spans")
