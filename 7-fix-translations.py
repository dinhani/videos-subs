# Fixes the mistakes of the translations that have a mechanical fix, by comparing each entry with its English source, shows the changes of each translation and asks before writing it, and deletes the report of every translation it changes.

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

from shared import CHECKS_FOLDER, DESCRIPTION_SIMPLE, FIX_SOUNDS, FIX_SUBJECTS, FIX_WORDS, FORMATTING_TAGS, IGNORE_MARKER, SUBS_FOLDER, WORD, Batch, Color, DescriptionTable, SubtitleKind, console, find_files, log_action, parse_movie_dir, parse_pairs, parse_srt, write_text_replacing

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
TRANSLATION_SUFFIXES = tuple(kind.to_translation_suffix() for kind in SubtitleKind)

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

    def fix(self, entry: "TranslationEntry") -> tuple[str, ...]:
        # delete the tags the English entry does not have
        tags_source = {self.parse_tag_key(match) for line in entry.source_lines for match in self.TAG.finditer(line)}
        lines: list[str] = []
        for line in entry.target_lines:
            line_kept, removed = self.TAG.subn(
                lambda match: match[0] if self.parse_tag_key(match) in tags_source else " " if self.WORDS_JOINED.match(line[match.start() - 1:match.start()] + line[match.end():match.end() + 1]) else "",
                line,
            )
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

    def fix(self, entry: "TranslationEntry") -> tuple[str, ...]:
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
            if line.count(closing) <= line.count(opening):
                continue
            quote = end["quote"] if end["quote"] and source_plain.endswith(end["quote"]) else ""
            lines[n] = (line[:end.start()] + quote).rstrip()
        return tuple(lines)


@dataclass(frozen=True)
class BracketsFix:
    name: ClassVar[str] = "brackets"
    PAIRS: ClassVar[tuple[tuple[str, str], ...]] = (("(", ")"), ("[", "]"))
    TAGS_END: ClassVar[re.Pattern[str]] = re.compile(r"(?:\s*<[^<>]+>)*\s*$")
    TAGS_START: ClassVar[re.Pattern[str]] = re.compile(r"^(?:\{\\[^{}]*\}|<[^<>]+>|[\s\-–—])*")
    LINE_WRAPPED: ClassVar[re.Pattern[str]] = re.compile(r"^(?P<start>(?:\{\\[^{}]*\}|<[^<>]+>|[\s\-–—])*)[\[(](?P<text>[^\[\]()]+)[\])](?P<end>(?:\s*<[^<>]+>)*\s*)$")
    QUOTE: ClassVar[str] = '"'
    enabled: bool

    def fix(self, entry: "TranslationEntry") -> tuple[str, ...]:
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

            for (opening, closing), (opening_other, closing_other) in itertools.permutations(self.PAIRS, 2):
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

        # one quote more than the English entry, at the start or end of the only line where the English line has none
        if sum(line.count(self.QUOTE) for line in lines) != sum(line.count(self.QUOTE) for line in entry.source_lines) + 1:
            return tuple(lines)
        quotes_added: list[tuple[int, int]] = []
        for n, (source_line, line) in enumerate(zip(entry.source_lines, lines)):
            source_plain = FORMATTING_TAGS.sub("", source_line).strip().lstrip("-–— ")
            plain = FORMATTING_TAGS.sub("", line).strip().lstrip("-–— ")
            if plain.endswith(self.QUOTE) and not source_plain.endswith(self.QUOTE):
                quotes_added.append((n, line.rfind(self.QUOTE)))
            if plain.startswith(self.QUOTE) and not source_plain.startswith(self.QUOTE):
                quotes_added.append((n, line.find(self.QUOTE)))
        if len(quotes_added) == 1:
            n, position = quotes_added[0]
            lines[n] = (lines[n][:position] + lines[n][position + 1:]).strip()
        return tuple(lines)


@dataclass(frozen=True)
class DescriptionsFix:
    name: ClassVar[str] = "descriptions"
    LINE_REST: ClassVar[re.Pattern[str]] = re.compile(r"[\s\-–—]")
    enabled: bool
    sounds: Path
    subjects: Path

    def fix(self, entry: "TranslationEntry") -> tuple[str, ...]:
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

            # each translation from the tables, kept only when the model kept its name
            translations = [table.find_translation(description[1] or description[2]) for description in descriptions]
            models = [description[1] or description[2] for description in descriptions_target] if paired else [line] * len(descriptions)
            texts = [
                f"{description[0][0]}{translation.text}{description[0][-1]}" if translation is not None and translation.is_confirmed_by(model) else None
                for description, translation, model in zip(descriptions, translations, models)
            ]

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
    words: Path

    def fix(self, entry: "TranslationEntry") -> tuple[str, ...]:
        words_right, words_wrong = load_words(self.words)
        if words_wrong is None:
            return entry.target_lines
        words_source = {match[0].lower() for line in entry.source_lines for match in WORD.finditer(line)}

        def replace_word(match: re.Match[str]) -> str:
            word = match[0]
            if word.lower() in words_source:
                return word
            right = words_right[word.lower()]
            if word.isupper():
                return right.upper()
            return right[:1].upper() + right[1:] if word[0].isupper() else right

        return tuple(words_wrong.sub(replace_word, line) for line in entry.target_lines)


TranslationFix = TagsFix | LeftoversFix | BracketsFix | DescriptionsFix | WordsFix

FIXES: tuple[TranslationFix, ...] = (
    TagsFix(enabled=True),
    LeftoversFix(enabled=True),
    BracketsFix(enabled=True),
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

    def apply(self, fix: TranslationFix) -> "TranslationEntry | None":
        lines = fix.fix(self)
        if lines == self.target_lines or any(not line.strip() for line in lines):
            return None
        return replace(self, target_lines=lines)


@functools.cache
def load_description_table(sounds: Path, subjects: Path) -> DescriptionTable:
    return DescriptionTable.from_files(sounds, subjects)


@functools.cache
def load_words(file: Path) -> tuple[dict[str, str], re.Pattern[str] | None]:
    words = {wrong.lower(): right for wrong, right in parse_pairs(file).items()}
    if not words:
        return words, None
    return words, re.compile(rf"(?<!\w)(?:{'|'.join(re.escape(word) for word in sorted(words, key=len, reverse=True))})(?!\w)", re.IGNORECASE)

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fixes the mistakes of the translations that have a mechanical fix, by comparing each entry with its English source. Shows the changes of each translation and asks before writing it.")
    parser.add_argument("--yes", action="store_true", help="write every translation without showing its changes or asking")
    subs = find_files(parser.description or "", extensions_file=TRANSLATION_SUFFIXES, extensions_folder=TRANSLATION_SUFFIXES, parser=parser)
    confirmed: bool = parser.parse_args().yes
    batch = Batch()

    for sub in subs:
        movie_dir = parse_movie_dir(sub)
        batch.log_movie(movie_dir)

        try:
            # find the English source
            kind = SubtitleKind.find_in_translation(sub)
            if kind is None:
                raise ValueError(f"not a translation: {sub.name}")
            source = movie_dir / SUBS_FOLDER / f"{sub.name.removesuffix(kind.to_translation_suffix())}.eng{kind}.srt"
            if not source.exists():
                raise FileNotFoundError(f"English source not found: {source}")
            sources_by_index = {entry.index: entry for entry in parse_srt(source)}

            # fix every entry that has a source entry; a changed entry loses its marker, so it is judged again
            entries = parse_srt(sub)
            fixes_applied: Counter[str] = Counter()
            entries_fixed: list[tuple[srt.Subtitle, srt.Subtitle, srt.Subtitle]] = []
            for position, entry in enumerate(entries):
                if entry.index not in sources_by_index:
                    continue
                translation = TranslationEntry.from_entries(sources_by_index[entry.index], entry)
                for fix in FIXES:
                    if not fix.enabled or (translation_fixed := translation.apply(fix)) is None:
                        continue
                    translation = translation_fixed
                    fixes_applied[fix.name] += 1
                content = "\n".join(translation.target_lines)
                if content == entry.content:
                    continue
                entries[position] = srt.Subtitle(index=entry.index, start=entry.start, end=entry.end, content=content, proprietary=IGNORE_MARKER.sub("", entry.proprietary or "").strip())
                entries_fixed.append((sources_by_index[entry.index], entry, entries[position]))

            # skip subs with nothing to fix
            if not entries_fixed:
                log_action("skip", Color.BLUE, sub.name)
                continue

            # show every changed entry and ask before writing, unless confirmed beforehand
            summary = f"{sub.name} · {len(entries_fixed)} entries · {', '.join(f'{name} {count}' for name, count in fixes_applied.items())}"
            if not confirmed:
                for source_entry, entry_old, entry_new in entries_fixed:
                    console.print(f"    entry {entry_old.index}")
                    for prefix, content, color in (("en ", source_entry.content, ""), ("old", entry_old.content, Color.RED), ("new", entry_new.content, Color.GREEN)):
                        for n, line in enumerate(content.split("\n")):
                            console.print(Text.assemble(f"      {prefix if n == 0 else '   '}  ", (line, color)))
                if not Confirm.ask(f"  Apply {summary}?", console=console, default=False):
                    log_action("declined", Color.YELLOW, sub.name)
                    continue

            # write the fixed sub and delete its report, which no longer matches it
            write_text_replacing(sub, srt.compose(entries, reindex=False))
            (movie_dir / CHECKS_FOLDER / f"{sub.stem}.txt").unlink(missing_ok=True)
            log_action("fix", Color.GREEN, summary)
        except Exception as error:
            batch.fail(sub, error)

    batch.finish()
