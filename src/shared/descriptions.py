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
DESCRIPTION_ADJECTIVE_SINGULAR = re.compile(r"nte\b")
DESCRIPTION_NUMBERED = re.compile(r"(?P<subject>.+) (?P<number>#?\d+)")
DESCRIPTION_TAIL_WORDS = ("in", "on", "over")
DESCRIPTION_FAR = ("ao longe", "distante", "distantes")
LANGUAGE_PHRASES = (
    ("speaking in ", ("falando em ", "falando ", "fala em ", "fala ", "em ")),
    ("speaks in ", ("fala em ", "fala ", "falando em ", "falando ", "em ")),
    ("speaking ", ("falando ", "falando em ", "fala ", "fala em ", "em ")),
    ("speaks ", ("fala ", "fala em ", "falando ", "falando em ", "em ")),
    ("in ", ("em ",)),
)

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
    texts_by_subject: tuple[tuple[str, str], ...] = ()

    def is_confirmed_by(self, translation: str, movie_names: "MovieNames") -> bool:
        if not self.name:
            return True
        words = normalize_description(translation)
        name = normalize_description(self.name)
        return words != normalize_description(self.source) and f" {name} " in f" {words} " and all(movie_names.is_name(word) for word in name.split())

    def to_text(self, translation: str) -> str:
        # the subject the model wrote, when the table lists it for this subject, so a right subject stays and only the phrase is written
        words = f"{DESCRIPTION_ARTICLE.sub('', normalize_description(translation))} "
        return next((text for subject, text in self.texts_by_subject if words.startswith(f"{normalize_description(subject)} ")), self.text)

    def is_already_in(self, translation: str) -> bool:
        parts = [DESCRIPTION_ARTICLE.sub("", part.strip()) for part in normalize_description(translation).split(",")]
        return len(parts) == len(self.forms) and all(part in forms for part, forms in zip(parts, self.forms))


@dataclass(frozen=True)
class DescriptionTable:
    sounds: dict[str, tuple[str, ...]]
    subjects: dict[str, tuple[str, ...]]
    moods: dict[str, tuple[str, dict[str, tuple[str, ...]]]]
    languages: dict[str, tuple[str, ...]]
    channels: dict[str, tuple[str, ...]]

    @classmethod
    def from_lists(cls, sounds: tuple[DataList, ...], subjects: DataList, music_moods: DataList, voice_moods: DataList, languages: DataList, channels: DataList) -> "DescriptionTable":
        phrases: dict[str, tuple[str, ...]] = {}
        for sound_list in sounds:
            for key, forms in parse_forms(sound_list).items():
                if key in phrases:
                    raise ValueError(f"{sound_list.file.name}: phrase listed in another sound list: {key!r}")
                phrases[key] = forms
        moods = {"music": ("música", parse_forms(music_moods)), "voice": ("voz", parse_forms(voice_moods))}
        return cls(sounds=phrases, subjects=parse_forms(subjects), moods=moods, languages=parse_forms(languages), channels=parse_forms(channels))

    def find_translation(self, description: str) -> DescriptionTranslation | None:
        words = description.split()
        description = " ".join(words)
        key = " ".join(words).lower()
        forms = self.find_forms(key)
        if forms is not None:
            return DescriptionTranslation(source=description, text=match_case(forms[0], description), name="", forms=(self.to_forms(forms, ""),))
        subject_forms = self.find_subject(key)
        if subject_forms is not None:
            return DescriptionTranslation(source=description, text=match_case(subject_forms[0], description), name="", forms=(self.to_forms(subject_forms, ""),))

        # a list such as GRUNTS, GASPS: the first part may have a subject or a name, the others are phrases
        if "," in description:
            first, *others = (part.strip() for part in description.split(","))
            translation = self.find_translation(first)
            others_forms = [self.find_forms(" ".join(other.split()).lower()) for other in others]
            if translation is None or any(other_forms is None for other_forms in others_forms):
                return None
            texts = [translation.text, *(match_case(other_forms[0], other) for other_forms, other in zip(others_forms, others) if other_forms)]
            forms = (*translation.forms, *(self.to_forms(other_forms, "") for other_forms in others_forms if other_forms))
            return DescriptionTranslation(source=description, text=", ".join(texts), name=translation.name, forms=forms)

        # a subject or a name, then a phrase led by a verb in -ing or -s, or by in, on or over
        for subject_size in range(1, min(DESCRIPTION_SUBJECT_WORDS_MAX, len(words) - 1) + 1):
            subject, phrase = words[:subject_size], words[subject_size:]
            verb = phrase[0].lower()
            phrase_forms = self.find_forms(" ".join(phrase).lower())
            if phrase_forms is None or not (verb.endswith(("ing", "s")) or verb in DESCRIPTION_TAIL_WORDS):
                continue
            subject_forms = self.find_subject(" ".join(subject).lower())
            if subject_forms is not None:
                if verb.endswith("s") and subject_forms[0].endswith("s"):
                    continue
                texts_by_subject = tuple((subject_portuguese, match_case(f"{subject_portuguese} {phrase_forms[0]}", description)) for subject_portuguese in subject_forms)
                forms = frozenset().union(*(self.to_forms(phrase_forms, subject_portuguese) for subject_portuguese in subject_forms))
                return DescriptionTranslation(source=description, text=texts_by_subject[0][1], name="", forms=(forms,), texts_by_subject=texts_by_subject)
            if any(word.lower().endswith(("'s", "’s")) or word.lower() in self.subjects or word.lower() in self.sounds or (not description.isupper() and word[0].islower()) for word in subject):
                continue
            name = " ".join(subject)
            phrase_portuguese = phrase_forms[0].upper() if description.isupper() else phrase_forms[0]
            return DescriptionTranslation(source=description, text=f"{name} {phrase_portuguese}", name=name, forms=(self.to_forms(phrase_forms, name),))
        return None

    def find_forms(self, phrase: str) -> tuple[str, ...] | None:
        # a phrase of the lists, or one built from them
        if phrase in self.sounds:
            return self.sounds[phrase]
        if phrase in self.channels:
            return self.channels[phrase]
        words = phrase.split(" ")
        rest = " ".join(words[1:])

        # one mood before music or a voice: GRIM MUSIC PLAYING, IN A DEEP VOICE; two moods are left out, since their order and the "e" between them change in Portuguese
        for n, (mood, noun) in enumerate(zip(words, words[1:])):
            noun_portuguese, moods = self.moods.get(noun, ("", {}))
            if n > 0 and words[n - 1].rstrip(",") in moods:
                return None
            forms = self.find_forms(" ".join(words[:n] + words[n + 1:])) if mood in moods else None
            if forms:
                return tuple(form.replace(noun_portuguese, f"{noun_portuguese} {mood_portuguese}", 1) for mood_portuguese in moods[mood] for form in forms if noun_portuguese in form) or None

        # a language after speaking or in: SPEAKING SWEDISH
        for prefix, prefixes_portuguese in LANGUAGE_PHRASES:
            language = phrase.removeprefix(prefix)
            if phrase.startswith(prefix) and language in self.languages:
                return tuple(f"{prefix_portuguese}{form}" for prefix_portuguese in prefixes_portuguese for form in self.languages[language])

        # a phrase in -ing that goes on, or that comes from far: CONTINUES LAUGHING, DISTANT EXPLOSION; the gerund is written first, and the other forms are left as the model wrote them
        if words[0] == "continues" and (forms := self.find_forms(rest)):
            gerunds = [form for form in forms if form.split()[0].endswith("ndo")]
            return tuple(f"continua {form}" for form in [*gerunds, *(form for form in forms if form not in gerunds)]) if gerunds else None
        if words[0] == "distant" and (forms := self.find_forms(rest)):
            return tuple(f"{form} {far}" for far in DESCRIPTION_FAR for form in forms)

        # a phrase followed by a phrase led by in, on or over: SHOUTING IN GERMAN, LAUGHING ON TV, BELL RINGING IN THE DISTANCE
        for size in range(len(words) - 1, 0, -1):
            if words[size] not in DESCRIPTION_TAIL_WORDS:
                continue
            forms_tail = self.find_forms(" ".join(words[size:]))
            forms = self.find_forms(" ".join(words[:size])) if forms_tail else None
            if forms_tail and forms:
                return tuple(f"{form} {form_tail}" for form in forms for form_tail in forms_tail)
        return None

    def find_subject(self, subject: str) -> tuple[str, ...] | None:
        # a subject of the list, or one with a number: MAN 2
        if subject in self.subjects:
            return self.subjects[subject]
        numbered = DESCRIPTION_NUMBERED.fullmatch(subject)
        if numbered is not None and numbered["subject"] in self.subjects:
            return tuple(f"{form} {numbered['number']}" for form in self.subjects[numbered["subject"]])
        return None

    def to_forms(self, phrases: tuple[str, ...], subject: str) -> frozenset[str]:
        # after a plural subject, an adjective in -nte also agrees with it: ESTUDANTES OFEGANTES
        forms = {DESCRIPTION_ARTICLE.sub("", normalize_description(f"{subject} {phrase}")) for phrase in phrases}
        if subject.endswith("s"):
            forms |= {DESCRIPTION_ADJECTIVE_SINGULAR.sub("ntes", form) for form in forms}
        return frozenset(forms)


def parse_forms(data_list: DataList) -> dict[str, tuple[str, ...]]:
    return {key: tuple(form.strip() for form in value.split("|")) for key, value in data_list.parse()}


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


@functools.cache
def load_nlp() -> spacy.language.Language:
    return spacy.load(NLP_MODEL, exclude=["tagger", "parser", "attribute_ruler", "lemmatizer"])
