import functools
import re
from dataclasses import dataclass, field
from pathlib import Path

import spacy

from shared.data_lists import CHECK_WORDS, DataList
from shared.files import SubtitleFile, parse_srt
from shared.subtitles import FORMATTING_TAGS, parse_plain_text

DESCRIPTION_SIMPLE = re.compile(r"\[([^\[\]()<>{}]+)\]|\(([^\[\]()<>{}]+)\)")
DESCRIPTION_SUBJECT_WORDS_MAX = 2
DESCRIPTION_PUNCTUATION = re.compile(r"[\[\]()\-–—]")
DESCRIPTION_ARTICLE = re.compile(r"^(?:o|a|os|as)\s+")

NLP_MODEL = "en_core_web_sm"
NLP_NAME_LABELS = frozenset({"PERSON", "ORG"})
NLP_BATCH_SIZE = 256
SPEAKER_LABEL = re.compile(r"^\s*-?\s*([A-Z][A-Z0-9'.\- ]*[A-Z0-9.]):", re.MULTILINE)


def normalize_description(text: str) -> str:
    return " ".join(DESCRIPTION_PUNCTUATION.sub(" ", FORMATTING_TAGS.sub("", text)).lower().split())


def match_case(text: str, source: str) -> str:
    if source.isupper():
        return text.upper()
    if source[:1].isupper():
        return text[:1].upper() + text[1:]
    return text


@dataclass(frozen=True)
class DescriptionTranslation:
    source: str
    text: str
    name: str
    forms: tuple[frozenset[str], ...]

    def is_confirmed_by(self, translation: str, movie_names: "MovieNames") -> bool:
        if not self.name:
            return True
        words = normalize_description(translation)
        name = normalize_description(self.name)
        return words != normalize_description(self.source) and f" {name} " in f" {words} " and all(movie_names.is_name(word) for word in name.split())

    def is_already_in(self, translation: str) -> bool:
        parts = [DESCRIPTION_ARTICLE.sub("", part.strip()) for part in normalize_description(translation).split(",")]
        return len(parts) == len(self.forms) and all(part in forms for part, forms in zip(parts, self.forms))


@dataclass(frozen=True)
class DescriptionTable:
    sounds: dict[str, tuple[str, ...]]
    subjects: dict[str, str]

    @classmethod
    def from_lists(cls, sounds: DataList, subjects: DataList) -> "DescriptionTable":
        return cls(sounds={key: tuple(form.strip() for form in value.split("|")) for key, value in sounds.parse()}, subjects=dict(subjects.parse()))

    def find_translation(self, description: str) -> DescriptionTranslation | None:
        words = description.split()
        description = " ".join(words)
        key = " ".join(words).lower()
        if key in self.sounds:
            return DescriptionTranslation(source=description, text=match_case(self.sounds[key][0], description), name="", forms=(self.to_forms(self.sounds[key], ""),))

        # a list such as GRUNTS, GASPS: the first part may have a subject or a name, the others are phrases
        if "," in description:
            first, *others = (part.strip() for part in description.split(","))
            translation = self.find_translation(first)
            others_keys = [" ".join(other.split()).lower() for other in others]
            if translation is None or not all(other_key in self.sounds for other_key in others_keys):
                return None
            texts = [translation.text, *(match_case(self.sounds[other_key][0], other) for other_key, other in zip(others_keys, others))]
            forms = (*translation.forms, *(self.to_forms(self.sounds[other_key], "") for other_key in others_keys))
            return DescriptionTranslation(source=description, text=", ".join(texts), name=translation.name, forms=forms)

        # a subject or a name, then a phrase led by a verb in -ing or -s
        for subject_size in range(1, min(DESCRIPTION_SUBJECT_WORDS_MAX, len(words) - 1) + 1):
            subject, phrase = words[:subject_size], words[subject_size:]
            phrase_key = " ".join(phrase).lower()
            verb = phrase[0].lower()
            if phrase_key not in self.sounds or not verb.endswith(("ing", "s")):
                continue
            subject_key = " ".join(subject).lower()
            if subject_key in self.subjects:
                subject_portuguese = self.subjects[subject_key]
                if not verb.endswith("ing") and subject_portuguese.endswith("s"):
                    continue
                text = match_case(f"{subject_portuguese} {self.sounds[phrase_key][0]}", description)
                return DescriptionTranslation(source=description, text=text, name="", forms=(self.to_forms(self.sounds[phrase_key], subject_portuguese),))
            if any(word.lower().endswith(("'s", "’s")) or word.lower() in self.subjects or word.lower() in self.sounds or (not description.isupper() and word[0].islower()) for word in subject):
                continue
            name = " ".join(subject)
            phrase_portuguese = self.sounds[phrase_key][0].upper() if description.isupper() else self.sounds[phrase_key][0]
            return DescriptionTranslation(source=description, text=f"{name} {phrase_portuguese}", name=name, forms=(self.to_forms(self.sounds[phrase_key], name),))
        return None

    def to_forms(self, phrases: tuple[str, ...], subject: str) -> frozenset[str]:
        return frozenset(DESCRIPTION_ARTICLE.sub("", normalize_description(f"{subject} {phrase}")) for phrase in phrases)


@dataclass(frozen=True)
class MovieNames:
    names_known: frozenset[str]
    dialogue: tuple[str, ...]
    names_checked: dict[str, bool] = field(default_factory=dict, compare=False)
    entities_by_entry: dict[int, frozenset[str]] = field(default_factory=dict, compare=False)

    @classmethod
    @functools.cache
    def from_subs_folder(cls, subs_folder: Path) -> "MovieNames":
        # the names of the movie are the words of the speaker labels and of check-words, and the words that NER finds as names in the dialogue of the entries that are not in capitals only, since NER reads names from their case and reads the words of a description as names
        texts = [parse_plain_text(entry) for file in sorted(subs_folder.glob("*.srt")) if SubtitleFile.from_path(file).is_english() for entry in parse_srt(file)]
        labels = {word for text in texts for label in SPEAKER_LABEL.finditer(text) for word in normalize_description(label[1]).split()}
        words_kept = {word for (line,) in CHECK_WORDS.parse() for word in normalize_description(line).split()}
        dialogue = tuple(DESCRIPTION_SIMPLE.sub(" ", text).replace("\n", " ") for text in texts if text != text.upper())
        return cls(names_known=frozenset(labels | words_kept), dialogue=dialogue)

    def is_name(self, word: str) -> bool:
        if word in self.names_known:
            return True
        if word in self.names_checked:
            return self.names_checked[word]

        # NER runs only on the entries whose text has the word, each entry once, since it reads every entry on its own
        entries = [n for n, text in enumerate(self.dialogue) if word in text.lower()]
        entries_pending = [n for n in entries if n not in self.entities_by_entry]
        documents = load_nlp().pipe((self.dialogue[n] for n in entries_pending), batch_size=NLP_BATCH_SIZE)
        for n, document in zip(entries_pending, documents):
            self.entities_by_entry[n] = frozenset(entity_word for entity in document.ents if entity.label_ in NLP_NAME_LABELS for entity_word in normalize_description(entity.text).split())
        self.names_checked[word] = any(word in self.entities_by_entry[n] for n in entries)
        return self.names_checked[word]

    def has_names_only(self, description: str) -> bool:
        words = normalize_description(description).split()
        return bool(words) and all(self.is_name(word) for word in words)


@functools.cache
def load_nlp() -> spacy.language.Language:
    return spacy.load(NLP_MODEL, exclude=["tagger", "parser", "attribute_ruler", "lemmatizer"])
