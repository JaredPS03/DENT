"""Deterministic urgency filter (bot rule 4). It runs before the AI and before the privacy notice,
so a patient in pain gets help even if no AI answers and they never accepted anything.

The clinic keeps its list of signals (clinics.urgency_signals). Each default signal has rules
written for how patients actually write: "me duele" close to "mucho", "cara" close to "hinchada".
A signal the clinic adds is matched as written. Both the message and the rules go through the
same normalization, which forgives accents, capitals, repeated letters and common spelling mixes
(h, z/s, c/s, v/b), so the rules are written in plain Spanish.

The filter errs on the side of firing: a false alarm costs a message to the team, a missed one
costs a patient. The AI is the second check for what the rules do not cover.
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

DEFAULT_SIGNALS = (
    "dolor fuerte",
    "sangrado que no para",
    "hinchazón de cara o cuello",
    "fiebre con dolor dental",
    "golpe o diente roto",
    "dificultad para tragar o respirar",
)

Phrase = tuple[str, ...]


def normalize(text: str) -> str:
    """Lowercase words without accents or punctuation, with spelling mixes folded together."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = re.sub(r"(?<!c)h", "", text)  # silent h: "inchada" is "hinchada"
    text = text.replace("z", "s")
    text = re.sub(r"c(?=[ei])", "s", text)
    text = text.replace("v", "b")
    text = re.sub(r"(.)\1+", r"\1", text)  # "muchooo" is "mucho"; also folds ll and rr
    return " ".join(text.split())


def _phrases(*texts: str) -> tuple[Phrase, ...]:
    return tuple(tuple(normalize(text).split()) for text in texts)


@dataclass(frozen=True, slots=True)
class _Rule:
    """`first` alone, or `first` within `gap` words of `second` (in that order, or either)."""

    first: tuple[Phrase, ...]
    second: tuple[Phrase, ...] = ()
    gap: int = 3
    either_order: bool = False
    # Not when `first` comes right after one of these: "si me duele mucho" is a what-if.
    unless_after: tuple[Phrase, ...] = ()
    # Not when the message mentions one of these: "un diente de leche" is not an emergency.
    unless_anywhere: tuple[Phrase, ...] = ()


_INTENSE = _phrases(
    "mucho",
    "muchisimo",
    "muxo",
    "demasiado",
    "bastante",
    "horrible",
    "horrores",
    "terrible",
    "fuerte",
    "fuertisimo",
    "insoportable",
    "intenso",
    "un chingo",
    "un buen",
    "bien feo",
    "bien fuerte",
    "a morir",
    "como nunca",
    "cada vez mas",
    "espantoso",
    "tremendo",
)
_HYPOTHETICAL = _phrases("si", "por si", "va a", "van a", "aunque", "cuando")
_TEETH = _phrases("diente", "dientes", "muela", "muelas", "colmillo", "colmillos", "incisivo")

_RULES_BY_SIGNAL: dict[str, tuple[_Rule, ...]] = {
    "dolor fuerte": (
        _Rule(
            first=_phrases(
                "me duele",
                "me duelen",
                "me esta doliendo",
                "me estan doliendo",
                "tengo dolor",
                "tengo un dolor",
                "traigo dolor",
                "traigo un dolor",
                "siento dolor",
                "siento un dolor",
            ),
            second=_INTENSE,
            gap=3,
            unless_after=_HYPOTHETICAL,
        ),
        _Rule(
            first=_phrases("dolor"),
            second=_phrases(
                "fuerte",
                "fuertisimo",
                "intenso",
                "horrible",
                "terrible",
                "insoportable",
                "espantoso",
                "tremendo",
            ),
            gap=1,
            unless_after=_HYPOTHETICAL,
        ),
        _Rule(
            first=_phrases(
                "ya no aguanto",
                "no aguanto el dolor",
                "no aguanto la muela",
                "no aguanto de dolor",
                "no soporto el dolor",
                "me muero de dolor",
                "me estoy muriendo de dolor",
                "me esta matando",
                "dolor insoportable",
                "no me deja dormir",
                "no puedo dormir del dolor",
                "no pude dormir del dolor",
                "llorando de dolor",
            )
        ),
    ),
    "sangrado que no para": (
        _Rule(
            first=_phrases("sangrando", "sangra", "sangrado", "sangre", "emoragia"),
            second=_phrases(
                "no para",
                "no se para",
                "no deja",
                "no se detiene",
                "no se quita",
                "no se corta",
                "sin parar",
                "a chorros",
                "chorro",
                "horas",
                "toda la noche",
                "desde ayer",
                "desde anoche",
            ),
            gap=5,
            either_order=True,
        ),
        _Rule(
            first=_phrases(
                "estoy sangrando",
                "esta sangrando",
                "sigo sangrando",
                "sigue sangrando",
                "no deja de sangrar",
                "no para de sangrar",
                "no paro de sangrar",
                "sangra y sangra",
                "emoragia",
                "mucha sangre",
                "sangrando mucho",
                "sangra mucho",
                "sangrando demasiado",
                "sangrando bastante",
            )
        ),
    ),
    "hinchazón de cara o cuello": (
        _Rule(
            first=_phrases(
                "hinchada",
                "hinchado",
                "hinchazon",
                "hinche",
                "hincho",
                "hinchando",
                "inflamada",
                "inflamado",
                "inflamacion",
                "inflamo",
                "inflamando",
                "bola",
                "bolita",
                "abultada",
                "abultado",
                "deformada",
            ),
            second=_phrases(
                "cara",
                "cachete",
                "cachetes",
                "mejilla",
                "cuello",
                "ojo",
                "quijada",
                "mandibula",
                "garganta",
                "labio",
                "pomulo",
                "barbilla",
            ),
            gap=4,
            either_order=True,
        ),
        _Rule(first=_phrases("postemilla", "absceso", "flemon")),
    ),
    "fiebre con dolor dental": (
        _Rule(
            first=_phrases("fiebre", "calentura", "temperatura"),
            second=_phrases(
                "duele",
                "duelen",
                "dolor",
                "muela",
                "muelas",
                "diente",
                "dientes",
                "encia",
                "encias",
                "boca",
                "hinchada",
                "hinchado",
                "infeccion",
            ),
            gap=12,
            either_order=True,
        ),
    ),
    "golpe o diente roto": (
        _Rule(
            first=_phrases(
                "golpe",
                "golpee",
                "golpeo",
                "me pegue",
                "me pegaron",
                "le pegaron",
                "me cai",
                "caida",
                "accidente",
                "choque",
                "chocamos",
                "balonazo",
                "pelotazo",
                "patada",
                "codazo",
            ),
            second=(*_TEETH, *_phrases("boca", "labio", "cara", "mandibula", "quijada", "encias")),
            gap=6,
            either_order=True,
        ),
        _Rule(
            first=_phrases(
                "rompio",
                "rompi",
                "quebro",
                "quebre",
                "partio",
                "parti",
                "despostillo",
                "despostille",
                "estrello",
                "tumbaron",
                "tumbe",
                "salio",
                "zafo",
                "cayo",
                "roto",
                "rota",
                "quebrado",
                "quebrada",
                "partido",
                "partida",
                "astillado",
            ),
            second=_TEETH,
            gap=3,
            either_order=True,
            unless_anywhere=_phrases(
                "de leche",
                "resina",
                "calza",
                "corona",
                "bracket",
                "brackets",
                "protesis",
                "placa",
                "puente",
                "funda",
                "amalgama",
            ),
        ),
    ),
    "dificultad para tragar o respirar": (
        _Rule(
            first=_phrases(
                "no puedo",
                "me cuesta",
                "dificultad para",
                "trabajo para",
                "casi no puedo",
                "apenas puedo",
                "no me deja",
            ),
            second=_phrases("tragar", "respirar", "pasar saliva", "pasar la saliva"),
            gap=3,
        ),
        _Rule(
            first=_phrases(
                "me ahogo",
                "me estoy ahogando",
                "no me entra aire",
                "no me entra el aire",
                "falta de aire",
                "me falta el aire",
                "me falta aire",
            )
        ),
    ),
}


# Looked up by the normalized signal, so "Hinchazon de cara o cuello" still finds its rules.
_RULES = {normalize(signal): rules for signal, rules in _RULES_BY_SIGNAL.items()}
assert set(_RULES) == {normalize(signal) for signal in DEFAULT_SIGNALS}  # noqa: S101 - a typo here disables a signal


def detect_urgency(text: str, signals: Iterable[str]) -> str | None:
    """The first of the clinic's signals that the text matches, as the clinic wrote it."""
    tokens = tuple(normalize(text).split())
    if not tokens:
        return None
    for signal in signals:
        key = normalize(signal)
        if not key:
            continue
        rules = (*_RULES.get(key, ()), _Rule(first=(tuple(key.split()),)))
        if any(_matches(rule, tokens) for rule in rules):
            return signal
    return None


def _matches(rule: _Rule, tokens: tuple[str, ...]) -> bool:
    if any(_occurrences(tokens, phrase) for phrase in rule.unless_anywhere):
        return False
    firsts = [
        span
        for phrase in rule.first
        for span in _occurrences(tokens, phrase)
        if not _right_after(tokens, span[0], rule.unless_after)
    ]
    if not rule.second:
        return bool(firsts)
    seconds = [span for phrase in rule.second for span in _occurrences(tokens, phrase)]
    for first_start, first_end in firsts:
        for second_start, second_end in seconds:
            if 0 <= second_start - first_end <= rule.gap:
                return True
            if rule.either_order and 0 <= first_start - second_end <= rule.gap:
                return True
    return False


def _occurrences(tokens: Sequence[str], phrase: Phrase) -> list[tuple[int, int]]:
    size = len(phrase)
    return [
        (start, start + size)
        for start in range(len(tokens) - size + 1)
        if tuple(tokens[start : start + size]) == phrase
    ]


def _right_after(tokens: Sequence[str], start: int, phrases: Iterable[Phrase]) -> bool:
    """Whether one of `phrases` ends at `start`, or one word before it ("si de veras me duele")."""
    for phrase in phrases:
        size = len(phrase)
        for skip in (0, 1):
            begin = start - skip - size
            if begin >= 0 and tuple(tokens[begin : begin + size]) == phrase:
                return True
    return False
