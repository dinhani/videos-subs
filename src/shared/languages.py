import functools
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import simplemma
from lingua import Language, LanguageDetectorBuilder
from wordfreq import zipf_frequency

from shared.files import find_language_code
from shared.subtitles import FORMATTING_TAGS

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

language_detector = LanguageDetectorBuilder.from_all_languages().build()


class TextLanguage(Enum):
    ENGLISH = ("eng", "en")
    PORTUGUESE_BRAZIL = ("pob", "pt")
    PORTUGUESE_PORTUGAL = ("por", "pt")

    def __init__(self, code: str, wordfreq_code: str) -> None:
        self.code = code
        self.wordfreq_code = wordfreq_code

    @classmethod
    def from_file(cls, file: Path) -> "TextLanguage":
        code = find_language_code(file)
        language = next((language for language in cls if language.code == code), None)
        if language is None:
            raise ValueError(f"language code {code or 'missing'} is not one of {', '.join(language.code for language in cls)}")
        return language

    @property
    def counterpart(self) -> "TextLanguage":
        return TextLanguage.PORTUGUESE_BRAZIL if self == TextLanguage.ENGLISH else TextLanguage.ENGLISH

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
        language = language_detector.detect_language_of(text)
        if language is None:
            return cls(code="und", name="unknown")
        if language != Language.PORTUGUESE:
            return cls(code=language.iso_code_639_3.name.lower(), name=language.name.lower())

        markers_portugal = len(WORDS_PT_PT.findall(text)) + len(MARKERS_PT_PT.findall(text))
        markers_brazil = len(MARKERS_PT_BR.findall(text))
        if markers_portugal >= MARKERS_PT_PT_MIN and markers_portugal > markers_brazil:
            return cls(code="por", name="portuguese (portugal)")
        return cls(code="pob", name="portuguese (brazil)")


@dataclass(frozen=True)
class Word:
    text: str
    name: bool

    @property
    def key(self) -> str:
        return "".join(character for character in unicodedata.normalize("NFKD", self.text.lower().replace("’", "'")) if not unicodedata.combining(character))
