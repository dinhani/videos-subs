# Fixes the mistakes of the translations that have a mechanical fix, by comparing each entry with its English source, shows the changes of each translation and asks before writing it, backs it up before writing it, and deletes the report of every translation it changes.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import functools
import itertools
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar

import srt
from rich.prompt import Confirm
from rich.text import Text

from shared.console import Batch, Color, console, log_action
from shared.data_lists import FIX_SOUNDS, FIX_SUBJECTS, FIX_WORDS, DataList
from shared.descriptions import DESCRIPTION_SIMPLE, DescriptionTable, Movie
from shared.files import CHECKS_FOLDER, Backups, find_files, get_english_source, parse_movie_dir, parse_srt, write_text_replacing
from shared.languages import WORD
from shared.subtitles import BRACKETS, FORMATTING_TAGS, IGNORE_MARKER, QUOTES, SubtitleKind

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
TRANSLATION_SUFFIXES = tuple(kind.to_translation_suffix() for kind in SubtitleKind)

LINE_WORDS = re.compile(r"[^\W\d_]|[♪♫]")
NUMBER = re.compile(r"\d+")

# ------------------------------------------------------------------------------
# Fixes
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class TagsFix:
    name: ClassVar[str] = "tags"
    TAG: ClassVar[re.Pattern[str]] = re.compile(r"</?([a-zA-Z]+)\b[^<>]*>|\{\\[^{}]*\}")
    POSITION: ClassVar[re.Pattern[str]] = re.compile(r"^\{\\an\d\}")
    WORDS_JOINED: ClassVar[re.Pattern[str]] = re.compile(r"\w\w")
    ITALIC: ClassVar[re.Pattern[str]] = re.compile(r"</?i>", re.IGNORECASE)
    ITALIC_LINE: ClassVar[re.Pattern[str]] = re.compile(r"^(?:\{\\an\d\})?<i>(?:(?!</?i>).)*</i>$", re.IGNORECASE)
    enabled: bool

    def fix(self, entry: "TranslationEntry", movie: Movie) -> tuple[str, ...]:
        # delete the tags the English entry does not have, leaving a space where a tag joined two words
        tags_source = {self.parse_tag_key(match) for line in entry.source_lines for match in self.TAG.finditer(line)}

        def replace_tag(match: re.Match[str]) -> str:
            if self.parse_tag_key(match) in tags_source:
                return match[0]
            line = match.string
            if self.WORDS_JOINED.match(line[match.start() - 1:match.start()] + line[match.end():match.end() + 1]):
                return " "
            return ""

        lines: list[str] = []
        for line in entry.target_lines:
            line_kept, removed = self.TAG.subn(replace_tag, line)
            lines.append(" ".join(line_kept.split()) if removed and line_kept != line else line)

        # restore the position of the English entry
        position = self.POSITION.match(entry.source_lines[0])
        if position is not None and not any("{\\an" in line for line in lines):
            lines[0] = position[0] + lines[0]

        # restore the italics of the English lines that are whole in italics, when the model dropped every italic of the entry
        if entry.is_aligned and not any(self.ITALIC.search(line) for line in lines):
            for n, source_line in enumerate(entry.source_lines):
                if not self.ITALIC_LINE.match(source_line) or not WORD.search(FORMATTING_TAGS.sub("", lines[n])):
                    continue
                position_target = self.POSITION.match(lines[n])
                prefix = position_target[0] if position_target else ""
                lines[n] = f"{prefix}<i>{lines[n].removeprefix(prefix)}</i>"
        return tuple(lines)

    def parse_tag_key(self, match: re.Match[str]) -> str:
        if match[1] is None:
            return match[0].lower()
        return f"{'/' if match[0].startswith('</') else ''}{match[1].lower()}"


@dataclass(frozen=True)
class LeftoversFix:
    name: ClassVar[str] = "leftovers"
    END: ClassVar[re.Pattern[str]] = re.compile(r"""(?P<quote>["'])?(?P<closers>\]\}?|\}),?\s*$""")
    enabled: bool

    def fix(self, entry: "TranslationEntry", movie: Movie) -> tuple[str, ...]:
        if not entry.is_aligned:
            return entry.target_lines
        lines = list(entry.target_lines)
        for n, (source_line, line) in enumerate(zip(entry.source_lines, entry.target_lines)):
            source_plain = FORMATTING_TAGS.sub("", source_line).rstrip()
            end = self.END.search(line)
            if end is None or source_plain.endswith(("]", "}")):
                continue
            closing = end["closers"][0]
            opening = "[" if closing == "]" else "{"
            if line.count(closing) <= line.count(opening) or (end["quote"] == "'" and source_plain.endswith("'")):
                continue
            quote = end["quote"] if end["quote"] and source_plain.endswith(end["quote"]) else ""
            lines[n] = (line[:end.start()] + quote).rstrip()

        # an entry left with other quotes than the English entry keeps its leftovers, so check-translate still flags it
        if sum(line.count(quote) for line in lines for quote in QUOTES) != sum(line.count(quote) for line in entry.source_lines for quote in QUOTES):
            return entry.target_lines
        return tuple(lines)


@dataclass(frozen=True)
class BracketsFix:
    name: ClassVar[str] = "brackets"
    TAGS_END: ClassVar[re.Pattern[str]] = re.compile(r"(?:\s*<[^<>]+>)*\s*$")
    TAGS_START: ClassVar[re.Pattern[str]] = re.compile(r"^(?:\{\\[^{}]*\}|<[^<>]+>|[\s\-–—])*")
    LINE_WRAPPED: ClassVar[re.Pattern[str]] = re.compile(r"^(?P<start>(?:\{\\[^{}]*\}|<[^<>]+>|[\s\-–—])*)[\[(](?P<text>[^\[\]()]+)[\])](?P<end>(?:\s*<[^<>]+>)*\s*)$")
    enabled: bool

    def fix(self, entry: "TranslationEntry", movie: Movie) -> tuple[str, ...]:
        if not entry.is_aligned:
            return entry.target_lines
        lines = list(entry.target_lines)
        for n, source_line in enumerate(entry.source_lines):
            source_plain = FORMATTING_TAGS.sub("", source_line).strip()

            # brackets around a line that the English line has without brackets
            wrapped = self.LINE_WRAPPED.match(lines[n])
            if wrapped is not None and not any(bracket in source_plain for bracket in "[]()"):
                lines[n] = f"{wrapped['start']}{wrapped['text'].strip()}{wrapped['end']}"
                continue

            for (opening, closing), (opening_other, closing_other) in itertools.permutations(BRACKETS, 2):
                if not source_plain.count(opening) or source_plain.count(opening) != source_plain.count(closing):
                    continue
                plain = FORMATTING_TAGS.sub("", lines[n]).strip()
                opened = plain.count(opening) == plain.count(closing) + 1
                closed = plain.count(closing) == plain.count(opening) + 1

                # a closer of the other pair in place of the missing one
                if opened and plain.count(closing_other) == plain.count(opening_other) + 1 and opening_other not in source_plain and closing_other not in source_plain:
                    position = lines[n].rfind(closing_other)
                    lines[n] = lines[n][:position] + closing + lines[n][position + 1:]
                elif opened and source_plain.endswith(closing) and not plain.endswith(closing):
                    tags_end = self.TAGS_END.search(lines[n])
                    position = tags_end.start() if tags_end else len(lines[n])
                    lines[n] = lines[n][:position] + closing + lines[n][position:]
                elif closed and source_plain.lstrip("-–— ").startswith(opening) and not plain.lstrip("-–— ").startswith(opening):
                    tags_start = self.TAGS_START.match(lines[n])
                    position = tags_start.end() if tags_start else 0
                    lines[n] = lines[n][:position] + opening + lines[n][position:]
        return tuple(lines)


@dataclass(frozen=True)
class QuotesFix:
    name: ClassVar[str] = "quotes"
    enabled: bool

    def fix(self, entry: "TranslationEntry", movie: Movie) -> tuple[str, ...]:
        # one quote more than the English entry, at the start or end of the only line where the English line has none; curly quotes count as quotes
        if not entry.is_aligned:
            return entry.target_lines
        lines = list(entry.target_lines)
        if sum(line.count(quote) for line in lines for quote in QUOTES) != sum(line.count(quote) for line in entry.source_lines for quote in QUOTES) + 1:
            return entry.target_lines
        quotes_added: list[tuple[int, int]] = []
        for n, (source_line, line) in enumerate(zip(entry.source_lines, lines)):
            source_plain = FORMATTING_TAGS.sub("", source_line).strip().lstrip("-–— ")
            plain = FORMATTING_TAGS.sub("", line).strip().lstrip("-–— ")
            if plain.endswith(tuple(QUOTES)) and not source_plain.endswith(tuple(QUOTES)):
                quotes_added.append((n, max(line.rfind(quote) for quote in QUOTES)))
            if plain.startswith(tuple(QUOTES)) and not source_plain.startswith(tuple(QUOTES)):
                quotes_added.append((n, min(position for quote in QUOTES if (position := line.find(quote)) >= 0)))
        if len(quotes_added) == 1:
            n, position = quotes_added[0]
            lines[n] = (lines[n][:position] + lines[n][position + 1:]).strip()
        return tuple(lines)


@dataclass(frozen=True)
class DescriptionsFix:
    name: ClassVar[str] = "descriptions"
    LINE_REST: ClassVar[re.Pattern[str]] = re.compile(r"[\s\-–—]")
    enabled: bool
    sounds: DataList
    subjects: DataList

    def fix(self, entry: "TranslationEntry", movie: Movie) -> tuple[str, ...]:
        if not entry.is_aligned:
            return entry.target_lines
        table = load_description_table(self.sounds, self.subjects)
        lines = list(entry.target_lines)
        for n, (source_line, line) in enumerate(zip(entry.source_lines, entry.target_lines)):
            descriptions = list(DESCRIPTION_SIMPLE.finditer(source_line))
            if not descriptions:
                continue
            descriptions_target = list(DESCRIPTION_SIMPLE.finditer(line))
            paired = len(descriptions_target) == len(descriptions)

            # each translation from the tables, kept only when the model kept its name, and left out when the model already wrote one of its forms
            translations = [table.find_translation(description[1] or description[2]) for description in descriptions]
            models = [description[1] or description[2] for description in descriptions_target] if paired else [line] * len(descriptions)
            texts: list[str | None] = []
            for description, translation, model in zip(descriptions, translations, models):
                if translation is None or not translation.is_confirmed_by(model, movie.names) or (paired and translation.is_already_in(model)):
                    texts.append(None)
                    continue
                inner = description[0][1:-1]
                space_before, space_after = inner[:len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()):]
                texts.append(f"{description[0][0]}{space_before}{translation.text}{space_after}{description[0][-1]}")

            # a line that is only descriptions is the English line with the descriptions translated
            line_whole = not self.LINE_REST.sub("", FORMATTING_TAGS.sub("", DESCRIPTION_SIMPLE.sub("", source_line)))
            if line_whole and all(text is not None for text in texts):
                lines[n] = self.replace_descriptions(source_line, descriptions, texts)
            elif paired:
                lines[n] = self.replace_descriptions(line, descriptions_target, texts)
        return tuple(lines)

    def replace_descriptions(self, line: str, descriptions: list[re.Match[str]], texts: list[str | None]) -> str:
        for description, text in reversed(list(zip(descriptions, texts))):
            if text is not None:
                line = line[:description.start()] + text + line[description.end():]
        return line


@dataclass(frozen=True)
class WordsFix:
    name: ClassVar[str] = "words"
    enabled: bool
    words: DataList

    def fix(self, entry: "TranslationEntry", movie: Movie) -> tuple[str, ...]:
        words_right, words_wrong = load_words(self.words)
        if words_wrong is None:
            return entry.target_lines
        words_source = [match[0].lower() for line in entry.source_lines for match in WORD.finditer(line)]
        text_source = f" {' '.join(words_source)} "

        # a misspelled word is replaced only when the English entry has its English word, and by one right word only
        def replace_word(match: re.Match[str]) -> str:
            word = match[0]
            rights = {right for english, right in words_right[word.lower()] if f" {english} " in text_source}
            if word.lower() in words_source or len(rights) != 1:
                return word
            right = rights.pop()
            if word.isupper():
                return right.upper()
            return right[:1].upper() + right[1:] if word[0].isupper() else right

        return tuple(words_wrong.sub(replace_word, line) for line in entry.target_lines)


TranslationFix = TagsFix | LeftoversFix | BracketsFix | QuotesFix | DescriptionsFix | WordsFix

FIXES: tuple[TranslationFix, ...] = (
    TagsFix(enabled=True),
    LeftoversFix(enabled=True),
    BracketsFix(enabled=True),
    QuotesFix(enabled=True),
    DescriptionsFix(enabled=True, sounds=FIX_SOUNDS, subjects=FIX_SUBJECTS),
    WordsFix(enabled=True, words=FIX_WORDS),
)

# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
@dataclass(frozen=True)
class TranslationEntry:
    source_lines: tuple[str, ...]
    target_lines: tuple[str, ...]

    @classmethod
    def from_entries(cls, source: srt.Subtitle, target: srt.Subtitle) -> "TranslationEntry":
        return cls(source_lines=tuple(" ".join(line.split()) for line in source.content.split("\n") if line.strip()), target_lines=tuple(target.content.split("\n")))

    @property
    def is_aligned(self) -> bool:
        return len(self.source_lines) == len(self.target_lines)

    @property
    def is_broken(self) -> bool:
        # a blank line, a line without words, or a number the English entry does not have, such as the ids of the model reply
        lines_without_words = sum(1 for line in self.target_lines if not LINE_WORDS.search(FORMATTING_TAGS.sub("", line)))
        numbers_added = set(NUMBER.findall("\n".join(self.target_lines))) - set(NUMBER.findall("\n".join(self.source_lines)))
        return any(not line.strip() for line in self.target_lines) or lines_without_words > sum(1 for line in self.source_lines if not LINE_WORDS.search(FORMATTING_TAGS.sub("", line))) or bool(numbers_added)

    def apply_fixes(self, fixes: tuple[TranslationFix, ...], movie: Movie) -> tuple["TranslationEntry", tuple[str, ...]]:
        # a broken entry is left whole for check-translate; a fix that leaves a blank line is not applied
        if self.is_broken:
            return self, ()
        entry = self
        fixes_applied: list[str] = []
        for fix in fixes:
            if not fix.enabled:
                continue
            lines = fix.fix(entry, movie)
            if lines == entry.target_lines or any(not line.strip() for line in lines):
                continue
            entry = replace(entry, target_lines=lines)
            fixes_applied.append(fix.name)
        return entry, tuple(fixes_applied)


@dataclass(frozen=True)
class EntryChange:
    position: int
    source: srt.Subtitle
    old: srt.Subtitle
    new: srt.Subtitle
    fixes: tuple[str, ...]


@dataclass(frozen=True)
class Translation:
    entries: list[srt.Subtitle]
    sources_by_index: dict[int, srt.Subtitle]
    movie: Movie

    @classmethod
    def from_file(cls, file: Path) -> "Translation":
        source = get_english_source(file)
        return cls(entries=parse_srt(file), sources_by_index={entry.index: entry for entry in parse_srt(source)}, movie=Movie.from_subs_folder(source.parent))

    def fix(self, fixes: tuple[TranslationFix, ...]) -> list[EntryChange]:
        # fix every entry that has a source entry; a changed entry loses its marker, so check-translate judges it again
        changes: list[EntryChange] = []
        for position, entry in enumerate(self.entries):
            source = self.sources_by_index.get(entry.index)
            if source is None:
                continue
            translation, fixes_applied = TranslationEntry.from_entries(source, entry).apply_fixes(fixes, self.movie)
            content = "\n".join(translation.target_lines)
            if content == entry.content:
                continue
            entry_new = srt.Subtitle(index=entry.index, start=entry.start, end=entry.end, content=content, proprietary=IGNORE_MARKER.sub("", entry.proprietary or "").strip())
            changes.append(EntryChange(position=position, source=source, old=entry, new=entry_new, fixes=fixes_applied))
        return changes

    def compose(self, changes: list[EntryChange]) -> str:
        entries = list(self.entries)
        for change in changes:
            entries[change.position] = change.new
        return srt.compose(entries, reindex=False)


@functools.cache
def load_description_table(sounds: DataList, subjects: DataList) -> DescriptionTable:
    return DescriptionTable.from_lists(sounds, subjects)


@functools.cache
def load_words(file: DataList) -> tuple[dict[str, list[tuple[str, str]]], re.Pattern[str] | None]:
    words: dict[str, list[tuple[str, str]]] = {}
    for english, wrong, right in file.parse():
        words.setdefault(wrong.lower(), []).append((" ".join(english.lower().split()), right))
    if not words:
        return words, None
    return words, re.compile(rf"(?<!\w)(?:{'|'.join(re.escape(word) for word in sorted(words, key=len, reverse=True))})(?!\w)", re.IGNORECASE)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fixes the mistakes of the translations that have a mechanical fix, by comparing each entry with its English source. Shows the changes of each translation and asks before writing it.")
    parser.add_argument("--yes", action="store_true", help="write every translation without showing its changes or asking")
    Backups.add_argument(parser)
    subs = find_files(parser, extensions_file=TRANSLATION_SUFFIXES, extensions_folder=TRANSLATION_SUFFIXES)
    arguments = parser.parse_args()
    backups = Backups.start(arguments.backups)
    batch = Batch()

    for sub in subs:
        movie_dir = parse_movie_dir(sub)
        batch.log_movie(movie_dir)

        try:
            # fix the entries against the English source
            translation = Translation.from_file(sub)
            changes = translation.fix(FIXES)

            # skip subs with nothing to fix
            if not changes:
                log_action("skip", Color.BLUE, sub.name)
                continue

            # show every changed entry and ask before writing, unless confirmed beforehand
            fixes_applied = Counter(fix for change in changes for fix in change.fixes)
            summary = f"{sub.name} · {len(changes)} entries · {', '.join(f'{name} {count}' for name, count in fixes_applied.items())}"
            if not arguments.yes:
                for change in changes:
                    console.print(f"    entry {change.old.index}")
                    for prefix, content, color in (("en ", change.source.content, ""), ("old", change.old.content, Color.RED), ("new", change.new.content, Color.GREEN)):
                        for n, line in enumerate(content.split("\n")):
                            console.print(Text.assemble(f"      {prefix if n == 0 else '   '}  ", (line, color)))
                if not Confirm.ask(f"  Apply {summary}?", console=console, default=False):
                    log_action("declined", Color.YELLOW, sub.name)
                    continue

            # back up the sub, write the fixed sub and delete its report, which no longer matches it
            backups.save(sub)
            write_text_replacing(sub, translation.compose(changes))
            (movie_dir / CHECKS_FOLDER / f"{sub.stem}.txt").unlink(missing_ok=True)
            log_action("fix", Color.GREEN, summary)
        except Exception as error:
            batch.fail(sub, error)

    batch.finish()
