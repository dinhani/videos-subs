# Checks a translation draft of translate-claude against its source, for the agent that writes it.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import sys
from pathlib import Path

from shared.drafts import WorkFolder

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Checks the translation draft in a translate-claude work folder and prints its problems and warnings.")
    parser.add_argument("folder", type=Path, help="work folder with english.txt and translation/")
    arguments = parser.parse_args()

    work = WorkFolder(path=arguments.folder)
    source = work.read_source()
    draft = work.read_draft()
    problems = draft.find_problems(source)
    warnings = draft.find_warnings(source)

    for problem in problems:
        print(f"problem {problem}")
    for warning in warnings:
        print(f"warning {warning}")
    print(f"{len(problems)} problems, {len(warnings)} warnings, {draft.describe(source)}")
    sys.exit(1 if problems else 0)
