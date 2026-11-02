# Fixes the mistakes of the translations that have a mechanical fix, by comparing each entry with its English source, shows the changes of each translation and asks before writing it, backs it up before writing it, and deletes the report of every translation it changes.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import functools
import itertools
import re
import shutil
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import spacy
import srt
from rich.prompt import Confirm
from rich.text import Text

from shared.console import Batch, Color, console, log_action
from shared.data_lists import CHECK_WORDS, FIX_SOUNDS, FIX_SUBJECTS, FIX_WORDS, DataList
from shared.descriptions import DESCRIPTION_SIMPLE, DescriptionTable, normalize_description
from shared.files import CHECKS_FOLDER, DIR_FILMES, SUBS_FOLDER, find_files, find_language_code, parse_movie_dir, parse_srt, write_text_replacing
from shared.languages import WORD
from shared.subtitles import BRACKETS, FORMATTING_TAGS, IGNORE_MARKER, QUOTES, SubtitleKind, parse_plain_text

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
TRANSLATION_SUFFIXES = tuple(kind.to_translation_suffix() for kind in SubtitleKind)

DIR_MIDIAS_ROOT = DIR_FILMES.parent
BACKUPS_FOLDER = DIR_MIDIAS_ROOT / "_backups"

LINE_WORDS = re.compile(r"[^\W\d_]|[♪♫]")
NUMBER = re.compile(r"\d+")

NLP_MODEL = "en_core_web_sm"
NLP_NAME_LABELS = frozenset({"PERSON", "ORG"})
NLP_BATCH_SIZE = 256
SPEAKER_LABEL = re.compile(r"^\s*-?\s*([A-Z][A-Z0-9'.\- ]*[A-Z0-9.]):", re.MULTILINE)

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

    def fix(self, entry: "TranslationEntry") -> tuple[str, ...]:
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

            # each translation from the tables, kept only when the model kept its name, and left out when the model already wrote one of its forms
            translations = [table.find_translation(description[1] or description[2]) for description in descriptions]
            models = [description[1] or description[2] for description in descriptions_target] if paired else [line] * len(descriptions)
            texts: list[str | None] = []
            for description, translation, model in zip(descriptions, translations, models):
                if translation is None or not translation.is_confirmed_by(model, entry.names) or (paired and translation.is_already_in(model)):
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

    def fix(self, entry: "TranslationEntry") -> tuple[str, ...]:
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
    names: frozenset[str]

    @classmethod
    def from_entries(cls, source: srt.Subtitle, target: srt.Subtitle, names: frozenset[str]) -> "TranslationEntry":
        return cls(source_lines=tuple(" ".join(line.split()) for line in source.content.split("\n") if line.strip()), target_lines=tuple(target.content.split("\n")), names=names)

    @property
    def is_aligned(self) -> bool:
        return len(self.source_lines) == len(self.target_lines)

    @property
    def is_broken(self) -> bool:
        # a blank line, a line without words, or a number the English entry does not have, such as the ids of the model reply
        lines_without_words = sum(1 for line in self.target_lines if not LINE_WORDS.search(FORMATTING_TAGS.sub("", line)))
        numbers_added = set(NUMBER.findall("\n".join(self.target_lines))) - set(NUMBER.findall("\n".join(self.source_lines)))
        return any(not line.strip() for line in self.target_lines) or lines_without_words > sum(1 for line in self.source_lines if not LINE_WORDS.search(FORMATTING_TAGS.sub("", line))) or bool(numbers_added)

    def apply(self, fix: TranslationFix) -> "TranslationEntry | None":
        lines = fix.fix(self)
        if lines == self.target_lines or any(not line.strip() for line in lines):
            return None
        return replace(self, target_lines=lines)


@functools.cache
def load_movie_names(subs_folder: Path) -> frozenset[str]:
    # the words of the names in the English subtitles of the movie: the speaker labels, and the names NER finds in the entries that are not in capitals only, since NER reads names from their case
    texts = [parse_plain_text(entry) for file in sorted(subs_folder.glob("*.srt")) if find_language_code(file) == "eng" for entry in parse_srt(file)]
    labels = {word for text in texts for label in SPEAKER_LABEL.finditer(text) for word in normalize_description(label[1]).split()}
    documents = load_nlp().pipe((text.replace("\n", " ") for text in texts if text != text.upper()), batch_size=NLP_BATCH_SIZE)
    entities = {word for document in documents for entity in document.ents if entity.label_ in NLP_NAME_LABELS for word in normalize_description(entity.text).split()}
    words_kept = {word for (line,) in CHECK_WORDS.parse() for word in normalize_description(line).split()}
    return frozenset(labels | entities | words_kept)


@functools.cache
def load_nlp() -> spacy.language.Language:
    return spacy.load(NLP_MODEL, exclude=["tagger", "parser", "attribute_ruler", "lemmatizer"])


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
    subs = find_files(parser, extensions_file=TRANSLATION_SUFFIXES, extensions_folder=TRANSLATION_SUFFIXES)
    confirmed: bool = parser.parse_args().yes
    backups_run = BACKUPS_FOLDER / datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
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

            # fix every entry that has a source entry and no broken line, which check-translate must still see; a changed entry loses its marker, so it is judged again
            entries = parse_srt(sub)
            fixes_applied: Counter[str] = Counter()
            entries_fixed: list[tuple[srt.Subtitle, srt.Subtitle, srt.Subtitle]] = []
            for position, entry in enumerate(entries):
                if entry.index not in sources_by_index:
                    continue
                translation = TranslationEntry.from_entries(sources_by_index[entry.index], entry, load_movie_names(source.parent))
                if translation.is_broken:
                    continue
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

            # back up the sub before writing it, into a folder of its own for this run, so no backup is ever replaced
            sub_absolute = sub.absolute()
            backup = backups_run / (sub_absolute.relative_to(DIR_MIDIAS_ROOT) if sub_absolute.is_relative_to(DIR_MIDIAS_ROOT) else sub_absolute.relative_to(sub_absolute.anchor))
            if backup.exists():
                raise FileExistsError(f"backup already exists: {backup}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sub, backup)
            if backup.read_bytes() != sub.read_bytes():
                raise OSError(f"backup differs from the sub: {backup}")

            # write the fixed sub and delete its report, which no longer matches it
            write_text_replacing(sub, srt.compose(entries, reindex=False))
            (movie_dir / CHECKS_FOLDER / f"{sub.stem}.txt").unlink(missing_ok=True)
            log_action("fix", Color.GREEN, summary)
        except Exception as error:
            batch.fail(sub, error)

    batch.finish()
