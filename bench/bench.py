# Benchmarks translation models on the same English sources, through the translate step or through a neural machine translation model.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import json
import os
import subprocess
import sys

import questionary
from rich.table import Table

from catalog import BASELINE_RUN, MODELS, RUNS, SCORING_MODELS, SOURCES
from models import PYTORCH_ENV
from report import Comparison, SourceReport
from runs import BENCH_DIR, RUNS_DIR, SCORE_JOBS_FILE, SRC_DIR, Existing
from shared.console import Action, Batch, console
from shared.files import write_text_replacing

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
COMPARE_DIR = BENCH_DIR / "4-compare"

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    models = {model.name: model for model in (*MODELS, *SCORING_MODELS)}
    runs = {run.name: run for run in RUNS}

    parser = argparse.ArgumentParser(description="Benchmarks translation models on the same English sources.")
    commands = parser.add_subparsers(dest="command", required=True)
    command_run = commands.add_parser("run", help="copy the sources and translate every run not translated yet, each with its own llama server; checks and scores are left to score")
    command_run.add_argument("runs", nargs="*", help=f"runs to translate: {', '.join(runs)} (default: all)")
    command_run.add_argument("--pick", action="store_true", help="pick the runs to translate from a list; a picked run that is translated is translated again")
    command_run.add_argument("--movie", default="", help="translate only the sources whose title contains this text, such as fall (default: all)")
    command_run.add_argument("--force", action="store_true", help="translate the runs again; each new translation replaces the old one, its report and its scores only when it is done")
    commands.add_parser("score", help="check and score every translation not checked or scored yet, without translating")
    commands.add_parser("runs", help="list the runs and what each one has done")
    command_download = commands.add_parser("download", help="download models from Hugging Face")
    command_download.add_argument("models", nargs="*", help=f"models to download: {', '.join(models)} (default: all)")
    commands.add_parser("report", help="show the scores, issues and time of every run")
    command_compare = commands.add_parser("compare", help="write the entries of every run side by side")
    command_compare.add_argument("runs", nargs="*", help=f"runs to compare: {', '.join(runs)} (default: all)")
    arguments = parser.parse_args()

    match arguments.command:
        case "run":
            if unknown := set(arguments.runs) - set(runs):
                parser.error(f"unknown runs: {', '.join(sorted(unknown))}")
            runs_selected = [runs[name] for name in arguments.runs] or list(RUNS)
            if arguments.pick:
                name_width = max(len(run.name) for run in runs_selected)
                runs_sorted = sorted(runs_selected, key=lambda run: run.name)
                choices_untranslated = [run.to_choice(SOURCES, name_width) for run in runs_sorted if not run.is_translated(SOURCES)]
                choices_translated = [run.to_choice(SOURCES, name_width) for run in runs_sorted if run.is_translated(SOURCES)]
                if all(choice.disabled for choice in [*choices_untranslated, *choices_translated]):
                    Action.SKIP.log("no run has its model downloaded")
                    raise SystemExit(0)
                separator = [questionary.Separator()] if choices_untranslated and choices_translated else []
                picked = questionary.checkbox("Runs to translate (space checks, enter starts)", choices=[*choices_untranslated, *separator, *choices_translated]).ask()
                if not picked:
                    raise SystemExit(0)
                runs_selected = [runs[name] for name in picked]

            for source in SOURCES:
                source.copy_from_library()
            sources = [source for source in SOURCES if arguments.movie.lower() in source.title.lower()]
            if not sources:
                parser.error(f"no source title contains {arguments.movie}")

            with Batch() as batch:
                for run in runs_selected:
                    existing = Existing.REPLACE if arguments.force or (arguments.pick and run.is_translated(sources)) else Existing.KEEP
                    if run.is_translated(sources) and existing is Existing.KEEP:
                        Action.EXISTS.log(run.name)
                        continue
                    Action.TRANSLATE.log(run.name)
                    with batch.attempt(run.dir):
                        run.translate(sources, existing)

        case "score":
            with Batch() as batch:
                runs_translated = [run for run in RUNS if run.count_translated(SOURCES)]
                translations_unchecked = [translation for run in runs_translated for translation in run.find_unchecked(SOURCES)]
                if translations_unchecked:
                    Action.CHECK.log(f"{len(translations_unchecked)} translations without a report")
                    with batch.attempt(RUNS_DIR):
                        subprocess.run([sys.executable, str(SRC_DIR / "8-check-translations.py"), *map(str, translations_unchecked)], check=True)

                scorings = [scoring for source in SOURCES if (scoring := source.find_scoring_claude())] + [scoring for run in runs_translated for scoring in run.to_scorings(SOURCES)]
                jobs = [scoring.to_job() for scoring in scorings if not scoring.output.exists()]
                if jobs:
                    jobs_file = RUNS_DIR / SCORE_JOBS_FILE
                    write_text_replacing(jobs_file, json.dumps(jobs, ensure_ascii=False))
                    subprocess.run(["uv", "run", "--script", str(BENCH_DIR / "score.py"), str(jobs_file)], env={**os.environ, **PYTORCH_ENV}, check=True)
                    jobs_file.unlink()

        case "runs":
            table = Table("run", "model", "translation", "downloaded", "translated", "checked", "scored")
            for run in sorted(RUNS, key=lambda run: (run.model.name, run.name)):
                table.add_row(*run.to_status_row(SOURCES))
            console.print(table)

        case "download":
            if unknown := set(arguments.models) - set(models):
                parser.error(f"unknown models: {', '.join(sorted(unknown))}")
            for name in arguments.models or models:
                models[name].download()

        case "report":
            console.print("COMET and chrF++: closeness to the reference translation, 0-100, higher is better.")
            console.print("check: check-translate score, weighted issues per 100 entries, lower is better. time: translation time in minutes:seconds, lower is better.")
            console.print(f"Each value shows its difference from {BASELINE_RUN}, the model of the translate step: green is better, red is worse.")
            for source in SOURCES:
                report = SourceReport.from_runs(source, RUNS, baseline=BASELINE_RUN)
                console.print()
                console.print(report.to_score_table())
                console.print()
                console.print(report.to_issue_table())
                console.print(report.to_issue_legend())

        case "compare":
            if unknown := set(arguments.runs) - set(runs):
                parser.error(f"unknown runs: {', '.join(sorted(unknown))}")
            runs_selected = tuple(runs[name] for name in arguments.runs) or RUNS
            COMPARE_DIR.mkdir(exist_ok=True)
            for source in SOURCES:
                comparison = Comparison.from_runs(source, runs_selected)
                compare = COMPARE_DIR / f"{source.name}.txt"
                write_text_replacing(compare, comparison.text)
                Action.COMPARE.log(f"{compare.name}: {', '.join(comparison.columns)}")
