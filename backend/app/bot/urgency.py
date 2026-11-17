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
    text = text.replace("ll", "y")  # "se me callo" is "se me cayo"
    text = re.sub(r"(.)\1+", r"\1", text)  # "muchooo" is "mucho"; also folds rr
    return " ".join(text.split())


def _phrases(*texts: str) -> tuple[Phrase, ...]:
    """A word ending in * matches by its stem: "hinch*" is "hinchada", "hincharon"..."""
    phrases = []
    for text in texts:
        words = []
        for word in text.split():
            stem = normalize(word)
            words.append(stem + "*" if word.endswith("*") else stem)
        phrases.append(tuple(words))
    return tuple(phrases)


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
    # Not when one of these is within `near` words of `first`: "se cayó la resina" is not a blow,
    # but "se le cayó un diente, trae brackets" is.
    unless_near: tuple[Phrase, ...] = ()
    near: int = 2
    # Not when one of these starts right after `second`: "¿le duele mucho la extracción?" asks
    # about a procedure, "le duele mucho la muela" is a symptom.
    unless_then: tuple[Phrase, ...] = ()
    # Only when the message also mentions one of these: a child's tooth that came out is an
    # emergency after a fall, not on its own.
    only_with: tuple[Phrase, ...] = ()


_INTENSE = _phrases(
    "mucho", "muchisimo", "muxo", "demasiado", "bastante", "horrible", "horrores", "terrible", "fuerte",
    "fuertisimo", "insoportable", "intenso", "un chingo", "un buen", "un monton", "un chorro", "un resto",
    "bien feo", "muy feo", "bien gacho", "bien fuerte", "a morir", "a madres", "canon", "como nunca",
    "cada vez mas", "espantoso", "tremendo", "no lo aguanto",
)  # fmt: skip
_HYPOTHETICAL = _phrases("si", "por si", "va a", "van a", "aunque", "cuando")
_TEETH = _phrases("diente", "dientes", "muela", "muelas", "colmillo", "colmillos", "incisivo")
_FACE_AND_NECK = _phrases(
    "cara", "cachete*", "mejilla*", "cuello", "quijada", "mandibula", "garganta", "pomulo*", "barbilla",
)  # fmt: skip
# A "bolita" on the eyelid or the lip is common and not an emergency; swollen lips or tongue are.
_FACE = (*_FACE_AND_NECK, *_phrases("ojo", "ojos", "parpado*", "labio*", "lengua", "debajo de la lengua"))
_SOFT_PLACES = _phrases("boca", "labio*", "cara", "mandibula", "quijada", "encia*")
_HIT_PLACES = (*_TEETH, *_SOFT_PLACES)
_PROSTHESES = _phrases(
    "resina", "calza", "corona", "bracket", "brackets", "protesis", "placa", "puente", "funda", "amalgama",
)  # fmt: skip
# The prosthesis as what fell or got stuck, right next to the verb: "se cayó la resina".
_THE_PROSTHESIS = _phrases(
    "la resina", "la calza", "la corona", "el bracket", "los brackets", "la protesis", "la placa",
    "el puente", "la funda", "la amalgama",
)  # fmt: skip
# Questions about a procedure, not a symptom: "¿le duele mucho la extracción?".
_PROCEDURES = _phrases(
    "la extraccion", "la limpiesa", "la endodonsia", "la anestesia", "la inyeccion", "el piquete",
    "el blanqueamiento", "los brackets", "la ortodonsia", "la curasion", "el tratamiento", "la sirugia",
    "el implante", "la resina", "sacar*",
)  # fmt: skip
# Someone in general, not a patient: "¿a los niños les duele mucho?".
_ANYONE = _phrases(
    "a los ninos", "a las ninas", "a un nino", "a una nina", "a los bebes", "a la gente", "a las personas",
    "a los pacientes", "a todos",
)  # fmt: skip
_BLOWS = _phrases(
    "golpe*", "me pegue", "me pegaron", "le pegaron", "se pego", "me cai", "se cayo", "caida", "accidente",
    "choque", "chocamos", "balonaso", "pelotaso", "patada", "codaso", "tropes*", "se resbalo",
)  # fmt: skip

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
                "me sigue doliendo",
                "me siguen doliendo",
                "sigue doliendo",
            ),
            second=_INTENSE,
            gap=3,
            unless_after=_HYPOTHETICAL,
        ),
        # Someone else in pain, but not a question about a procedure or about people in general.
        _Rule(
            first=_phrases(
                "le duele",
                "le duelen",
                "le esta doliendo",
                "le estan doliendo",
                "le sigue doliendo",
                "tiene dolor",
                "tiene un dolor",
                "trae dolor",
                "trae un dolor",
            ),
            second=_INTENSE,
            gap=3,
            # Only right next to the pain: "a mi hijo le duele mucho, ¿atienden a los niños?" fires.
            unless_after=(*_HYPOTHETICAL, *_ANYONE),
            unless_then=(*_PROCEDURES, *_ANYONE),
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
            gap=3,
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
                "no lo aguanto",
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
                "no le para de sangrar",
                "no me para de sangrar",
                "no le deja de sangrar",
                "no se le para la sangre",
                "no se me para la sangre",
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
        _Rule(first=_phrases("hinch*"), second=_FACE, gap=4, either_order=True),
        # "La garganta inflamada" is usually a cold; a throat that swells is not.
        _Rule(
            first=_phrases("inflam*"),
            second=tuple(place for place in _FACE if place != ("garganta",)),
            gap=4,
            either_order=True,
        ),
        _Rule(
            first=_phrases("bola", "bolita", "abultad*", "deformad*"),
            second=_FACE_AND_NECK,
            gap=4,
            either_order=True,
        ),
        _Rule(first=_phrases("postemilla", "absceso", "flemon")),
    ),
    "fiebre con dolor dental": (
        _Rule(
            first=_phrases(
                "fiebre",
                "calentura",
                "tengo temperatura",
                "tiene temperatura",
                "traigo temperatura",
                "me subio la temperatura",
                "le subio la temperatura",
            ),
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
                "hinch*",
                "infeccion",
            ),
            gap=12,
            either_order=True,
        ),
    ),
    "golpe o diente roto": (
        _Rule(
            first=_phrases(
                "golpe*",
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
            second=_HIT_PLACES,
            gap=6,
            either_order=True,
        ),
        # A fall or a hit on the mouth or the face is a blow, braces or not, unless what fell is
        # the prosthesis itself ("se cayó la resina y me raspa la encía").
        _Rule(
            first=_phrases("se cayo", "se pego"),
            second=_SOFT_PLACES,
            gap=6,
            either_order=True,
            unless_near=_THE_PROSTHESIS,
            near=0,
        ),
        _Rule(
            first=_phrases("me lastime", "se lastimo"),
            second=(*_TEETH, *_phrases("boca", "labio*", "cara", "mandibula", "quijada")),
            gap=6,
            either_order=True,
            unless_near=_PROSTHESES,
            near=4,
        ),
        # Verbs with two meanings: "se pegó la resina" or "me lastimé con el bracket" are not
        # blows. A tooth that "se cayó" is the rule of a tooth that came out, below.
        _Rule(
            first=_phrases("se pego"),
            second=_TEETH,
            gap=6,
            either_order=True,
            unless_near=_PROSTHESES,
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
            unless_anywhere=_phrases("de leche"),
            unless_near=_PROSTHESES,
        ),
        # A tooth that came out: "se me salió" is a tooth lost, "me salió" is one growing in.
        _Rule(
            first=_phrases("se me salio", "se me cayo", "se me zafo"),
            second=_TEETH,
            gap=3,
            either_order=True,
            unless_anywhere=_phrases("de leche", "juicio", "primer diente", "primera muela"),
            unless_near=_PROSTHESES,
        ),
        # Someone else's tooth (usually a child's) is an emergency only after a blow (Jared, P14).
        _Rule(
            first=_phrases("se le salio", "se salio", "se le cayo", "se le zafo", "se zafo"),
            second=_TEETH,
            gap=3,
            either_order=True,
            unless_anywhere=_phrases("de leche", "juicio", "primer diente", "primera muela"),
            unless_near=_PROSTHESES,
            only_with=_BLOWS,
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
                "no puede",
                "le cuesta",
                "casi no puede",
                "apenas puede",
                "no le deja",
            ),
            second=_phrases("tragar", "respirar", "pasar saliva", "pasar la saliva", "pasar ni el agua"),
            gap=3,
        ),
        _Rule(
            first=_phrases(
                "me ahogo",
                "me estoy ahogando",
                "se ahoga",
                "no me entra aire",
                "no me entra el aire",
                "falta de aire",
                "me falta el aire",
                "me falta aire",
                "se me cierra la garganta",
                "se me esta cerrando la garganta",
                "se le cierra la garganta",
                "me asfixio",
                "se asfixia",
                "no me pasa la saliva",
                "no puedo pasar ni el agua",
                "le falta el aire",
                "le falta aire",
                "no le entra el aire",
                "no le entra aire",
                "se esta ahogando",
                "se le esta cerrando la garganta",
                "no le pasa la saliva",
                "tiene la garganta cerrada",
                "tengo la garganta cerrada",
            )
        ),
    ),
}


# Looked up by the normalized signal, so "Hinchazon de cara o cuello" still finds its rules.
_RULES = {normalize(signal): rules for signal, rules in _RULES_BY_SIGNAL.items()}
assert set(_RULES) == {normalize(signal) for signal in DEFAULT_SIGNALS}  # noqa: S101 - a typo here disables a signal


BREATHING_SIGNAL = DEFAULT_SIGNALS[5]


def is_breathing_signal(signal: str) -> bool:
    """Trouble breathing or swallowing: the one signal that always alerts again."""
    return normalize(signal) == normalize(BREATHING_SIGNAL)


def active_signals(signals: Iterable[str]) -> tuple[str, ...]:
    """The clinic's signals with trouble breathing or swallowing first: a clinic cannot turn it off,
    and when a message matches several, it is the one the team must hear about."""
    signals = tuple(signals)
    breathing = next((signal for signal in signals if is_breathing_signal(signal)), BREATHING_SIGNAL)
    return (breathing, *(signal for signal in signals if not is_breathing_signal(signal)))


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
    if rule.only_with and not any(_occurrences(tokens, phrase) for phrase in rule.only_with):
        return False
    firsts = [
        span
        for phrase in rule.first
        for span in _occurrences(tokens, phrase)
        if not _right_after(tokens, span[0], rule.unless_after)
        and not _near(tokens, span, rule.unless_near, rule.near)
    ]
    if not rule.second:
        return bool(firsts)
    seconds = [span for phrase in rule.second for span in _occurrences(tokens, phrase)]
    for first_start, first_end in firsts:
        for second_start, second_end in seconds:
            if 0 <= second_start - first_end <= rule.gap and not _starts_at(
                tokens, second_end, rule.unless_then
            ):
                return True
            if rule.either_order and 0 <= first_start - second_end <= rule.gap:
                return True
    return False


def _occurrences(tokens: Sequence[str], phrase: Phrase) -> list[tuple[int, int]]:
    size = len(phrase)
    return [
        (start, start + size)
        for start in range(len(tokens) - size + 1)
        if all(_same(token, word) for token, word in zip(tokens[start : start + size], phrase, strict=True))
    ]


def _same(token: str, word: str) -> bool:
    return token.startswith(word[:-1]) if word.endswith("*") else token == word


def _starts_at(tokens: Sequence[str], start: int, phrases: Iterable[Phrase]) -> bool:
    return any(span[0] == start for phrase in phrases for span in _occurrences(tokens, phrase))


def _near(tokens: Sequence[str], span: tuple[int, int], phrases: Iterable[Phrase], near: int) -> bool:
    """Whether one of `phrases` ends at most `near` words before the span or starts at most `near`
    words after it."""
    start, end = span
    return any(
        0 <= start - other_end <= near or 0 <= other_start - end <= near
        for phrase in phrases
        for other_start, other_end in _occurrences(tokens, phrase)
    )


def _right_after(tokens: Sequence[str], start: int, phrases: Iterable[Phrase]) -> bool:
    """Whether one of `phrases` ends at `start`, or one word before it ("si de veras me duele")."""
    for phrase in phrases:
        size = len(phrase)
        for skip in (0, 1):
            begin = start - skip - size
            if begin >= 0 and tuple(tokens[begin : begin + size]) == phrase:
                return True
    return False
