# Checks translated subtitles against their English source for common translation mistakes and writes a report of each one to _checks, skipping ones with a current report.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import bisect
import contextlib
import functools
import itertools
import json
import re
from collections.abc import Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import ClassVar

import language_tool_python
import srt
from rich.progress import MofNCompleteColumn, TimeElapsedColumn, TimeRemainingColumn

from shared.console import Batch, Color, log_action, progress_bar
from shared.data_lists import CHECK_EXPRESSIONS, CHECK_WORDS, DICTIONARY_HUNSPELL, DataList, load_dictionary
from shared.files import CHECKS_FOLDER, SUBS_FOLDER, TRACES_FOLDER, find_files, parse_movie_dir, parse_srt
from shared.languages import WORDS_PT_PT, TextLanguage, parse_words
from shared.reports import CHECK_WORKERS, Issue, IssueKind, Report, ReportScore, log_ranking
from shared.subtitles import BRACKETS, FORMATTING_TAGS, SubtitleKind, parse_plain_text

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
TRANSLATION_SUFFIXES = tuple(kind.to_translation_suffix() for kind in SubtitleKind)
SOURCE_LANGUAGE = TextLanguage.ENGLISH
TARGET_LANGUAGE = TextLanguage.PORTUGUESE_BRAZIL

QUOTE_CONTEXT_ENTRIES = 2

# ------------------------------------------------------------------------------
# Rules
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class AcceptedWithProblemRule:
    kind: ClassVar[IssueKind] = IssueKind.ACCEPTED_WITH_PROBLEM
    enabled: bool

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        for i, entry in enumerate(check.entries):
            if entry.source.index in check.problems_accepted:
                yield i, check.problems_accepted[entry.source.index]


@dataclass(frozen=True)
class UntranslatedRule:
    kind: ClassVar[IssueKind] = IssueKind.UNTRANSLATED
    SONG: ClassVar[re.Pattern[str]] = re.compile(r"[♪♫]")
    enabled: bool
    votes_min: int
    unchanged_votes_min: int

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        for i, entry in enumerate(check.entries):
            if lines_english := [
                n
                for n, (source_line, target_line) in enumerate(itertools.zip_longest(entry.source_lines, entry.target_lines, fillvalue=""), start=1)
                if not self.SONG.search(source_line)
                and (
                    (target_line == source_line and SOURCE_LANGUAGE.outweighs(TARGET_LANGUAGE, parse_words(target_line), votes_min=self.unchanged_votes_min))
                    or SOURCE_LANGUAGE.outweighs(TARGET_LANGUAGE, parse_words(target_line), votes_min=self.votes_min)
                )
            ]:
                yield i, f"line {', '.join(map(str, lines_english))} is in english"


@dataclass(frozen=True)
class AddedSymbolsRule:
    kind: ClassVar[IssueKind] = IssueKind.ADDED_SYMBOLS
    SYMBOL: ClassVar[re.Pattern[str]] = re.compile(r"[{}\[\]_<>`\\]")
    LATIN_LAST: ClassVar[str] = chr(0x024F)
    enabled: bool

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        for i, entry in enumerate(check.entries):
            if symbols := sorted(self.parse_symbols(entry.target.content) - self.parse_symbols(entry.source.content)):
                yield i, ", ".join(f"\"{symbol}\"" for symbol in symbols)

    def parse_symbols(self, content: str) -> set[str]:
        text = FORMATTING_TAGS.sub("", content)
        tags = {tag.lower() for tag in FORMATTING_TAGS.findall(content)}
        letters_foreign = {character for character in text if character.isalpha() and character > self.LATIN_LAST}
        return tags | set(self.SYMBOL.findall(text)) | letters_foreign


@dataclass(frozen=True)
class DuplicatedLineRule:
    kind: ClassVar[IssueKind] = IssueKind.DUPLICATED_LINE
    enabled: bool
    length_min: int
    ratio_min: float

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        for i, entry in enumerate(check.entries):
            source_keys, target_keys = ([" ".join(word.key for word in parse_words(line)) for line in lines] for lines in (entry.source_lines, entry.target_lines))
            if len(source_keys) != len(target_keys):
                continue
            if lines_duplicated := [
                (a, b)
                for a, b in itertools.permutations(range(len(target_keys)), 2)
                if len(target_keys[a]) >= self.length_min
                and target_keys[a] in target_keys[b]
                and source_keys[a] not in source_keys[b]
                and len(target_keys[b]) >= self.ratio_min * len(source_keys[b])
            ]:
                line_repeated, line_whole = lines_duplicated[0]
                yield i, f"line {line_whole + 1} holds the whole entry, line {line_repeated + 1} repeats part of it"


@dataclass(frozen=True)
class BrokenLineRule:
    kind: ClassVar[IssueKind] = IssueKind.BROKEN_LINE
    LINE_BROKEN: ClassVar[re.Pattern[str]] = re.compile(r"^(?:[^\w♪♫]|[\d_])*$|^\s*[,;:]")
    enabled: bool

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        for i, entry in enumerate(check.entries):
            if lines_broken := [n for n, (source_line, target_line) in enumerate(zip(entry.source_lines, entry.target_lines), start=1) if self.LINE_BROKEN.search(target_line) and not self.LINE_BROKEN.search(source_line)]:
                yield i, f"line {', '.join(map(str, lines_broken))}"


@dataclass(frozen=True)
class GrammarRule:
    LANGUAGE: ClassVar[str] = "pt-BR"
    kind: IssueKind
    enabled: bool
    languagetool_rules: tuple[str, ...]
    expressions: DataList

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        expressions = [re.compile(rf"(?<!\w){re.escape(expression.lower())}(?!\w)") for (expression,) in self.expressions.parse()]
        text = check.target_text
        entry_starts = check.target_entry_starts
        for match in check.languagetool_matches:
            if match.rule_id not in self.languagetool_rules:
                continue
            i = bisect.bisect_right(entry_starts, match.offset) - 1
            flagged_text = text[match.offset:match.offset + match.error_length]

            entry_text = check.entries[i].target_text.replace("\n", " ").lower()
            flagged_start = match.offset - entry_starts[i]
            if any(found.start() <= flagged_start and flagged_start + match.error_length <= found.end() for expression in expressions for found in expression.finditer(entry_text)):
                continue
            words_flagged = parse_words(flagged_text)
            if words_flagged and any(word.name and not word.text.isupper() for word in parse_words(text[entry_starts[i]:match.offset + match.error_length])[-len(words_flagged):]):
                continue

            flagged = flagged_text.replace("\n", " / ")
            replacements = [replacement.replace("\n", " / ") for replacement in match.replacements[:1]]
            yield i, f"\"{flagged}\"" + "".join(f", maybe \"{replacement}\"" for replacement in replacements)


class WordCase(Enum):
    CAPS = "caps"
    OTHER = "other"

    def matches(self, word: str) -> bool:
        return word.isupper() == (self == WordCase.CAPS)


@dataclass(frozen=True)
class UnknownWordRule:
    enabled: bool
    case: WordCase
    frequency_max: float
    length_min: int
    dictionary_hunspell: Path
    words: DataList

    @property
    def kind(self) -> IssueKind:
        return IssueKind.UNKNOWN_WORD_CAPS if self.case == WordCase.CAPS else IssueKind.UNKNOWN_WORD

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        dictionary_hunspell = load_dictionary(self.dictionary_hunspell)
        words = {word.lower() for (word,) in self.words.parse()}
        for i, entry in enumerate(check.entries):
            source = parse_words(entry.source_text)
            source_words = {word.key for word in source} | {word.key.removesuffix("'s") for word in source if word.text[0].isupper()}
            if words_unknown := sorted({
                word.text
                for word in parse_words(entry.target_text)
                if not word.name
                and self.case.matches(word.text)
                and len(word.text) >= self.length_min
                and word.key not in source_words
                and word.text.lower() not in words
                and TARGET_LANGUAGE.measure_frequency(word.text) < self.frequency_max
                and not dictionary_hunspell.lookup(word.text)
                and not dictionary_hunspell.lookup(word.text.lower())
            }):
                yield i, ", ".join(f"\"{word}\"" for word in words_unknown)


@dataclass(frozen=True)
class PortugalPortugueseRule:
    kind: ClassVar[IssueKind] = IssueKind.PORTUGAL_PORTUGUESE
    enabled: bool

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        for i, entry in enumerate(check.entries):
            if portugal := sorted({match[0].lower() for match in WORDS_PT_PT.finditer(entry.target_text)}):
                yield i, ", ".join(f"\"{word}\"" for word in portugal)


@dataclass(frozen=True)
class UnbalancedRule:
    kind: ClassVar[IssueKind] = IssueKind.UNBALANCED_TRANSLATION
    PAIRS: ClassVar[tuple[tuple[str, str], ...]] = (*BRACKETS, ("“", "”"), ('"', '"'))
    enabled: bool

    def find_issues(self, check: "TranslationCheck") -> Iterator[tuple[int, str]]:
        for i, entry in enumerate(check.entries):
            if pairs := [f"{opening}{closing}" for opening, closing in self.PAIRS if self.is_paired(entry.source_text, opening, closing) and not self.is_paired(entry.target_text, opening, closing)]:
                yield i, f"{', '.join(pairs)} unpaired"

    def is_paired(self, text: str, opening: str, closing: str) -> bool:
        if opening == closing:
            return text.count(opening) % 2 == 0
        return text.count(opening) == text.count(closing)


TranslationRule = AcceptedWithProblemRule | UntranslatedRule | AddedSymbolsRule | DuplicatedLineRule | BrokenLineRule | GrammarRule | UnknownWordRule | PortugalPortugueseRule | UnbalancedRule

RULES: tuple[TranslationRule, ...] = (
    AcceptedWithProblemRule(enabled=True),
    UntranslatedRule(enabled=True, votes_min=3, unchanged_votes_min=2),
    AddedSymbolsRule(enabled=True),
    DuplicatedLineRule(enabled=True, length_min=8, ratio_min=1.6),
    BrokenLineRule(enabled=True),
    GrammarRule(kind=IssueKind.GRAMMAR_VERB, enabled=True, languagetool_rules=("AUXILIARY_VERB_INFINITIVE",), expressions=CHECK_EXPRESSIONS),
    GrammarRule(kind=IssueKind.GRAMMAR_CONTRACTION, enabled=True, languagetool_rules=("CONTRACOES_OBRIGATORIAS",), expressions=CHECK_EXPRESSIONS),
    GrammarRule(kind=IssueKind.GRAMMAR_QUE, enabled=True, languagetool_rules=("QUE_QUÊ",), expressions=CHECK_EXPRESSIONS),
    GrammarRule(kind=IssueKind.GRAMMAR_GENDER, enabled=True, languagetool_rules=("GENERAL_GENDER_AGREEMENT_ERRORS", "ERRO_DE_CONCORDNCIA_DO_GÉNERO_MASCULINO_O"), expressions=CHECK_EXPRESSIONS),
    UnknownWordRule(enabled=True, case=WordCase.OTHER, frequency_max=2.0, length_min=3, dictionary_hunspell=DICTIONARY_HUNSPELL, words=CHECK_WORDS),
    UnknownWordRule(enabled=True, case=WordCase.CAPS, frequency_max=2.0, length_min=3, dictionary_hunspell=DICTIONARY_HUNSPELL, words=CHECK_WORDS),
    PortugalPortugueseRule(enabled=True),
    UnbalancedRule(enabled=True),
)
LANGUAGETOOL_RULES = frozenset(languagetool_rule for rule in RULES if isinstance(rule, GrammarRule) and rule.enabled for languagetool_rule in rule.languagetool_rules)

# ------------------------------------------------------------------------------
# Checks
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class TranslationEntry:
    source: srt.Subtitle
    target: srt.Subtitle
    source_text: str
    target_text: str

    @classmethod
    def from_entries(cls, source: srt.Subtitle, target: srt.Subtitle) -> "TranslationEntry":
        return cls(source=source, target=target, source_text=parse_plain_text(source), target_text=parse_plain_text(target))

    @property
    def source_lines(self) -> list[str]:
        return self.source_text.split("\n")

    @property
    def target_lines(self) -> list[str]:
        return self.target_text.split("\n")

    @property
    def quote(self) -> tuple[str, ...]:
        return tuple(f"en  {line}" for line in self.source.content.split("\n")) + tuple(f"pt  {line}" for line in self.target.content.split("\n"))


@dataclass
class TranslationCheck:
    subtitle: Path
    entries: list[TranslationEntry]
    problems_accepted: dict[int, str]
    languagetool_server: str
    issues: list[Issue] = field(default_factory=list)
    issues_ignored: int = 0

    @classmethod
    def from_file(cls, subtitle: Path, languagetool_server: str) -> "TranslationCheck":
        # find the English source and the trace of the translation
        movie_dir = parse_movie_dir(subtitle)
        kind = SubtitleKind.find_in_translation(subtitle)
        if kind is None:
            raise ValueError(f"not a translation: {subtitle.name}")
        source = movie_dir / SUBS_FOLDER / f"{subtitle.name.removesuffix(kind.to_translation_suffix())}.eng{kind}.srt"
        if not source.exists():
            raise FileNotFoundError(f"English source not found: {source}")
        trace = movie_dir / TRACES_FOLDER / f"{subtitle.stem}.json"
        records = json.loads(trace.read_text(encoding="utf-8")) if trace.exists() else []

        # pair each translated entry with the source entry of the same index
        sources_by_index = {entry.index: entry for entry in parse_srt(source)}
        return cls(
            subtitle=subtitle,
            entries=[TranslationEntry.from_entries(sources_by_index[target.index], target) for target in parse_srt(subtitle) if target.index in sources_by_index],
            problems_accepted={index: record["accepted_with_problem"] for record in records if "accepted_with_problem" in record for index in record["entries"]},
            languagetool_server=languagetool_server,
        )

    def check(self) -> None:
        for rule in RULES:
            if not rule.enabled:
                continue
            for i, detail in rule.find_issues(self):
                if rule.kind.is_ignored_in(self.entries[i].target):
                    self.issues_ignored += 1
                    continue
                self.issues.append(Issue(entry=self.entries[i].target, kind=rule.kind, detail=detail, quote=self.quote(i)))

    @functools.cached_property
    def target_text(self) -> str:
        return "\n\n".join(entry.target_text for entry in self.entries)

    @functools.cached_property
    def target_entry_starts(self) -> list[int]:
        return list(itertools.accumulate((len(entry.target_text) + 2 for entry in self.entries[:-1]), initial=0))

    @functools.cached_property
    def languagetool_matches(self) -> list[language_tool_python.Match]:
        if not LANGUAGETOOL_RULES:
            return []
        return connect_languagetool(self.languagetool_server, LANGUAGETOOL_RULES).check(self.target_text)

    def quote(self, i: int) -> tuple[str, ...]:
        quote: list[str] = []
        for j in range(max(0, i - QUOTE_CONTEXT_ENTRIES), min(len(self.entries), i + QUOTE_CONTEXT_ENTRIES + 1)):
            mark = ">>" if j == i else "  "
            quote += [f"{mark} {str(self.entries[j].target.index) if n == 0 else '':>4}  {line}" for n, line in enumerate(self.entries[j].quote)]
        return tuple(quote)

    def to_report(self) -> Report:
        return Report(subtitle=self.subtitle, entries_count=len(self.entries), issues=self.issues, issues_ignored=self.issues_ignored)


@functools.cache
def connect_languagetool(server: str, rules: frozenset[str]) -> language_tool_python.LanguageTool:
    languagetool = language_tool_python.LanguageTool(GrammarRule.LANGUAGE, remote_server=server)
    languagetool.enabled_rules = set(rules)
    languagetool.enabled_rules_only = True
    return languagetool


def check_subtitle(subtitle: Path, report: Path, languagetool_server: str) -> ReportScore:
    check = TranslationCheck.from_file(subtitle, languagetool_server)
    check.check()
    return check.to_report().write(report)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    subs = find_files(argparse.ArgumentParser(description="Checks translated subtitles against their English source for common translation mistakes."), extensions_file=TRANSLATION_SUFFIXES, extensions_folder=TRANSLATION_SUFFIXES)
    reports = {sub: parse_movie_dir(sub) / CHECKS_FOLDER / f"{sub.stem}.txt" for sub in subs}
    scores_current = {sub: ReportScore.find(reports[sub]) for sub in subs}
    batch = Batch()
    scores = [score for score in scores_current.values() if score is not None]
    subs_pending = [sub for sub in subs if scores_current[sub] is None]

    # start one LanguageTool server for all workers, only when there is a sub to check and a LanguageTool rule enabled
    with (language_tool_python.LanguageTool(GrammarRule.LANGUAGE) if subs_pending and LANGUAGETOOL_RULES else contextlib.nullcontext()) as languagetool, ProcessPoolExecutor(max_workers=CHECK_WORKERS) as executor:
        # check every sub without a report, CHECK_WORKERS at a time across movies
        languagetool_server = f"http://{languagetool.host}:{languagetool.port}" if languagetool is not None else ""
        checks: dict[Path, Future[ReportScore]] = {sub: executor.submit(check_subtitle, sub, reports[sub], languagetool_server) for sub in subs_pending}

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
