# Translates the base English subtitle of each video with a Claude Code agent, skipping videos already translated.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import itertools
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.text import Text

from shared.console import Action, Answer, Batch, Color, confirm, console, log_heading
from shared.drafts import WORK_ENGLISH, WORK_REFERENCES_INDEX, Reference, Session, Source, WorkFolder, format_references_index
from shared.files import SEASON_PREFIX, TRACK_INDEX, Backups, SubtitleFile, SubtitleFormat, find_subtitles, list_folder, parse_srt, write_text_replacing
from shared.llm import Trace
from shared.movies import NFO_SHOW, MovieInfo
from shared.subtitles import LANGUAGE_SOURCE, LANGUAGE_TRANSLATION
from shared.translation_rules import RULES_ENTRIES, RULES_EUROPEAN_PORTUGUESE, RULES_KEPT, RULES_MEANING, RULES_REGISTER

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).absolute().parent.parent

HOME_DEFAULT = "claude"
HOME_CONFIG_VARIABLE = "CLAUDE_CONFIG_DIR"
HOME_CREDENTIALS = ".credentials.json"
CHECK_COMMAND = f"just -f {PROJECT_DIR.as_posix()}/justfile check-draft"

AGENT_MODEL = "opus"
AGENT_EFFORT = "high"
AGENT_EFFORTS = ("low", "medium", "high", "xhigh", "max")
AGENT_RUNS = 3
AGENT_OPTIONS = ("--safe-mode", "--tools", "Read,Grep,Glob,Write,Edit,Bash", "--permission-mode", "dontAsk", "--output-format", "stream-json", "--verbose")
AGENT_ALLOWED = ("Read", "Grep", "Glob", "Write(./**)", "Edit(./**)", f"Bash({CHECK_COMMAND})")
AGENT_TOOL_ACTIONS = {"Read": Action.READ, "Write": Action.WRITE, "Edit": Action.EDIT}
AGENT_EVENTS_SKIPPED = frozenset({"thinking_tokens"})
RATE_LIMIT_ALLOWED = "allowed"
REFERENCE_LANGUAGES = (LANGUAGE_SOURCE, LANGUAGE_TRANSLATION, "por", "spa", "fra", "ita")

AGENT_TASK = """\
Translate the subtitle in this folder. It is from {movie}.{movie_info}

Files outside this folder, if you want more than this:
{files}"""
AGENT_TASK_CONTINUE = "Your last session stopped at the usage limit. Continue the translation where you stopped."
AGENT_TASK_CONTINUE_DRAFT = """\
{task}

An earlier session stopped at the usage limit after writing part of `translation/`, and maybe `notes.md`. Run the check to see where it stopped, read what it wrote, and continue the translation from there."""
AGENT_TASK_FIX = """\
The check of the whole translation still finds these problems:
{problems}

Fix them, run the check again, and finish when it reports no problems and every entry translated."""
AGENT_PROMPT = """\
You translate a movie or TV subtitle from English to Brazilian Portuguese, for viewers who watch it with the video. You work in the current folder.

# Files

- `english.txt`: the English subtitle. Each entry starts with `#<number>` on its own line, followed by its lines.
- `references/`: the English SDH, Brazilian Portuguese, European Portuguese, Spanish, French and Italian subtitles of the same video that exist, one file per subtitle, matched to the English by timing: under `#<number>` are the lines on screen during English entry `<number>`. An entry with nothing on screen in a subtitle is not in its file. To see every reference for one entry, Grep `^#523$` in `references/` with 4 lines after.
- `references.md`: one line per reference: its language code (ISO 639-3; `por` is European Portuguese, `pob` Brazilian Portuguese), SDH or base, extracted from the video or made by OCR, and how many English entries it matched.
- `translation/`: your translation. Write `.txt` files in the format of `english.txt`, with the Portuguese lines under each number. The files are joined in name order, so name them `0001.txt`, `0002.txt`, and so on. One tool call cannot write a whole subtitle, so split it over several files.

# Result

Every entry of `english.txt` is translated once, under its number, with the same number of lines as the English. Run `{check}` whenever you want. It lists as problems the entries whose number or line count do not match the English, which you must fix, and as warnings the entries whose tags or digits differ from the English, which you judge one by one: fix the translation, or leave it when the difference is right. It also says how many entries are translated. You are done when it reports no problems and every entry translated; then reply with one word: done.

Run no other command, and write only in this folder.

# The English source

The English subtitle is not perfect. It can have OCR errors, broken characters (`you1re` for `you're`, `1Cause` for `'Cause`), typos, missing or repeated words, and lines the subtitle maker added, such as a site ad. Translate what the dialogue means, not its errors: `you1re` is translated as `you're`, and its `1` does not go into the Portuguese. A line that is not dialogue, such as an ad, stays as it is. The references help to see what a broken line meant.

# References

People translated the references from the same dialogue, so they show what the English means in a scene: the sense of an idiom or of a word with several meanings, whether "you" is one person or several, the gender of who speaks or is spoken about, and tense. An SDH subtitle names speakers and sounds. Use them to understand the English, and translate the English:
- When a reference leaves something out, adds something or says something else, follow the English. Few matched entries or OCR errors make a reference less reliable.
- Never copy from a reference, the Brazilian Portuguese one included, and never copy the words, spellings, idioms or sentence structure of another language.
- Address people as the Register section says, not as a reference does: European Portuguese, Spanish, French and Italian use formal address far more often than Brazilian Portuguese. """ + RULES_EUROPEAN_PORTUGUESE + """
- What the Kept unchanged section keeps, such as song lyrics, stays as in the English even when a reference translates it.

The translation is consistent across the whole subtitle: a character keeps their gender, two characters keep the same form of address between them, a term keeps its translation. Notes in `notes.md` help to keep it over a long subtitle.

# Entries

""" + RULES_ENTRIES.replace("{unit}", "entry") + "\n\n" + RULES_MEANING + "\n\n" + RULES_REGISTER + "\n\n" + RULES_KEPT + "\n"

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Translates the base English subtitle of each video to Brazilian Portuguese with a Claude Code agent; folders only English ones.")
    parser.add_argument("--model", default=AGENT_MODEL, help=f"Claude model of the agent (default: {AGENT_MODEL})")
    parser.add_argument("--effort", default=AGENT_EFFORT, choices=AGENT_EFFORTS, help=f"effort of the agent (default: {AGENT_EFFORT})")
    home_default = Path(os.environ[HOME_CONFIG_VARIABLE]).name.removeprefix(".") if os.environ.get(HOME_CONFIG_VARIABLE) else HOME_DEFAULT
    parser.add_argument("--home", default=home_default, help=f"Claude home of the account that runs the agent, the folder ~/.<home>, such as claude, claude-2 or claude-3 (default: {home_default}, from {HOME_CONFIG_VARIABLE} or claude)")
    parser.add_argument("--print-prompt", action="store_true", help="print the system and task prompts sent to the agent for each video, even one already translated, with the files it can read and the claude options, without translating it")
    Backups.add_argument(parser)
    return parser

# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class AgentRun:
    events: tuple[dict[str, Any], ...]
    stderr: str

    @property
    def result(self) -> dict[str, Any]:
        result = next((event for event in reversed(self.events) if event.get("type") == "result"), None)
        if result is None:
            raise RuntimeError(f"claude ended without a result: {self.stderr.strip()[-500:]}")
        return result

    @property
    def session_id(self) -> str:
        session_id = next((event["session_id"] for event in self.events if event.get("session_id")), None)
        if session_id is None:
            raise RuntimeError(f"claude ended without a session: {self.stderr.strip()[-500:]}")
        return session_id

    @property
    def rate_limit(self) -> dict[str, Any]:
        return next((event["rate_limit_info"] for event in reversed(self.events) if event.get("type") == "rate_limit_event"), {})

    def hit_usage_limit(self) -> bool:
        return bool(self.result.get("is_error")) and (self.rate_limit.get("status", RATE_LIMIT_ALLOWED) != RATE_LIMIT_ALLOWED or self.result.get("api_error_status") == 429)

    def ensure_completed(self) -> None:
        if self.result.get("is_error"):
            raise RuntimeError(f"claude failed ({self.result.get('subtype')}): {str(self.result.get('result', ''))[:500]}")

    def describe(self) -> str:
        windows = self.rate_limit.get("unifiedWindows", {})
        usage = ", ".join(f"{name} {window['utilization']:.0%}" for name, window in windows.items() if "utilization" in window)
        return f"{self.result.get('num_turns', 0)} turns, {self.result.get('duration_ms', 0) / 60000:.0f} min, usage {usage or 'unknown'}"

    def describe_limit(self) -> str:
        resets_at = self.rate_limit.get("resetsAt")
        return f"{self.rate_limit.get('rateLimitType', 'usage')} limit reached, resets at {datetime.fromtimestamp(resets_at):%Y-%m-%d %H:%M}" if resets_at else "usage limit reached"

    def to_record(self) -> dict[str, Any]:
        return {"events": [event for event in self.events if event.get("subtype") not in AGENT_EVENTS_SKIPPED]}


@dataclass(frozen=True)
class Agent:
    executable: str
    home: Path
    model: str
    effort: str

    def run(self, work: WorkFolder, system: str, prompt: str, session_id: str | None, folders: list[Path]) -> AgentRun:
        command = self.to_command(system=system, prompt=prompt, session_id=session_id, folders=folders)

        # environment of the home
        environment = {name: value for name, value in os.environ.items() if name != HOME_CONFIG_VARIABLE}
        if self.home != Path.home() / f".{HOME_DEFAULT}":
            environment[HOME_CONFIG_VARIABLE] = str(self.home)

        # stream the events, logging what the agent reads, writes and checks
        events: list[dict[str, Any]] = []
        tools: dict[str, str] = {}
        with subprocess.Popen(command, cwd=work.path, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8") as process:
            for line in process.stdout or ():
                event = json.loads(line)
                events.append(event)
                content = event.get("message", {}).get("content")
                for block in content if isinstance(content, list) else ():
                    if block.get("type") == "tool_use":
                        tools[block["id"]] = block["name"]
                        if (action := AGENT_TOOL_ACTIONS.get(block["name"])) is not None:
                            path = Path(block["input"].get("file_path", ""))
                            action.log(path.relative_to(work.path).as_posix() if path.is_relative_to(work.path) else str(path))
                    elif block.get("type") == "tool_result" and tools.get(block.get("tool_use_id", "")) == "Bash":
                        output = block.get("content")
                        text = output if isinstance(output, str) else " ".join(part.get("text", "") for part in output or ())
                        Action.CHECK.log(text.strip().splitlines()[-1] if text.strip() else "no output")
                if event.get("subtype") == "permission_denied":
                    Action.DECLINED.log(f"{event.get('tool_name')}: {event.get('message', '')[:200]}")
            stderr = process.stderr.read() if process.stderr else ""
        return AgentRun(events=tuple(events), stderr=stderr)

    def to_command(self, system: str, prompt: str, session_id: str | None, folders: list[Path]) -> list[str]:
        return [
            self.executable, "-p", prompt,
            "--model", self.model, "--effort", self.effort, "--system-prompt", system, *AGENT_OPTIONS,
            *(("--resume", session_id) if session_id else ()),
            *(option for folder in folders for option in ("--add-dir", str(folder))),
            "--allowedTools", *AGENT_ALLOWED,
        ]

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = create_parser()
    subs_src = find_subtitles(parser, formats=(SubtitleFormat.TEXT,), languages=(LANGUAGE_SOURCE,))
    arguments = parser.parse_args()
    executable = shutil.which("claude")
    if executable is None and not arguments.print_prompt:
        parser.error("claude not found on PATH")
    home = Path.home() / f".{arguments.home}"
    if not (home / HOME_CREDENTIALS).exists():
        parser.error(f"{home} is not a Claude home with a login ({HOME_CREDENTIALS} not found)")
    agent = Agent(executable=executable or "claude", home=home, model=arguments.model, effort=arguments.effort)
    backups = Backups.start(arguments.backups)
    if not arguments.print_prompt:
        Action.START.log(f"claude home: {home}")
    videos = [next(video_subs) for _, video_subs in itertools.groupby(subs_src, key=lambda sub: (sub.movie_dir, sub.video_name))]

    with Batch() as batch:
        for position, video_sub in enumerate(batch.walk(videos), start=1):
            # skip videos without a base English subtitle, or already translated
            video_name = video_sub.video_name
            subs_english = [sub for sub in video_sub.find_text_subtitles(LANGUAGE_SOURCE) if not sub.is_sdh()]
            if not subs_english:
                Action.SKIP.log(f"{video_name}: no base English subtitle")
                continue
            translated = next((translation for sub in subs_english if (translation := sub.to_translation(format=SubtitleFormat.TRANSLATION_CLAUDE)).path.exists()), None)
            if translated is not None and not arguments.print_prompt:
                Action.EXISTS.log(translated.name)
                continue

            with batch.attempt(video_sub.path):
                # source: the base English subtitle with the most entries
                entries_by_sub = {sub: parse_srt(sub.path) for sub in subs_english}
                sub_src = max(subs_english, key=lambda sub: len(entries_by_sub[sub]))
                sub_entries = entries_by_sub[sub_src]
                sub_dst = sub_src.to_translation(format=SubtitleFormat.TRANSLATION_CLAUDE)
                if arguments.print_prompt:
                    batch.clear_screen()
                    Action.PROMPT.log(sub_src.name)
                else:
                    Action.TRANSLATE.log(sub_dst.name)

                # context: movie info, and the files the agent may open for more
                movie_info = MovieInfo.find(sub_src)
                if movie_info is None:
                    Action.NFO.log(f"{sub_src.nfo.name} not found, translating with the title only")
                movie_info_text = movie_info.to_prompt("\n".join(entry.content for entry in sub_entries)) if movie_info else ""
                series_dir = sub_src.movie_dir.parent if sub_src.movie_dir.name.startswith(SEASON_PREFIX) else None
                folders = [sub_src.movie_dir, *([series_dir] if series_dir else [])]
                files = [f"- the movie folder, with every subtitle of the video in `_subs`: {sub_src.movie_dir}"]
                if sub_src.nfo.exists():
                    files.append(f"- the .nfo of the video: {sub_src.nfo}")
                if series_dir:
                    files.append(f"- the series folder: {series_dir}")
                    if (series_dir / NFO_SHOW).exists():
                        files.append(f"- the .nfo of the series: {series_dir / NFO_SHOW}")
                    files += [f"- a translated episode: {episode}" for episode in sorted(series_dir.glob(f"*/*{SubtitleFormat.TRANSLATION.extension}"))]
                task = AGENT_TASK.replace("{movie_info}", f"\n\n{movie_info_text}" if movie_info_text else "").replace("{files}", "\n".join(files)).replace("{movie}", sub_src.movie_title)
                system = AGENT_PROMPT.replace("{check}", CHECK_COMMAND)

                # references: the English SDH and the Portuguese, Spanish, French and Italian text subtitles of the video aligned to the English, unless continuing a draft a usage limit stopped
                work = WorkFolder.for_video(movie_dir=sub_src.movie_dir, video_name=video_name)
                session = None if arguments.print_prompt else work.find_session()
                references: list[Reference] = []
                if session is None:
                    subs_reference = [file for name in list_folder(sub_src.folder) if (file := SubtitleFile.find(sub_src.folder / name)) and file.is_text() and file.video_name == video_name and file.language in REFERENCE_LANGUAGES and (file.is_sdh() or file.language != LANGUAGE_SOURCE)]
                    subs_reference.sort(key=lambda file: (REFERENCE_LANGUAGES.index(file.language or ""), int(file.track.rpartition(".")[2]) if TRACK_INDEX.search(file.track) else 0))
                    references = [Reference.from_file(file, english_entries=sub_entries) for file in subs_reference]

                # print the prompts sent to the agent, without translating
                if arguments.print_prompt:
                    log_heading(f"system prompt: {sub_src.name}", Color.YELLOW)
                    console.print(system, soft_wrap=True)
                    task_text = Text(task)
                    task_text.highlight_words([movie_info_text] if movie_info_text else [], style=Color.GREEN)
                    task_text.highlight_words([sub_src.movie_title], style=Color.CYAN)
                    log_heading("task prompt", Color.YELLOW)
                    console.print(task_text, soft_wrap=True)
                    # files the agent can read, not sent
                    log_heading(f"work folder, read only if the agent opens them: {work.path}", Color.YELLOW)
                    console.print(f"- `{WORK_ENGLISH}`: {len(Source.from_entries(sub_entries).english)} entries")
                    console.print(f"- `{WORK_REFERENCES_INDEX}`, one line per reference:")
                    console.print(format_references_index(references), soft_wrap=True, end="")

                    # claude options, with the prompts printed above left out
                    command = agent.to_command(system=system, prompt=task, session_id=None, folders=folders)
                    log_heading(f"claude, home {agent.home}", Color.YELLOW)
                    console.print(subprocess.list2cmdline("<system>" if part is system else "<task>" if part is task else part for part in command), soft_wrap=True)

                    # ask before showing the next video
                    if position == len(videos):
                        continue
                    console.print()
                    if not confirm(f"Next video, {len(videos) - position} left: {videos[position].video_name}. Show it?", default=Answer.YES):
                        break
                    continue

                # work folder: continue the draft a usage limit stopped, in its session on the same home or a new session on another, or prepare the English and the references
                trace_file = sub_dst.to_trace()
                session_id = None
                if session is not None and session.home == agent.home:
                    Action.RETRY.log(f"resuming the session stopped at the usage limit, in {work.path}")
                    prompt, session_id = AGENT_TASK_CONTINUE, session.session_id
                    trace = Trace(file=trace_file, records=json.loads(trace_file.read_text(encoding="utf-8")) if trace_file.exists() else [])
                elif session is not None:
                    Action.RETRY.log(f"continuing the draft of a session of {session.home} in a new session, in {work.path}")
                    prompt = AGENT_TASK_CONTINUE_DRAFT.replace("{task}", task)
                    trace = Trace(file=trace_file, records=json.loads(trace_file.read_text(encoding="utf-8")) if trace_file.exists() else [])
                else:
                    work.create(source=Source.from_entries(sub_entries), references=references)
                    prompt = task

                    # start trace, keeping a copy of the trace it replaces
                    trace_file.parent.mkdir(exist_ok=True)
                    if trace_file.exists():
                        backups.save(trace_file)
                    trace = Trace(file=trace_file)
                    trace.record({"model": agent.model, "effort": agent.effort, "home": str(agent.home), "work_folder": str(work.path), "system": system, "task": task, "references": work.read_references_index()})

                # translate, resuming the session with the problems of the check of the whole translation
                source = work.read_source()
                problems: list[str] = []
                for run_number in range(1, AGENT_RUNS + 1):
                    run = agent.run(work, system=system, prompt=prompt, session_id=session_id, folders=folders)
                    trace.record({"run": run_number, "home": str(agent.home), "prompt": prompt, **run.to_record()})
                    session_id = run.session_id
                    work.save_session(Session(session_id=session_id, home=agent.home))
                    if run.hit_usage_limit():
                        break
                    run.ensure_completed()
                    draft = work.read_draft()
                    problems = draft.find_problems(source) + [f"#{number}: not translated" for number in draft.find_missing(source)]
                    trace.record({"run": run_number, "problems": problems, "warnings": draft.find_warnings(source)})
                    if not problems:
                        break
                    Action.RETRY.log(f"{len(problems)} problems in the whole translation (run {run_number}/{AGENT_RUNS})")
                    prompt = AGENT_TASK_FIX.replace("{problems}", "\n".join(problems))

                # stop at the usage limit, so the next run resumes this session
                if run.hit_usage_limit():
                    Action.STOP.log(f"{run.describe_limit()}; work folder: {work.path}")
                    break
                if problems:
                    raise RuntimeError(f"{len(problems)} problems after {AGENT_RUNS} runs; work folder: {work.path}")

                # write translated
                trace.record({"notes": work.read_notes()})
                write_text_replacing(sub_dst.path, draft.compose(sub_entries))
                Action.TRANSLATE.log(f"{sub_dst.name}: {run.describe()}")
                work.end_session()
