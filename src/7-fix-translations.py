# Fixes the mistakes of the translations that have a mechanical fix, by comparing each entry with its English source, shows the changes of each translation and asks before writing it, backs it up before writing it, and deletes the report of every translation it changes.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import functools
import itertools
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from typing import ClassVar

import readchar
import srt
from rich.text import Text

from shared.console import Action, Batch, Color, console
from shared.data_lists import FIX_CHANNELS, FIX_EXPRESSIONS, FIX_INTERJECTIONS, FIX_LANGUAGES, FIX_MUSIC_MOODS, FIX_PORTUGAL, FIX_SOUND_LISTS, FIX_SUBJECTS, FIX_VOICE_MOODS, FIX_WORDS, DataList
from shared.descriptions import DESCRIPTION_SIMPLE, SPEAKER_LABEL, DescriptionTable, MovieNames, match_case
from shared.files import Backups, SubtitleFile, SubtitleFormat, find_subtitles, parse_srt, write_text_replacing
from shared.languages import WORD
from shared.subtitles import BRACKETS, FORMATTING_TAGS, LANGUAGE_TRANSLATION, QUOTES, parse_lines, replace_text

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
LINE_WORDS = re.compile(r"[^\W\d_]|[♪♫]")
NUMBER = re.compile(r"\d+")

READ_WORKERS = 8

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

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
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

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
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

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
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

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
        if not entry.is_aligned:
            return entry.target_lines
        lines = list(entry.target_lines)
        quotes_extra = sum(line.count(quote) for line in lines for quote in QUOTES) - sum(line.count(quote) for line in entry.source_lines for quote in QUOTES)

        # one quote less than the English entry, at the start or end of the only line where the English line has it; the quote is the one the English line has
        if quotes_extra == -1:
            quotes_missing: list[tuple[int, str, str]] = []
            for n, (source_line, line) in enumerate(zip(entry.source_lines, lines)):
                source_plain = FORMATTING_TAGS.sub("", source_line).strip().lstrip("-–— ")
                plain = FORMATTING_TAGS.sub("", line).strip().lstrip("-–— ")
                if source_plain.startswith(tuple(QUOTES)) and not plain.startswith(tuple(QUOTES)):
                    quotes_missing.append((n, "start", source_plain[0]))
                if source_plain.endswith(tuple(QUOTES)) and not plain.endswith(tuple(QUOTES)):
                    quotes_missing.append((n, "end", source_plain[-1]))
            if len(quotes_missing) == 1:
                n, place, quote = quotes_missing[0]
                position = BracketsFix.TAGS_START.match(lines[n]).end() if place == "start" else BracketsFix.TAGS_END.search(lines[n]).start()
                lines[n] = lines[n][:position] + quote + lines[n][position:]
            return tuple(lines)

        # one quote more than the English entry, at the start or end of the only line where the English line has none; curly quotes count as quotes
        if quotes_extra != 1:
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
class DashesFix:
    name: ClassVar[str] = "dashes"
    START: ClassVar[re.Pattern[str]] = re.compile(r"^(?:\{\\[^{}]*\}|<[^<>]+>|\s)*")
    DASH: ClassVar[re.Pattern[str]] = re.compile(r"[-–—](?![-–—])\s*")
    DASHES: ClassVar[str] = "-–—"
    enabled: bool

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
        # the dialogue dash of each English line: added when the line lost it, removed when the English line has no dash at all
        if not entry.is_aligned:
            return entry.target_lines
        lines: list[str] = []
        for source_line, line in zip(entry.source_lines, entry.target_lines):
            source_start, start = self.START.match(source_line).end(), self.START.match(line).end()
            source_dash, dash = self.DASH.match(source_line, source_start), self.DASH.match(line, start)
            if source_dash is not None and not line[start:start + 1] in self.DASHES and line[start:].strip():
                lines.append(line[:start] + source_dash[0] + line[start:])
            elif dash is not None and not source_line[source_start:source_start + 1] in self.DASHES:
                lines.append(line[:start] + line[dash.end():])
            else:
                lines.append(line)
        return tuple(lines)


@dataclass(frozen=True)
class SymbolsFix:
    name: ClassVar[str] = "symbols"
    SPANISH: ClassVar[re.Pattern[str]] = re.compile(r"[¿¡]")
    enabled: bool

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
        # the Spanish opening marks, when the English entry has none
        if any(self.SPANISH.search(line) for line in entry.source_lines):
            return entry.target_lines
        return tuple(self.SPANISH.sub("", line) for line in entry.target_lines)


@dataclass(frozen=True)
class DescriptionsFix:
    name: ClassVar[str] = "descriptions"
    LINE_REST: ClassVar[re.Pattern[str]] = re.compile(r"[\s\-–—]")
    enabled: bool
    sounds: tuple[DataList, ...]
    subjects: DataList
    music_moods: DataList
    voice_moods: DataList
    languages: DataList
    channels: DataList

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
        if not entry.is_aligned:
            return entry.target_lines
        table = load_description_table(self.sounds, self.subjects, self.music_moods, self.voice_moods, self.languages, self.channels)
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
            # the spaces inside the brackets come from the model's description at the same place, so the entry keeps one style, or from the English one when the model has none
            spacings = descriptions_target if paired else descriptions
            texts: list[str | None] = []
            for description, translation, model, spacing in zip(descriptions, translations, models, spacings):
                if translation is None or not translation.is_confirmed_by(model, movie_names) or (paired and translation.is_already_in(model)):
                    texts.append(None)
                    continue
                inner = spacing[0][1:-1]
                space_before, space_after = inner[:len(inner) - len(inner.lstrip())], inner[len(inner.rstrip()):]
                texts.append(f"{description[0][0]}{space_before}{translation.text}{space_after}{description[0][-1]}")

            # a line that is only descriptions is the English line with the descriptions translated
            line_whole = not self.LINE_REST.sub("", FORMATTING_TAGS.sub("", DESCRIPTION_SIMPLE.sub("", source_line)))
            if line_whole and all(text is not None for text in texts):
                lines[n] = self.replace_descriptions(source_line, descriptions, texts)
            elif paired:
                lines[n] = self.replace_descriptions(line, descriptions_target, texts)

        # a speaker label the model kept in English, such as MAN 2, written from the tables; a name stays as it is
        for n, (source_line, line) in enumerate(zip(entry.source_lines, lines)):
            source_label, label = SPEAKER_LABEL.match(FORMATTING_TAGS.sub("", source_line)), SPEAKER_LABEL.match(line)
            if source_label is None or label is None or label[1] != source_label[1]:
                continue
            translation = table.find_translation(label[1])
            subject_forms = table.find_subject(label[1].lower())
            text = translation.text if translation is not None and not translation.name else subject_forms[0].upper() if subject_forms else None
            if text is not None:
                lines[n] = line[:label.start(1)] + text + line[label.end(1):]
        return tuple(lines)

    def replace_descriptions(self, line: str, descriptions: list[re.Match[str]], texts: list[str | None]) -> str:
        for description, text in reversed(list(zip(descriptions, texts))):
            if text is not None:
                line = line[:description.start()] + text + line[description.end():]
        return line


@dataclass(frozen=True)
class InterjectionsFix:
    name: ClassVar[str] = "interjections"
    SONG: ClassVar[re.Pattern[str]] = re.compile(r"[♪♫]")
    enabled: bool
    interjections: DataList

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
        if not entry.is_aligned:
            return entry.target_lines
        interjections, pattern = load_interjections(self.interjections)
        if pattern is None:
            return entry.target_lines

        # an interjection the model kept in English is replaced on the line whose English has it; song lines stay as they are
        lines: list[str] = []
        for source_line, line in zip(entry.source_lines, entry.target_lines):
            source_words = {match[0].lower() for match in pattern.finditer(source_line)}
            if self.SONG.search(source_line) or not source_words:
                lines.append(line)
                continue
            lines.append(pattern.sub(lambda match: match_case(interjections[match[0].lower()], match[0]) if match[0].lower() in source_words else match[0], line))
        return tuple(lines)


@dataclass(frozen=True)
class ExpressionsFix:
    name: ClassVar[str] = "expressions"
    enabled: bool
    expressions: DataList

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
        rights, wrongs = load_expressions(self.expressions)
        if wrongs is None:
            return entry.target_lines
        text_source = f" {' '.join(match[0].lower() for line in entry.source_lines for match in WORD.finditer(line))} "

        # a wrong translation of an English expression is replaced only when the English entry has that expression, by one right translation only, and only where it ends its clause, so a command stays a command
        def replace_expression(match: re.Match[str]) -> str:
            expression = match[0]
            rights_found = {right for english, right in rights[" ".join(expression.lower().split())] if f" {english} " in text_source}
            return match_case(rights_found.pop(), expression) if len(rights_found) == 1 else expression

        return tuple(wrongs.sub(replace_expression, line) for line in entry.target_lines)


@dataclass(frozen=True)
class WordsFix:
    name: ClassVar[str] = "words"
    enabled: bool
    words: DataList

    def fix(self, entry: "TranslationEntry", movie_names: MovieNames) -> tuple[str, ...]:
        words_right, words_wrong = load_words(self.words)
        if words_wrong is None:
            return entry.target_lines
        words_source = [match[0].lower() for line in entry.source_lines for match in WORD.finditer(line)]
        text_source = f" {' '.join(words_source)} "

        # a misspelled word, or a word of Portugal, is replaced only when the English entry has its English word, and by one right word only
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


TranslationFix = TagsFix | LeftoversFix | BracketsFix | QuotesFix | DashesFix | SymbolsFix | DescriptionsFix | InterjectionsFix | ExpressionsFix | WordsFix

FIXES: tuple[TranslationFix, ...] = (
    TagsFix(enabled=True),
    LeftoversFix(enabled=True),
    BracketsFix(enabled=True),
    QuotesFix(enabled=True),
    DashesFix(enabled=True),
    SymbolsFix(enabled=True),
    DescriptionsFix(enabled=True, sounds=FIX_SOUND_LISTS, subjects=FIX_SUBJECTS, music_moods=FIX_MUSIC_MOODS, voice_moods=FIX_VOICE_MOODS, languages=FIX_LANGUAGES, channels=FIX_CHANNELS),
    InterjectionsFix(enabled=True, interjections=FIX_INTERJECTIONS),
    ExpressionsFix(enabled=True, expressions=FIX_EXPRESSIONS),
    WordsFix(enabled=True, words=FIX_WORDS),
    WordsFix(enabled=True, words=FIX_PORTUGAL),
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
        return cls(source_lines=parse_lines(source), target_lines=tuple(target.content.split("\n")))

    @property
    def is_aligned(self) -> bool:
        return len(self.source_lines) == len(self.target_lines)

    @property
    def is_broken(self) -> bool:
        # a blank line, a line without words, or a number the English entry does not have, such as the ids of the model reply
        lines_without_words = sum(1 for line in self.target_lines if not LINE_WORDS.search(FORMATTING_TAGS.sub("", line)))
        numbers_added = set(NUMBER.findall("\n".join(self.target_lines))) - set(NUMBER.findall("\n".join(self.source_lines)))
        return any(not line.strip() for line in self.target_lines) or lines_without_words > sum(1 for line in self.source_lines if not LINE_WORDS.search(FORMATTING_TAGS.sub("", line))) or bool(numbers_added)

    def apply_fixes(self, fixes: tuple[TranslationFix, ...], movie_names: MovieNames) -> tuple["TranslationEntry", tuple[str, ...]]:
        # a broken entry is left whole for check-translate; a fix that leaves a blank line is not applied
        if self.is_broken:
            return self, ()
        entry = self
        fixes_applied: list[str] = []
        for fix in fixes:
            if not fix.enabled:
                continue
            lines = fix.fix(entry, movie_names)
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
    movie_names: MovieNames

    @classmethod
    def from_file(cls, file: SubtitleFile) -> "Translation":
        source = file.get_english_source()
        return cls(entries=parse_srt(file.path), sources_by_index={entry.index: entry for entry in parse_srt(source.path)}, movie_names=MovieNames.from_subs_folder(source.folder))

    def fix(self, fixes: tuple[TranslationFix, ...]) -> list[EntryChange]:
        # fix every entry that has a source entry; a changed entry loses its marker, so check-translate judges it again
        changes: list[EntryChange] = []
        for position, entry in enumerate(self.entries):
            source = self.sources_by_index.get(entry.index)
            if source is None:
                continue
            translation, fixes_applied = TranslationEntry.from_entries(source, entry).apply_fixes(fixes, self.movie_names)
            content = "\n".join(translation.target_lines)
            if content == entry.content:
                continue
            changes.append(EntryChange(position=position, source=source, old=entry, new=replace_text(entry, content), fixes=fixes_applied))
        return changes

    def compose(self, changes: list[EntryChange]) -> str:
        entries = list(self.entries)
        for change in changes:
            entries[change.position] = change.new
        return srt.compose(entries, reindex=False)


@functools.cache
def load_description_table(sounds: tuple[DataList, ...], subjects: DataList, music_moods: DataList, voice_moods: DataList, languages: DataList, channels: DataList) -> DescriptionTable:
    return DescriptionTable.from_lists(sounds=sounds, subjects=subjects, music_moods=music_moods, voice_moods=voice_moods, languages=languages, channels=channels)


@functools.cache
def load_interjections(file: DataList) -> tuple[dict[str, str], re.Pattern[str] | None]:
    interjections = dict(file.parse())
    if not interjections:
        return interjections, None
    return interjections, re.compile(rf"(?<![\w-])(?:{'|'.join(re.escape(word) for word in sorted(interjections, key=len, reverse=True))})(?![\w-])", re.IGNORECASE)


@functools.cache
def load_expressions(file: DataList) -> tuple[dict[str, list[tuple[str, str]]], re.Pattern[str] | None]:
    expressions: dict[str, list[tuple[str, str]]] = {}
    for english, wrong, right in file.parse():
        expressions.setdefault(" ".join(wrong.lower().split()), []).append((" ".join(english.lower().split()), right))
    if not expressions:
        return expressions, None
    alternatives = "|".join(r"\s+".join(re.escape(word) for word in wrong.split()) for wrong in sorted(expressions, key=len, reverse=True))
    return expressions, re.compile(rf"(?<!\w)(?:{alternatives})(?=\s*(?:[.!?,;…]|$))", re.IGNORECASE)


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
    subs = find_subtitles(parser, formats=(SubtitleFormat.TRANSLATION,), language=LANGUAGE_TRANSLATION)
    arguments = parser.parse_args()
    backups = Backups.start(arguments.backups)

    # read the subs READ_WORKERS at a time, ahead of the one being fixed
    with Batch() as batch, ThreadPoolExecutor(max_workers=READ_WORKERS) as executor:
        translations_read = {sub: executor.submit(Translation.from_file, sub) for sub in subs}
        for sub in batch.walk(subs):
            with batch.attempt(sub.path):
                # fix the entries against the English source
                translation = translations_read[sub].result()
                changes = translation.fix(FIXES)

                # skip subs with nothing to fix
                if not changes:
                    Action.SKIP.log(sub.name)
                    continue

                # show every changed entry and ask before writing, unless confirmed beforehand
                fixes_applied = Counter(fix for change in changes for fix in change.fixes)
                summary = f"{sub.name} · {len(changes)} entries · {', '.join(f'{name} {count}' for name, count in fixes_applied.items())}"
                if not arguments.yes:
                    batch.clear_screen()
                    for change in changes:
                        console.print(f"    entry {change.old.index}")
                        for prefix, content, color in (("en ", change.source.content, ""), ("old", change.old.content, Color.RED), ("new", change.new.content, Color.GREEN)):
                            for n, line in enumerate(content.split("\n")):
                                console.print(Text.assemble(f"      {prefix if n == 0 else '   '}  ", (line, color)))
                    console.print(Text(f"  Apply {summary}? (y/N) "), end="")
                    answer = ""
                    while answer not in ("y", "n"):
                        answer = readchar.readkey().lower().replace(readchar.key.ENTER, "n")
                    if answer == "n":
                        Action.DECLINED.log(sub.name)
                        continue

                # back up the sub, write the fixed sub and delete its report, which no longer matches it
                backups.save(sub.path)
                write_text_replacing(sub.path, translation.compose(changes))
                sub.to_report().unlink(missing_ok=True)
                Action.FIX.log(summary)
