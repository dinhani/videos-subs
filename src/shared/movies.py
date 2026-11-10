import re
from dataclasses import dataclass
from xml.etree import ElementTree

from shared.files import SubtitleFile
from shared.languages import WORD, parse_words

NFO_CHARACTERS_MAX = 20
NFO_SHOW = "tvshow.nfo"
NFO_CREDIT_NOTES = re.compile(r"\s*\((?:voice|archive footage|archive sound)(?:,\s*(?:voice|archive footage|archive sound))*\)", re.IGNORECASE)
NFO_TITLES = frozenset({"Agent", "Captain", "Chief", "Corporal", "Detective", "Doctor", "Director", "General", "Lieutenant", "Major", "Officer", "Private", "Senator", "Sergeant", "Sheriff", "Young", "Old"})


@dataclass(frozen=True)
class Character:
    role: str
    actor: str

    def is_named_in(self, names: set[str]) -> bool:
        return any(word.upper() in names for word in WORD.findall(self.role) if word[0].isupper() and len(word) >= 3 and word not in NFO_TITLES and not word.endswith(("'s", "’s")))


@dataclass(frozen=True)
class MovieInfo:
    plots: tuple[tuple[str, str], ...]
    characters: tuple[Character, ...]

    @classmethod
    def find(cls, sub: SubtitleFile) -> "MovieInfo | None":
        if not sub.nfo.exists():
            return None
        root = ElementTree.parse(sub.nfo).getroot()
        show_nfo = sub.movie_dir.parent / NFO_SHOW
        show = ElementTree.parse(show_nfo).getroot() if root.tag == "episodedetails" and show_nfo.exists() else None

        # an episode takes the plot of its season, or of its series when the season has none
        plots = [("Plot", root.findtext("plot"))]
        if show is not None:
            season = root.findtext("season", "")
            season_plot = next((element.text for element in show.iter("seasonplot") if element.get("number") == season and element.text and element.text.strip()), None)
            plots = [(f"Season {season}", season_plot) if season_plot else ("Series", show.findtext("plot")), ("Episode", root.findtext("plot"))]

        # credited roles without credit notes, the first billed of each role
        characters: dict[str, Character] = {}
        for actor in root.findall("actor") or (show.findall("actor") if show is not None else []):
            role = " ".join(NFO_CREDIT_NOTES.sub("", actor.findtext("role", "")).split())
            name = " ".join(actor.findtext("name", "").split())
            if role and name and "uncredited" not in role.lower():
                characters.setdefault(role, Character(role=role, actor=name))
        return cls(plots=tuple((label, " ".join(text.split())) for label, text in plots if text and text.strip()), characters=tuple(characters.values()))

    def to_prompt(self, english: str) -> str:
        names = {word.text.upper() for word in parse_words(english) if word.name and not word.text.isupper()}
        characters = [character for character in self.characters if character.is_named_in(names)][:NFO_CHARACTERS_MAX]
        lines = [f"{label}: {text}" for label, text in self.plots]
        if characters:
            lines.append("Characters named in the subtitles, each with the actor who plays them:")
            lines += [f"- {character.role} ({character.actor})" for character in characters]
        return "\n".join(lines)
