# Collects statistics of the English text subtitles: what repeats across titles in the dialogue and in the descriptions, and what the description tables of fix-translations do not cover. It shows what fix-translations could translate with fixed rules and what the translation prompt should cover.

# ------------------------------------------------------------------------------
# Libraries
# ------------------------------------------------------------------------------
import argparse
import functools
import itertools
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from pathlib import Path
from typing import NamedTuple

import spacy

from shared.console import Action, Batch, Color, cancel_on_interrupt, log_heading
from shared.data_lists import DATA_FOLDER, FIX_CHANNELS, FIX_LANGUAGES, FIX_MUSIC_MOODS, FIX_SOUND_LISTS, FIX_SUBJECTS, FIX_VERBS, FIX_VOICE_MOODS
from shared.descriptions import DESCRIPTION_SIMPLE, DESCRIPTION_SUBJECT_WORDS_MAX, NLP_MODEL, SPEAKER_LABEL, DescriptionTable
from shared.files import SubtitleFile, SubtitleFormat, find_subtitles, parse_srt, write_text_replacing
from shared.languages import WORD
from shared.subtitles import LANGUAGE_SOURCE, OCR_FAILED_TEXT, QUOTES, parse_plain_text

# ------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------
OUTPUT_DEFAULT = DATA_FOLDER / "stats"
SUMMARY_FILE = "summary.txt"
TITLES_MIN_DEFAULT = 3
ROWS_DEFAULT = 300

NAS_WORKERS = 8

NLP_BATCH_SIZE = 256
NLP_VERB = "VERB"
NLP_INTERJECTION = "INTJ"
NLP_PARTICLE = "prt"
NLP_ADDRESS = frozenset({"NOUN", "PROPN"})
ADDRESS_WORDS_MAX = 2

MUSIC = re.compile(r"[♪♫]")
TURN_DASH = re.compile(r"^\s*[\-–—]+\s*")
SENTENCE_BREAK = re.compile(r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bLt\.)(?<!\bJr\.)(?<!\bSr\.)(?<=[.!?…])\s+")
SENTENCE_CHARACTERS = str.maketrans({"’": "'", **{quote: None for quote in QUOTES}})
SENTENCE_EDGES = " .!,…;:-–—"
WORD_HYPHENATED = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
SOUND_VERB_ENDINGS = ("s", "ing")

WORD_SEQUENCE_SIZES = range(3, 6)
WORD_SEQUENCE_EDGES = frozenset({"a", "an", "the", "of", "to", "and", "or", "but", "for", "with", "at", "from", "by", "as", "my", "your", "his", "her", "our", "their", "its"})
WORD_SEQUENCE_SUBSUMED_SHARE = 0.8

TEMPLATE_WORDS_MAX = 5
TEMPLATE_GAP = "*"
TEMPLATE_FILLERS_MIN = 3

DETAILS_SHOWN = 6

# ------------------------------------------------------------------------------
# Args
# ------------------------------------------------------------------------------
def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collects statistics of the English text subtitles, to find what fix-translations could translate with fixed rules and what the translation prompt should cover.")
    parser.add_argument("--output", type=Path, default=OUTPUT_DEFAULT, help=f"folder to write the report files to (default: {OUTPUT_DEFAULT})")
    parser.add_argument("--titles-min", type=int, default=TITLES_MIN_DEFAULT, help=f"list only items found in at least this many movies or series (default: {TITLES_MIN_DEFAULT})")
    parser.add_argument("--rows", type=int, default=ROWS_DEFAULT, help=f"most items listed in each section (default: {ROWS_DEFAULT})")
    return parser

# ------------------------------------------------------------------------------
# Types
# ------------------------------------------------------------------------------
class Section(Enum):
    SENTENCES = ("sentences.txt", "Sentences of the dialogue", "", 0)
    WORD_SEQUENCES = ("word-sequences.txt", "Word sequences of the dialogue, without a sequence that a longer one holds in most of its count", "", 0)
    PHRASAL_VERBS = ("phrasal-verbs.txt", "Phrasal verbs of the dialogue, by the lemma of the verb", "forms", 0)
    INTERJECTIONS = ("interjections.txt", "Interjections of the dialogue", "", 0)
    ADDRESSES = ("addresses.txt", "Nouns that address a person, at the start or the end of a sentence", "", 0)
    LABELS = ("labels.txt", "Speaker labels", "", 0)
    DESCRIPTIONS = ("descriptions.txt", "Descriptions the tables of fix-translations do not cover", "", 0)
    PHRASES = ("description-phrases.txt", "Phrases after a subject, in description parts the tables do not cover, missing from the sound lists", "subjects", 0)
    SUBJECTS = ("description-subjects.txt", "Subjects before a verb, in description parts the tables do not translate with a subject, missing from fix-subjects", "phrases", 0)
    DESCRIPTION_VERBS = ("description-verbs.txt", "Verbs of description parts, by lemma", "tables", 0)
    TEMPLATES = ("description-templates.txt", f"Patterns of description parts the tables do not cover, with one word as {TEMPLATE_GAP}", "words", TEMPLATE_FILLERS_MIN)

    def __init__(self, file: str, heading: str, details_label: str, details_min: int) -> None:
        self.file = file
        self.heading = heading
        self.details_label = details_label
        self.details_min = details_min


class Measure(Enum):
    ENTRIES = ("entries", False)
    ENTRIES_DESCRIBED = ("entries with a description", False)
    ENTRIES_LABELED = ("entries with a speaker label", False)
    ENTRIES_MUSIC = ("entries with ♪", False)
    DESCRIPTIONS_BRACKETS = ("descriptions in [ ]", True)
    DESCRIPTIONS_PARENTHESES = ("descriptions in ( )", True)
    DESCRIPTIONS_CAPITALS = ("descriptions in capitals", True)

    def __init__(self, label: str, of_descriptions: bool) -> None:
        self.label = label
        self.of_descriptions = of_descriptions


class Observation(NamedTuple):
    key: str
    form: str
    detail: str


class DescriptionStatus(StrEnum):
    COVERED = "covered"
    NAMED = "subject read as a name"
    UNCOVERED = "not covered"

    @classmethod
    def from_text(cls, text: str) -> "DescriptionStatus":
        translation = load_description_table().find_translation(text)
        if translation is None:
            return cls.UNCOVERED
        return cls.NAMED if translation.name else cls.COVERED


@dataclass(frozen=True)
class DescriptionVerb:
    lemma: str
    subject: str
    phrase: str

    @classmethod
    @functools.cache
    def find(cls, text: str) -> "DescriptionVerb | None":
        # the parse reads the description in lowercase, and a capitalized word of a description not in capitals is a name
        text_lower = text.lower()
        words = [token for token in load_parser()(text_lower) if not token.is_punct and (text.isupper() or not text[token.idx].isupper())]
        verb = next((token for token in words if token.pos_ == NLP_VERB and text_lower[token.idx - 1:token.idx] in ("", " ")), None)
        if verb is None and len(words) == 1 and words[0].lower_.endswith(SOUND_VERB_ENDINGS):
            verb = words[0]
        if verb is None:
            return None
        return cls(lemma=verb.lemma_.lower(), subject=text_lower[:verb.idx].strip(), phrase=text_lower[verb.idx:].strip())

    def has_table_subject(self) -> bool:
        return 1 <= len(self.subject.split()) <= DESCRIPTION_SUBJECT_WORDS_MAX


@dataclass(frozen=True)
class DescriptionPart:
    text: str
    status: DescriptionStatus
    verb: DescriptionVerb | None

    @classmethod
    @functools.cache
    def parse(cls, raw: str) -> "DescriptionPart":
        text = " ".join(raw.split())
        return cls(text=text.lower(), status=DescriptionStatus.from_text(raw), verb=DescriptionVerb.find(text))

    def to_observations(self) -> Iterator[tuple[Section, Observation]]:
        table = load_description_table()
        if self.verb is not None:
            yield Section.DESCRIPTION_VERBS, Observation(key=self.verb.lemma, form="", detail=self.status.value)
        if self.verb is not None and self.verb.has_table_subject() and self.status is not DescriptionStatus.COVERED:
            if self.status is DescriptionStatus.UNCOVERED and self.verb.phrase not in table.sounds:
                yield Section.PHRASES, Observation(key=self.verb.phrase, form="", detail=self.verb.subject)
            if self.verb.subject not in table.subjects:
                yield Section.SUBJECTS, Observation(key=self.verb.subject, form="", detail=self.verb.phrase)
        words = self.text.split()
        if self.status is DescriptionStatus.UNCOVERED and 2 <= len(words) <= TEMPLATE_WORDS_MAX:
            for n, word in enumerate(words):
                template = " ".join(TEMPLATE_GAP if m == n else other for m, other in enumerate(words))
                yield Section.TEMPLATES, Observation(key=template, form="", detail=word)


@dataclass(frozen=True)
class Observations:
    sections: dict[Section, Counter[Observation]]
    descriptions: Counter[DescriptionStatus]
    measures: Counter[Measure]
    tracks: int
    videos: int

    @classmethod
    def create(cls) -> "Observations":
        return cls(sections={section: Counter() for section in Section}, descriptions=Counter(), measures=Counter(), tracks=0, videos=0)

    @classmethod
    def from_file(cls, sub: SubtitleFile) -> "Observations":
        sections: dict[Section, Counter[Observation]] = {section: Counter() for section in Section}
        descriptions: Counter[DescriptionStatus] = Counter()
        measures: Counter[Measure] = Counter()
        turns: list[str] = []
        for entry in parse_srt(sub.path):
            text = parse_plain_text(entry)
            measures[Measure.ENTRIES] += 1
            for measure, pattern in ((Measure.ENTRIES_DESCRIBED, DESCRIPTION_SIMPLE), (Measure.ENTRIES_LABELED, SPEAKER_LABEL), (Measure.ENTRIES_MUSIC, MUSIC)):
                if pattern.search(text):
                    measures[measure] += 1

            entry_turns: list[str] = []
            for line in text.split("\n"):
                # descriptions, whole and in the parts the tables read on their own
                for match in DESCRIPTION_SIMPLE.finditer(line):
                    if match[0] == OCR_FAILED_TEXT:
                        continue
                    raw = match[1] or match[2]
                    status = DescriptionStatus.from_text(raw)
                    descriptions[status] += 1
                    measures[Measure.DESCRIPTIONS_BRACKETS if match[0].startswith("[") else Measure.DESCRIPTIONS_PARENTHESES] += 1
                    if raw.isupper():
                        measures[Measure.DESCRIPTIONS_CAPITALS] += 1
                    if status is DescriptionStatus.UNCOVERED:
                        sections[Section.DESCRIPTIONS][Observation(key=" ".join(raw.lower().split()), form=" ".join(match[0].split()), detail="")] += 1
                    for part_raw in raw.split(","):
                        if not part_raw.strip():
                            continue
                        for section, observation in DescriptionPart.parse(part_raw.strip()).to_observations():
                            sections[section][observation] += 1

                # speaker label, which starts a turn like a dash
                label = SPEAKER_LABEL.match(line)
                turn_new = TURN_DASH.match(line) is not None or label is not None
                if label is not None:
                    sections[Section.LABELS][Observation(key=" ".join(label[1].lower().split()), form=label[1], detail="")] += 1
                    line = line[label.end():]

                # dialogue, without song lines and descriptions, joined across the lines of one turn
                if MUSIC.search(line):
                    continue
                dialogue = " ".join(TURN_DASH.sub("", DESCRIPTION_SIMPLE.sub(" ", line)).split())
                if not dialogue:
                    continue
                if turn_new or not entry_turns:
                    entry_turns.append(dialogue)
                else:
                    entry_turns[-1] = f"{entry_turns[-1]} {dialogue}"
            turns += entry_turns

        # sentences and word sequences of each turn
        for turn in turns:
            for sentence in SENTENCE_BREAK.split(turn):
                form = " ".join(sentence.translate(SENTENCE_CHARACTERS).split())
                key = form.lower().strip(SENTENCE_EDGES)
                if not WORD.search(key):
                    continue
                sections[Section.SENTENCES][Observation(key=key, form=form, detail="")] += 1
                words = [word.lower() for word in WORD.findall(form)]
                for size in WORD_SEQUENCE_SIZES:
                    for start in range(len(words) - size + 1):
                        sequence = words[start:start + size]
                        if sequence[0] in WORD_SEQUENCE_EDGES or sequence[-1] in WORD_SEQUENCE_EDGES:
                            continue
                        sections[Section.WORD_SEQUENCES][Observation(key=" ".join(sequence), form="", detail="")] += 1

        # phrasal verbs, interjections and addresses, from the parse of each turn
        for turn, document in zip(turns, load_parser().pipe(turns, batch_size=NLP_BATCH_SIZE)):
            for token in document:
                if token.dep_ == NLP_PARTICLE:
                    sections[Section.PHRASAL_VERBS][Observation(key=f"{token.head.lemma_.lower()} {token.lower_}", form="", detail=f"{token.head.lower_} {token.lower_}")] += 1

            interjection_starts = {token.idx for token in document if token.pos_ == NLP_INTERJECTION}
            for match in WORD_HYPHENATED.finditer(turn):
                if any(start in interjection_starts for start in range(match.start(), match.end())):
                    sections[Section.INTERJECTIONS][Observation(key=match[0].lower().replace("’", "'"), form="", detail="")] += 1

            for sentence in document.sents:
                tokens = list(sentence)
                for comma in (n for n, token in enumerate(tokens) if token.text == ","):
                    before, after = tokens[:comma], tokens[comma + 1:]
                    words_after = list(itertools.takewhile(lambda token: not token.is_punct, after))
                    addresses = []
                    if 1 <= len(before) <= ADDRESS_WORDS_MAX and any(not token.is_punct for token in after):
                        addresses.append(before)
                    if 1 <= len(words_after) <= ADDRESS_WORDS_MAX and all(token.is_punct for token in after[len(words_after):]) and any(not token.is_punct for token in before):
                        addresses.append(words_after)
                    for address in addresses:
                        if all(token.pos_ in NLP_ADDRESS for token in address):
                            sections[Section.ADDRESSES][Observation(key=" ".join(token.lower_ for token in address), form=" ".join(token.text for token in address), detail="")] += 1
        return cls(sections=sections, descriptions=descriptions, measures=measures, tracks=1, videos=1)

    def with_track(self, other: "Observations") -> "Observations":
        # the tracks of one video repeat the same dialogue, so a video counts each item as often as its track with the most of it
        return Observations(
            sections={section: self.sections[section] | other.sections[section] for section in Section},
            descriptions=self.descriptions | other.descriptions,
            measures=self.measures | other.measures,
            tracks=self.tracks + other.tracks,
            videos=max(self.videos, other.videos),
        )

    def with_video(self, other: "Observations") -> "Observations":
        return Observations(
            sections={section: self.sections[section] + other.sections[section] for section in Section},
            descriptions=self.descriptions + other.descriptions,
            measures=self.measures + other.measures,
            tracks=self.tracks + other.tracks,
            videos=self.videos + other.videos,
        )


@dataclass
class Tally:
    counts: Counter[str] = field(default_factory=Counter)
    titles: Counter[str] = field(default_factory=Counter)
    forms: Counter[tuple[str, str]] = field(default_factory=Counter)
    details: Counter[tuple[str, str]] = field(default_factory=Counter)

    def add_title(self, observations: Counter[Observation]) -> None:
        for observation, count in observations.items():
            self.counts[observation.key] += count
            if observation.form:
                self.forms[(observation.key, observation.form)] += count
            if observation.detail:
                self.details[(observation.key, observation.detail)] += count
        self.titles.update({observation.key for observation in observations})

    def to_lines(self, section: Section, titles_min: int, rows: int) -> list[str]:
        details_by_key: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for (key, detail), count in self.details.items():
            details_by_key[key][detail] += count
        keys = [key for key, titles in self.titles.items() if titles >= titles_min and len(details_by_key.get(key, ())) >= section.details_min]

        if section is Section.WORD_SEQUENCES:
            keys_kept = set(keys)
            keys_subsumed: set[str] = set()
            for key in keys:
                words = key.split(" ")
                for inner in (" ".join(words[:-1]), " ".join(words[1:])):
                    if inner in keys_kept and self.counts[key] >= WORD_SEQUENCE_SUBSUMED_SHARE * self.counts[inner]:
                        keys_subsumed.add(inner)
            keys = [key for key in keys if key not in keys_subsumed]

        keys_shown = sorted(keys, key=lambda key: (-self.counts[key], key))[:rows]
        keys_shown_set = set(keys_shown)
        forms_by_key: defaultdict[str, Counter[str]] = defaultdict(Counter)
        for (key, form), count in self.forms.items():
            if key in keys_shown_set:
                forms_by_key[key][form] += count

        lines = [f"{'count':>8}  {'titles':>6}  text"]
        for key in keys_shown:
            text = forms_by_key[key].most_common(1)[0][0] if key in forms_by_key else key
            line = f"{self.counts[key]:>8}  {self.titles[key]:>6}  {text}"
            details = details_by_key.get(key)
            if details:
                line += f"   · {section.details_label} ({len(details)}): {', '.join(f'{detail} {count}' for detail, count in details.most_common(DETAILS_SHOWN))}"
            lines.append(line)
        return lines


@dataclass
class Library:
    tallies: dict[Section, Tally]
    descriptions: Counter[DescriptionStatus]
    measures: Counter[Measure]
    tracks: int
    videos: int
    titles: int

    @classmethod
    def create(cls) -> "Library":
        return cls(tallies={section: Tally() for section in Section}, descriptions=Counter(), measures=Counter(), tracks=0, videos=0, titles=0)

    def add_title(self, observations: Observations) -> None:
        for section in Section:
            self.tallies[section].add_title(observations.sections[section])
        self.descriptions += observations.descriptions
        self.measures += observations.measures
        self.tracks += observations.tracks
        self.videos += observations.videos
        self.titles += 1

    def describe(self) -> str:
        return f"{self.tracks} tracks, {self.videos} videos, {self.titles} titles"

    def to_reports(self, titles_min: int, rows: int) -> dict[str, str]:
        descriptions_total = self.descriptions.total()
        entries_total = self.measures[Measure.ENTRIES]
        summary = ["English subtitles statistics", "", self.describe()]
        for measure in Measure:
            total = descriptions_total if measure.of_descriptions else entries_total
            summary.append(f"{measure.label}: {self.measures[measure]}" + (f" ({self.measures[measure] / total:.1%})" if total and measure != Measure.ENTRIES else ""))
        summary.append(f"descriptions: {descriptions_total}" + "".join(f", {status.value} {self.descriptions[status] / descriptions_total:.1%}" for status in DescriptionStatus if descriptions_total))
        summary += ["", f"Each file lists the items found in at least {titles_min} titles, at most {rows} of them. A video counts each item as often as its track with the most of it."]
        reports = {SUMMARY_FILE: "\n".join(summary) + "\n"}
        for section in Section:
            reports[section.file] = "\n".join([section.heading, "", *self.tallies[section].to_lines(section, titles_min=titles_min, rows=rows)]) + "\n"
        return reports


@functools.cache
def load_description_table() -> DescriptionTable:
    return DescriptionTable.from_lists(sounds=FIX_SOUND_LISTS, subjects=FIX_SUBJECTS, verbs=FIX_VERBS, music_moods=FIX_MUSIC_MOODS, voice_moods=FIX_VOICE_MOODS, languages=FIX_LANGUAGES, channels=FIX_CHANNELS)


@functools.cache
def load_parser() -> spacy.language.Language:
    return spacy.load(NLP_MODEL, exclude=["ner"])

# ------------------------------------------------------------------------------
# Execution
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = create_parser()
    subs = sorted(set(find_subtitles(parser, formats=(SubtitleFormat.TEXT,), languages=(LANGUAGE_SOURCE,))), key=lambda sub: (sub.title_name, str(sub.movie_dir), sub.video_name, sub.name))
    arguments = parser.parse_args()
    library = Library.create()

    with Batch() as batch, cancel_on_interrupt(ProcessPoolExecutor(max_workers=NAS_WORKERS)) as executor:
        # read and parse every sub, NAS_WORKERS at a time across movies
        observations_read: dict[SubtitleFile, Future[Observations]] = {sub: executor.submit(Observations.from_file, sub) for sub in subs}

        # add up every sub of a movie as its observations arrive, by video and by title
        subs_walked = batch.walk_ahead(subs, observations_read)
        for _, title_walked in itertools.groupby(subs_walked, key=lambda walked: walked[0].title_name):
            title = Observations.create()
            for _, video_walked in itertools.groupby(title_walked, key=lambda walked: (walked[0].movie_dir, walked[0].video_name)):
                video = Observations.create()
                for sub, _ in video_walked:
                    with batch.attempt(sub.path):
                        track = observations_read.pop(sub).result()
                        Action.COUNT.log(f"{sub.name} · {track.measures[Measure.ENTRIES]} entries")
                        video = video.with_track(track)
                title = title.with_video(video)
            library.add_title(title)

        arguments.output.mkdir(parents=True, exist_ok=True)
        for name, report in library.to_reports(titles_min=arguments.titles_min, rows=arguments.rows).items():
            write_text_replacing(arguments.output / name, report, newline="\n")
        log_heading(f"{library.describe()}, written to {arguments.output}", Color.GREEN)
