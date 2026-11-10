# Translates the base English subtitle of each video with a Claude Code agent, skipping videos already translated.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import itertools
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from shared.console import Action, Batch
from shared.drafts import Reference, Source, WorkFolder
from shared.files import SEASON_PREFIX, Backups, SubtitleFile, SubtitleFormat, find_subtitles, list_folder, parse_srt, write_text_replacing
from shared.llm import Trace
from shared.movies import NFO_SHOW, MovieInfo
from shared.subtitles import LANGUAGE_SOURCE

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
PROJECT_DIR = Path(__file__).absolute().parent.parent
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

AGENT_TASK = """\
Translate the subtitle in this folder. It is from {movie}.{movie_info}

Files outside this folder, if you want more than this:
{files}"""
AGENT_TASK_CONTINUE = "Your last session stopped at the usage limit. Continue the translation where you stopped."
AGENT_TASK_FIX = """\
The check of the whole translation still finds these problems:
{problems}

Fix them, run the check again, and finish when it reports no problems and every entry translated."""
AGENT_PROMPT = """\
You translate a movie or TV subtitle from English to Brazilian Portuguese, for viewers who watch it with the video. You work in the current folder.

# Files

- `english.txt`: the English subtitle. Each entry starts with `#<number>` on its own line, followed by its lines.
- `references/`: every other subtitle of the same video, one file per subtitle, matched to the English by timing: under `#<number>` are the lines on screen during English entry `<number>`. An entry with nothing on screen in a subtitle is not in its file. To see every reference for one entry, Grep `^#523$` in `references/` with 4 lines after.
- `references.md`: one line per reference: its language code (ISO 639-3; `por` is European Portuguese, `pob` Brazilian Portuguese), SDH or base, extracted from the video or made by OCR, and how many English entries it matched.
- `translation/`: your translation. Write `.txt` files in the format of `english.txt`, with the Portuguese lines under each number. The files are joined in name order, so name them `0001.txt`, `0002.txt`, and so on. One tool call cannot write a whole subtitle, so split it over several files.

# Result

Every entry of `english.txt` is translated once, under its number, with the same number of lines as the English. Run `{check}` whenever you want: it lists every entry whose number, line count, tags or digits do not match the English, and how many entries are translated. You are done when it reports no problems and every entry translated; then reply with one word: done.

Run no other command, and write only in this folder.

# References

People translated the references from the same dialogue, so they show what the English means in a scene: the sense of an idiom or of a word with several meanings, whether "you" is one person or several, the gender of who speaks or is spoken about, and tense. An SDH subtitle names speakers and sounds. Use them to understand the English, and translate the English:
- When a reference leaves something out, adds something or says something else, follow the English. Few matched entries or OCR errors make a reference less reliable.
- Never copy from a reference, the Brazilian Portuguese one included, and never copy the words, spellings, idioms or sentence structure of another language.
- Address people as the Register section says, not as a reference does: European Portuguese, Spanish, French and Italian use formal address far more often than Brazilian Portuguese. Never write European Portuguese forms: "tu" and its verb forms, "consigo" for "com você", "estar a" with the infinitive for the gerund ("está a fazer" is "está fazendo"), or spellings such as "facto" and "equipa".

The translation is consistent across the whole subtitle: a character keeps their gender, two characters keep the same form of address between them, a term keeps its translation. Notes in `notes.md` help to keep it over a long subtitle.

# Entries

Each entry gets its own translation: never merge, split, move, skip or refuse an entry. Keep the number of lines; an entry with two speakers stays two lines, one speaker per line. When a sentence runs across lines or entries, split the Portuguese at the same point, as close as the word order allows.

# Meaning

- Translate the meaning exactly. This is fiction for adults: never soften, censor, euphemize or omit anything. Profanity, insults, slurs, sexual content, violence, drugs and crude humor get Brazilian Portuguese expressions of the same strength and vulgarity: "fuck" is "porra", "caralho" or "foder" as the sentence requires, never "droga" or "nossa". Sarcasm stays sarcasm, threats stay threats, rudeness stays rude.
- Never mask a word that the English writes in full: "caralho", not "c*ralho". Never add warnings, notes or explanations.
- Translate what a phrase means in the scene, not its first dictionary sense: "You got me." after a joke is "Essa me pegou."; "Break a leg." before a show is "Boa sorte.".
- Adjectives and participles agree with the gender of the person they describe: a woman says "Estou atrasada"; a female lawyer is "a advogada".
- Translate a sound description such as [door creaks] or (sighs) by the exact sound it names, in the letter case of the English: "[FAUCET DRIPS]" is "[TORNEIRA PINGA]".
- Translate, do not localize. Units, currencies, dates, times and cultural references stay as in the English, with only the unit word translated: "30 miles" is "30 milhas", never "48 quilômetros"; "6 feet" is "6 pés".

# Register

Match the register of the English: casual stays casual, formal stays formal. Write dialogue as Brazilians speak it today, not as books write it:
- Drop the subject pronoun when the verb shows the subject: "Acho que errei o caminho", not "Eu acho que eu errei o caminho".
- Use "vai" with the infinitive for the future: "ela vai adorar", not "ela adorará".
- Use everyday words: "Consegui os ingressos", not "Obtive os ingressos".
- Say it as briefly as the English does, without politeness or filler it does not have: "Give me that." is "Me dá isso.", not "Você poderia me dar isso?".
- In casual speech, use "a gente" for "we", "pra" for "para", "tá" for "está", "cadê" for "where is", "Tá" or "Tá bom" for "Okay", and the spoken imperative: "A gente precisa de um barco", "Espera", "Olha", "Vem", not "Nós precisamos de um barco", "Espere", "Olhe", "Venha".
- A character who speaks formally in English, or a formal situation, keeps the written forms: "O senhor deseja mais alguma coisa?".

Address people as the scene calls for, and keep the same form between the same characters:
- "você" in everyday speech, never "tu";
- "o senhor" and "a senhora" for formal address, such as to a superior, an elder or a client;
- "Vossa Majestade" and "Vossa Alteza" for royalty.

# Kept unchanged

These stay exactly as in the English, in the same place relative to the text:
- names of people, places and brands, and titles of books, films, shows and songs. Ranks and honorifics before a name are translated: "Captain Rex" is "Capitão Rex", "Mr. Smith" is "Sr. Smith";
- digits;
- the symbols ♪ ♫ … - – — " ¿ ¡ # * [ ] ( ), and a leading "- " dialogue dash on its line;
- every tag, around the same words: <i>, <b>, <u> and <font ...> with their closing tags, {\\an1} to {\\an9} at the start of a line, and any other <...>, </...> or {\\...} tag. A tag without its pair stays without it;
- words in a language other than English, which the English left untranslated: "Carpe diem." stays "Carpe diem."; in "No habla español, fellas?" only "fellas" is translated: "No habla español, pessoal?";
- song lyrics: lines marked with ♪ or ♫, and lines the story shows are sung, even when a reference translates them. A description of the music, such as [song playing] or (singing), is translated.
"""

# ------------------------------------------------------------------------------
# Agent
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
    model: str
    effort: str

    def run(self, work: WorkFolder, system: str, prompt: str, session_id: str | None, folders: list[Path]) -> AgentRun:
        command = [
            self.executable, "-p", prompt,
            "--model", self.model, "--effort", self.effort, "--system-prompt", system, *AGENT_OPTIONS,
            *(("--resume", session_id) if session_id else ()),
            *(option for folder in folders for option in ("--add-dir", str(folder))),
            "--allowedTools", *AGENT_ALLOWED,
        ]

        # stream the events, logging what the agent reads, writes and checks
        events: list[dict[str, Any]] = []
        tools: dict[str, str] = {}
        with subprocess.Popen(command, cwd=work.path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8") as process:
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

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Translates the base English subtitle of each video to Brazilian Portuguese with a Claude Code agent; folders only English ones.")
    parser.add_argument("--model", default=AGENT_MODEL, help=f"Claude model of the agent (default: {AGENT_MODEL})")
    parser.add_argument("--effort", default=AGENT_EFFORT, choices=AGENT_EFFORTS, help=f"effort of the agent (default: {AGENT_EFFORT})")
    Backups.add_argument(parser)
    subs_src = find_subtitles(parser, formats=(SubtitleFormat.TEXT,), languages=(LANGUAGE_SOURCE,))
    arguments = parser.parse_args()
    executable = shutil.which("claude")
    if executable is None:
        parser.error("claude not found on PATH")
    agent = Agent(executable=executable, model=arguments.model, effort=arguments.effort)
    backups = Backups.start(arguments.backups)

    with Batch() as batch:
        for (_, video_name), video_subs in itertools.groupby(batch.walk(subs_src), key=lambda sub: (sub.movie_dir, sub.video_name)):
            # skip videos without a base English subtitle, or already translated
            video_sub = next(video_subs)
            subs_english = [sub for sub in video_sub.find_text_subtitles(LANGUAGE_SOURCE) if not sub.is_sdh()]
            if not subs_english:
                Action.SKIP.log(f"{video_name}: no base English subtitle")
                continue
            if translated := next((sub.to_translation() for sub in subs_english if sub.to_translation().path.exists()), None):
                Action.EXISTS.log(translated.name)
                continue

            with batch.attempt(video_sub.path):
                # source: the base English subtitle with the most entries
                entries_by_sub = {sub: parse_srt(sub.path) for sub in subs_english}
                sub_src = max(subs_english, key=lambda sub: len(entries_by_sub[sub]))
                sub_entries = entries_by_sub[sub_src]
                sub_dst = sub_src.to_translation()
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

                # work folder: resume the session a usage limit stopped, or prepare the English and every other subtitle of the video aligned to it
                work = WorkFolder.for_video(video_name)
                trace_file = sub_dst.to_trace()
                session_id = work.find_session()
                if session_id is not None:
                    Action.RETRY.log(f"resuming the session stopped at the usage limit, in {work.path}")
                    prompt = AGENT_TASK_CONTINUE
                    trace = Trace(file=trace_file, records=json.loads(trace_file.read_text(encoding="utf-8")) if trace_file.exists() else [])
                else:
                    subs_reference = [file for name in sorted(list_folder(sub_src.folder)) if (file := SubtitleFile.find(sub_src.folder / name)) and file.is_text() and file.video_name == video_name and file != sub_src]
                    references = [Reference.from_file(file, english_entries=sub_entries) for file in subs_reference]
                    work.create(source=Source.from_entries(sub_entries), references=references)
                    prompt = task

                    # start trace, keeping a copy of the trace it replaces
                    trace_file.parent.mkdir(exist_ok=True)
                    if trace_file.exists():
                        backups.save(trace_file)
                    trace = Trace(file=trace_file)
                    trace.record({"model": agent.model, "effort": agent.effort, "work_folder": str(work.path), "system": system, "task": task, "references": work.read_references_index()})

                # translate, resuming the session with the problems of the check of the whole translation
                source = work.read_source()
                problems: list[str] = []
                for run_number in range(1, AGENT_RUNS + 1):
                    run = agent.run(work, system=system, prompt=prompt, session_id=session_id, folders=folders)
                    trace.record({"run": run_number, "prompt": prompt, **run.to_record()})
                    session_id = run.session_id
                    work.save_session(session_id)
                    if run.hit_usage_limit():
                        break
                    run.ensure_completed()
                    draft = work.read_draft()
                    problems = draft.find_problems(source) + [f"#{number}: not translated" for number in draft.find_missing(source)]
                    trace.record({"run": run_number, "problems": problems})
                    if not problems:
                        break
                    Action.RETRY.log(f"{len(problems)} problems in the whole translation (run {run_number}/{AGENT_RUNS})")
                    prompt = AGENT_TASK_FIX.replace("{problems}", "\n".join(problems))

                # stop at the usage limit, so the next run resumes this session
                if run.hit_usage_limit():
                    Action.STOP.log(f"{run.describe_limit()}; work folder kept: {work.path}")
                    break
                if problems:
                    raise RuntimeError(f"{len(problems)} problems after {AGENT_RUNS} runs; work folder kept: {work.path}")

                # write translated
                trace.record({"notes": work.read_notes()})
                write_text_replacing(sub_dst.path, draft.compose(sub_entries))
                Action.TRANSLATE.log(f"{sub_dst.name}: {run.describe()}")
                work.remove()
