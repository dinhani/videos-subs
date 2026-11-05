import functools
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import Enum

import simplemma
from lingua import Language, LanguageDetector, LanguageDetectorBuilder
from wordfreq import zipf_frequency

from shared.subtitles import FORMATTING_TAGS, LANGUAGE_SOURCE, LANGUAGE_TRANSLATION, LANGUAGE_UNDETECTED

WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
SENTENCE_START = re.compile(r"(?:^|[.!?…\-–—\"“(♪:\n])[ \t]*$")

LANGUAGE_VOTE_DIFFERENCE_MIN = 1.0
LANGUAGE_VOTE_FREQUENCY_MIN = 3.0

WORDS_PT_PT = re.compile(
    r"\b(autocarro|telemóvel|ecrã|chávena|gaj[oa]|fixe|"
    r"pequeno-almoço|casa de banho|betão|autoclismo|sandes|boleia|talho|agrafador|"
    r"est(?:ou|ás|á|amos|ão|ava|avas|ávamos|avam) a [a-záàâãéêíóôõúç]+r)\b",
    re.IGNORECASE,
)
MARKERS_PT_PT = re.compile(
    r"\b(tu|teus?|tuas?|contigo|vós|voss[oa]s?|"
    r"tens|queres|podes|sabes|vais|és|estás|fazes|dizes|vês|"
    r"factos?|actos?|óptim[oa]s?|acç(?:ão|ões)|direcç(?:ão|ões)|connosco|registo|bebé|génio|fenómeno|económic[oa]s?)\b",
    re.IGNORECASE,
)
MARKERS_PT_BR = re.compile(
    r"\b(vocês?|conosco|registro|bebê|gênio|fenômeno|econômic[oa]s?|"
    r"est(?:ou|ás|á|amos|ão|ava|avas|ávamos|avam|ive|eve|ivemos|iveram) [a-záàâãéêíóôõúç]+ndo)\b",
    re.IGNORECASE,
)
MARKERS_PT_PT_MIN = 3


@functools.cache
def get_language_detector() -> LanguageDetector:
    return LanguageDetectorBuilder.from_all_languages().build()


class SecondLanguage(Enum):
    EUROPEAN_PORTUGUESE = ("por", "European Portuguese", "pt-PT")
    SPANISH = ("spa", "Spanish", "es")
    FRENCH = ("fra", "French", "fr")
    ITALIAN = ("ita", "Italian", "it")

    def __init__(self, code: str, label: str, tag: str) -> None:
        self.code = code
        self.label = label
        self.tag = tag


LANGUAGES_OCR = (LANGUAGE_SOURCE, *(language.code for language in SecondLanguage))


class TextLanguage(Enum):
    ENGLISH = (LANGUAGE_SOURCE, "en")
    PORTUGUESE_BRAZIL = (LANGUAGE_TRANSLATION, "pt")
    PORTUGUESE_PORTUGAL = ("por", "pt")

    def __init__(self, code: str, wordfreq_code: str) -> None:
        self.code = code
        self.wordfreq_code = wordfreq_code

    @functools.cache
    def measure_frequency(self, word: str) -> float:
        word = word.lower()
        lemma = simplemma.lemmatize(word, lang=self.wordfreq_code)
        return max(zipf_frequency(word, self.wordfreq_code), zipf_frequency(lemma, self.wordfreq_code))

    def outweighs(self, other: "TextLanguage", words: list["Word"], votes_min: int) -> bool:
        votes = Counter(
            difference > 0
            for word in {word.key: word for word in words}.values()
            if not word.name
            and max(self.measure_frequency(word.text), other.measure_frequency(word.text)) >= LANGUAGE_VOTE_FREQUENCY_MIN
            and abs(difference := self.measure_frequency(word.text) - other.measure_frequency(word.text)) >= LANGUAGE_VOTE_DIFFERENCE_MIN
        )
        return votes[True] >= votes_min and votes[True] > 2 * votes[False]


def parse_words(text: str) -> list["Word"]:
    return [Word(text=match[0], name=match[0][0].isupper() and SENTENCE_START.search(text, 0, match.start()) is None) for match in WORD.finditer(text)]


@dataclass(frozen=True)
class SubtitleLanguage:
    code: str
    name: str

    @classmethod
    def from_text(cls, text: str) -> "SubtitleLanguage":
        text = FORMATTING_TAGS.sub(" ", text)
        language = get_language_detector().detect_language_of(text)
        if language is None:
            return cls(code=LANGUAGE_UNDETECTED, name="unknown")
        if language != Language.PORTUGUESE:
            return cls(code=language.iso_code_639_3.name.lower(), name=language.name.lower())

        markers_portugal = len(WORDS_PT_PT.findall(text)) + len(MARKERS_PT_PT.findall(text))
        markers_brazil = len(MARKERS_PT_BR.findall(text))
        if markers_portugal >= MARKERS_PT_PT_MIN and markers_portugal > markers_brazil:
            return cls(code="por", name="portuguese (portugal)")
        return cls(code=LANGUAGE_TRANSLATION, name="portuguese (brazil)")


@dataclass(frozen=True)
class Word:
    text: str
    name: bool

    @property
    def key(self) -> str:
        return "".join(character for character in unicodedata.normalize("NFKD", self.text.lower().replace("’", "'")) if not unicodedata.combining(character))
