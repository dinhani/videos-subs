# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "sacrebleu>=2.4",
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

# Scores translations against references with COMET and chrF++, in its own environment, since unbabel-comet needs numpy 1.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
from pathlib import Path

import sacrebleu
import torch
from comet import download_model, load_from_checkpoint

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
COMET_MODEL = "Unbabel/wmt22-comet-da"
COMET_BATCH_SIZE = 32

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scores translations against references. Each job gives its aligned segments, with src, mt and ref, and the file to write its scores to.")
    parser.add_argument("jobs", type=Path, help="JSON file with a list of jobs: {\"output\": path, \"segments\": [{\"src\", \"mt\", \"ref\"}, ...]}")
    arguments = parser.parse_args()

    jobs = [job for job in json.loads(arguments.jobs.read_text(encoding="utf-8")) if not Path(job["output"]).exists()]
    if not jobs:
        raise SystemExit(0)

    model = load_from_checkpoint(download_model(COMET_MODEL))
    for job in jobs:
        segments = job["segments"]
        comet = model.predict(segments, batch_size=COMET_BATCH_SIZE, gpus=1 if torch.cuda.is_available() else 0, progress_bar=True)
        chrf = sacrebleu.corpus_chrf([segment["mt"] for segment in segments], [[segment["ref"] for segment in segments]], word_order=2)
        output = Path(job["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"comet": round(100 * comet.system_score, 2), "chrf": round(chrf.score, 2), "segments": len(segments)}, indent=2), encoding="utf-8")
        print(f"  score     {output.name}: comet {100 * comet.system_score:.2f}, chrf++ {chrf.score:.2f}")
