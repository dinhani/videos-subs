# Checks translated subtitles against their English source for common translation mistakes and writes a report of each one to _checks, skipping ones with a current report.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import bisect
import contextlib
import functools
import itertools
import json
import os
import re
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import language_tool_python
import srt
from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn
from spylls.hunspell import Dictionary

from checks import CHECK_WORKERS, CHECKS_FOLDER, Issue, IssueKind, Report, ReportScore, TextLanguage, log_ranking, parse_plain_text, parse_words
from shared import FORMATTING_TAGS, SUBS_FOLDER, TRACES_FOLDER, WORDS_PT_PT, Batch, Color, find_files, log_action, parse_movie_dir, parse_srt, progress_bar

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
TRANSLATION_SUFFIX = ".pob.translated.srt"
SOURCE_LANGUAGE = TextLanguage.ENGLISH
TARGET_LANGUAGE = TextLanguage.PORTUGUESE_BRAZIL

UNTRANSLATED_VOTES_MIN = 3
UNTRANSLATED_UNCHANGED_VOTES_MIN = 2

DUPLICATED_LINE_LENGTH_MIN = 8
DUPLICATED_LINE_RATIO_MIN = 1.6

DICTIONARY = Path(os.environ["LOCALAPPDATA"]) / "hunspell" / "pt_BR"
UNKNOWN_WORD_FREQUENCY_MAX = 2.0
UNKNOWN_WORD_LENGTH_MIN = 3

LANGUAGETOOL_LANGUAGE = "pt-BR"
LANGUAGETOOL_RULES = (
    "AUXILIARY_VERB_INFINITIVE",
    "CONTRACOES_OBRIGATORIAS",
    "QUE_QUÊ",
    "GENERAL_GENDER_AGREEMENT_ERRORS",
    "ERRO_DE_CONCORDNCIA_DO_GÉNERO_MASCULINO_O",
    "LINKING_VERB_PREDICATE_AGREEMENT",
)

SONG = re.compile(r"[♪♫]")
LINE_BROKEN = re.compile(r"^[^\w♪♫]*$|^\s*[,;:]")
SYMBOL = re.compile(r"[{}\[\]_<>`\\]")
LATIN_LAST = chr(0x024F)

# ------------------------------------------------------------------------------
# Checks
# ------------------------------------------------------------------------------
@dataclass
class TranslationCheck:
    subtitle: Path
    sources: list[srt.Subtitle]
    targets: list[srt.Subtitle]
    source_texts: list[str]
    target_texts: list[str]
    problems_accepted: dict[int, str]
    issues: list[Issue] = field(default_factory=list)

    @classmethod
    def from_file(cls, subtitle: Path) -> "TranslationCheck":
        # find the English source and the trace of the translation
        movie_dir = parse_movie_dir(subtitle)
        name = subtitle.name.removesuffix(TRANSLATION_SUFFIX)
        source = movie_dir / SUBS_FOLDER / f"{name}.eng.srt"
        if not source.exists():
            raise FileNotFoundError(f"English source not found: {source}")
        trace = movie_dir / TRACES_FOLDER / f"{subtitle.stem}.json"
        records = json.loads(trace.read_text(encoding="utf-8")) if trace.exists() else []

        # pair each translated entry with the source entry of the same index
        sources_by_index = {entry.index: entry for entry in parse_srt(source)}
        pairs = [(sources_by_index[entry.index], entry) for entry in parse_srt(subtitle) if entry.index in sources_by_index]
        return cls(
            subtitle=subtitle,
            sources=[source for source, _ in pairs],
            targets=[target for _, target in pairs],
            source_texts=[parse_plain_text(source) for source, _ in pairs],
            target_texts=[parse_plain_text(target) for _, target in pairs],
            problems_accepted={index: record["accepted_with_problem"] for record in records if "accepted_with_problem" in record for index in record["entries"]},
        )

    def flag(self, i: int, kind: IssueKind, detail: str) -> None:
        quote = tuple(f"en  {line}" for line in self.sources[i].content.split("\n")) + tuple(f"pt  {line}" for line in self.targets[i].content.split("\n"))
        self.issues.append(Issue(entry=self.targets[i], kind=kind, detail=detail, quote=quote))

    def check_lines(self) -> None:
        dictionary = load_dictionary()
        for i, (source, source_text, target_text) in enumerate(zip(self.sources, self.source_texts, self.target_texts)):
            if source.index in self.problems_accepted:
                self.flag(i, IssueKind.ACCEPTED_WITH_PROBLEM, self.problems_accepted[source.index])
            source_lines = source_text.split("\n")
            target_lines = target_text.split("\n")
            if lines_english := [
                n
                for n, (source_line, target_line) in enumerate(itertools.zip_longest(source_lines, target_lines, fillvalue=""), start=1)
                if (target_line == source_line and SOURCE_LANGUAGE.outweighs(TARGET_LANGUAGE, parse_words(target_line), votes_min=UNTRANSLATED_UNCHANGED_VOTES_MIN))
                or (not SONG.search(target_line) and SOURCE_LANGUAGE.outweighs(TARGET_LANGUAGE, parse_words(target_line), votes_min=UNTRANSLATED_VOTES_MIN))
            ]:
                self.flag(i, IssueKind.UNTRANSLATED, f"line {', '.join(map(str, lines_english))} is in english")
            if lines_broken := [n for n, (source_line, target_line) in enumerate(zip(source_lines, target_lines), start=1) if LINE_BROKEN.search(target_line) and not LINE_BROKEN.search(source_line)]:
                self.flag(i, IssueKind.BROKEN_LINE, f"line {', '.join(map(str, lines_broken))}")
            source_keys, target_keys = ([" ".join(word.key for word in parse_words(line)) for line in lines] for lines in (source_lines, target_lines))
            if len(source_keys) == len(target_keys) and (lines_duplicated := [
                (a, b)
                for a, b in itertools.permutations(range(len(target_keys)), 2)
                if len(target_keys[a]) >= DUPLICATED_LINE_LENGTH_MIN
                and target_keys[a] in target_keys[b]
                and source_keys[a] not in source_keys[b]
                and len(target_keys[b]) >= DUPLICATED_LINE_RATIO_MIN * len(source_keys[b])
            ]):
                line_repeated, line_whole = lines_duplicated[0]
                self.flag(i, IssueKind.DUPLICATED_LINE, f"line {line_whole + 1} holds the whole entry, line {line_repeated + 1} repeats part of it")
            if portugal := sorted({match[0].lower() for match in WORDS_PT_PT.finditer(target_text)}):
                self.flag(i, IssueKind.PORTUGAL_PORTUGUESE, ", ".join(f"\"{word}\"" for word in portugal))
            if symbols := sorted(parse_symbols(self.targets[i].content) - parse_symbols(source.content)):
                self.flag(i, IssueKind.ADDED_SYMBOLS, ", ".join(f"\"{symbol}\"" for symbol in symbols))
            source_words = {word.key for word in parse_words(source_text)}
            if words_unknown := sorted({
                word.text
                for word in parse_words(target_text)
                if not word.name
                and len(word.text) >= UNKNOWN_WORD_LENGTH_MIN
                and word.key not in source_words
                and TARGET_LANGUAGE.measure_frequency(word.text) < UNKNOWN_WORD_FREQUENCY_MAX
                and not dictionary.lookup(word.text)
                and not dictionary.lookup(word.text.lower())
            }):
                self.flag(i, IssueKind.UNKNOWN_WORD, ", ".join(f"\"{word}\"" for word in words_unknown))

    def check_grammar(self, languagetool_server: str) -> None:
        text = "\n\n".join(self.target_texts)
        entry_starts = list(itertools.accumulate((len(target_text) + 2 for target_text in self.target_texts[:-1]), initial=0))
        for match in connect_languagetool(languagetool_server).check(text):
            i = bisect.bisect_right(entry_starts, match.offset) - 1
            flagged = text[match.offset:match.offset + match.error_length].replace("\n", " / ")
            replacements = [replacement.replace("\n", " / ") for replacement in match.replacements[:1]]
            self.flag(i, IssueKind.GRAMMAR, f"\"{flagged}\"" + "".join(f", maybe \"{replacement}\"" for replacement in replacements))

    def to_report(self) -> Report:
        return Report(subtitle=self.subtitle, entries_count=len(self.targets), issues=self.issues)


def parse_symbols(content: str) -> set[str]:
    text = FORMATTING_TAGS.sub("", content)
    tags = {tag.lower() for tag in FORMATTING_TAGS.findall(content)}
    letters_foreign = {character for character in text if character.isalpha() and character > LATIN_LAST}
    return tags | set(SYMBOL.findall(text)) | letters_foreign


@functools.cache
def load_dictionary() -> Dictionary:
    return Dictionary.from_files(str(DICTIONARY))


@functools.cache
def connect_languagetool(server: str) -> language_tool_python.LanguageTool:
    languagetool = language_tool_python.LanguageTool(LANGUAGETOOL_LANGUAGE, remote_server=server)
    languagetool.enabled_rules = set(LANGUAGETOOL_RULES)
    languagetool.enabled_rules_only = True
    return languagetool


def check_subtitle(subtitle: Path, report: Path, languagetool_server: str) -> ReportScore:
    check = TranslationCheck.from_file(subtitle)
    check.check_lines()
    check.check_grammar(languagetool_server)
    return check.to_report().write(report)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_files("Checks translated subtitles against their English source for common translation mistakes.", extensions_file=(TRANSLATION_SUFFIX,), extensions_folder=(TRANSLATION_SUFFIX,))
    reports = {sub: parse_movie_dir(sub) / CHECKS_FOLDER / f"{sub.stem}.txt" for sub in subs}
    scores_current = {sub: ReportScore.find(reports[sub]) for sub in subs}
    batch = Batch()
    scores = [score for score in scores_current.values() if score is not None]
    subs_pending = [sub for sub in subs if scores_current[sub] is None]

    # start one LanguageTool server for all workers, only when there is a sub to check
    with (language_tool_python.LanguageTool(LANGUAGETOOL_LANGUAGE) if subs_pending else contextlib.nullcontext()) as languagetool, ProcessPoolExecutor(max_workers=CHECK_WORKERS) as executor:
        # check every sub without a report, CHECK_WORKERS at a time across movies
        checks: dict[Path, Future[ReportScore]] = {
            sub: executor.submit(check_subtitle, sub, reports[sub], f"http://{languagetool.host}:{languagetool.port}") for sub in subs_pending if languagetool is not None
        }

        try:
            for movie_dir, movie_subs in itertools.groupby(subs, key=parse_movie_dir):
                batch.log_movie(movie_dir)

                # skip subs with a report
                movie_subs_pending: list[Path] = []
                for sub in movie_subs:
                    score = scores_current[sub]
                    if score is not None:
                        log_action("exists", Color.BLUE, score.describe())
                        continue
                    movie_subs_pending.append(sub)
                if not movie_subs_pending:
                    continue

                # report every pending sub of the movie as its check arrives, showing progress
                with progress_bar(MofNCompleteColumn(), TimeElapsedColumn(), TimeRemainingColumn()) as progress:
                    for sub in progress.track(movie_subs_pending):
                        try:
                            score = checks[sub].result()
                            log_action("check", score.color, score.describe())
                            scores.append(score)
                        except Exception as error:
                            batch.fail(sub, error)
        except KeyboardInterrupt:
            executor.shutdown(cancel_futures=True)
            raise

    log_ranking(scores)
    batch.finish()
