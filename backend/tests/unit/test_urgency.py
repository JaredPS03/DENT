"""The deterministic urgency filter: it runs before any AI, so it has to catch the ways patients
really write (typos, no accents, slang, voice-note transcriptions) without firing on questions
about a procedure."""

import pytest

from app.bot.urgency import DEFAULT_SIGNALS, detect_urgency, normalize

PAIN, BLEEDING, SWELLING, FEVER, TRAUMA, BREATHING = DEFAULT_SIGNALS

SHOULD_FIRE = [
    # Strong pain
    ("me duele mucho la muela", PAIN),
    ("Me duele muchísimo", PAIN),
    ("ME DUELE MUCHOOOO", PAIN),
    ("me duele demasiado el diente", PAIN),
    ("tengo un dolor muy fuerte en la muela", PAIN),
    ("traigo un dolor horrible de muela", PAIN),
    ("ya no aguanto el dolor", PAIN),
    ("me esta matando la muela", PAIN),
    ("no puedo dormir del dolor", PAIN),
    ("me duele un chingo", PAIN),
    ("dolor insoportable", PAIN),
    ("me duele bien feo la muela de atras", PAIN),
    ("me duele muxo", PAIN),
    ("dolor fuertisimo en el colmillo", PAIN),
    ("oiga es que me duele muchisimo la muela ya no aguanto", PAIN),
    ("buenas noches, me duelen mucho los dientes de abajo", PAIN),
    ("a mi hijo le duele muchísimo la muela", PAIN),
    ("buenas, dolor de muela muy fuerte", PAIN),
    ("dolor de muela insoportable", PAIN),
    ("me duele un montón", PAIN),
    ("me duele bien gacho", PAIN),
    ("me sigue doliendo muchísimo", PAIN),
    # Bleeding that does not stop
    ("no para de sangrar desde que me sacaron la muela", BLEEDING),
    ("estoy sangrando mucho", BLEEDING),
    ("me sigue saliendo mucha sangre", BLEEDING),
    ("sangra y sangra la encia", BLEEDING),
    ("tengo sangrado desde ayer y no se detiene", BLEEDING),
    ("no le para de sangrar el labio a mi niña", BLEEDING),
    # Swelling of face or neck
    ("tengo la cara hinchada", SWELLING),
    ("se me hincho el cachete", SWELLING),
    ("inchada la cara", SWELLING),
    ("se me esta hinchando la cara", SWELLING),
    ("tengo una bola en el cuello", SWELLING),
    ("creo que tengo una postemilla", SWELLING),
    ("tengo un absceso", SWELLING),
    ("se me hincharon los labios", SWELLING),
    ("tengo la cara hinchadísima", SWELLING),
    ("tengo la lengua hinchada", SWELLING),
    # Fever with dental pain
    ("tengo fiebre y me duele la muela", FEVER),
    ("tengo calentura y la encia hinchada", FEVER),
    # Blow or broken tooth
    ("me di un golpe en la boca", TRAUMA),
    ("me cai y se me rompio un diente", TRAUMA),
    ("se me quebro la muela", TRAUMA),
    ("a mi hijo le pegaron un balonazo en los dientes", TRAUMA),
    ("se me partio el diente de enfrente", TRAUMA),
    ("mi hijo se cayó y se pegó en la boca", TRAUMA),
    ("se me callo el diente de enfrente con el golpe", TRAUMA),
    # Trouble swallowing or breathing
    ("no puedo tragar", BREATHING),
    ("me cuesta respirar", BREATHING),
    ("siento que me ahogo", BREATHING),
    ("se me cierra la garganta", BREATHING),
    ("mi papá no puede tragar", BREATHING),
    ("me asfixio", BREATHING),
]

SHOULD_NOT_FIRE = [
    "¿la limpieza duele?",
    "¿me va a doler la extracción?",
    "¿duele mucho la extracción?",
    "¿el blanqueamiento duele?",
    "si me duele mucho después de la extracción, ¿qué hago?",
    "me dolió mucho la anestesia la vez pasada, ¿me pueden poner más?",
    "hola quiero una cita para limpieza",
    "¿cuánto cuesta un implante?",
    "¿tienen servicio de urgencias?",
    "tengo una cita mañana, ¿me la pueden cambiar?",
    "¿es normal que me sangren un poco las encías al cepillarme?",
    "se me cayó la resina",
    "a mi hijo se le cayó un diente de leche",
    "se me despegó un bracket",
    "¿puedo ir a mi limpieza si tuve fiebre la semana pasada?",
    "con los brackets me cuesta comer",
    "no puedo ir mañana a mi cita",
    "mi muela del juicio está saliendo, ¿la pueden revisar?",
    "me duelen los dientes con los cambios de temperatura",
    "ya me salió la muela del juicio",
    "ya le salió el primer diente a mi bebé",
]


@pytest.mark.parametrize(("text", "signal"), SHOULD_FIRE)
def test_urgent_messages_fire_with_their_signal(text: str, signal: str) -> None:
    assert detect_urgency(text, DEFAULT_SIGNALS) == signal


@pytest.mark.parametrize("text", SHOULD_NOT_FIRE)
def test_questions_and_ordinary_messages_do_not_fire(text: str) -> None:
    assert detect_urgency(text, DEFAULT_SIGNALS) is None


def test_the_task_asks_for_at_least_30_that_fire_and_15_that_do_not() -> None:
    assert len(SHOULD_FIRE) >= 30
    assert len(SHOULD_NOT_FIRE) >= 15


def test_a_burst_is_read_as_one_message() -> None:
    assert detect_urgency("me duele\nmuchísimo", DEFAULT_SIGNALS) == PAIN


def test_a_signal_the_clinic_removed_no_longer_fires() -> None:
    without_trauma = [signal for signal in DEFAULT_SIGNALS if signal != TRAUMA]
    assert detect_urgency("me di un golpe en la boca", without_trauma) is None


def test_a_signal_the_clinic_added_fires_as_written() -> None:
    signals = [*DEFAULT_SIGNALS, "se me cayó el puente"]
    assert detect_urgency("Se me cayo el PUENTE de los dientes!!", signals) == "se me cayó el puente"


def test_signals_stored_with_other_accents_or_capitals_still_use_their_rules() -> None:
    assert (
        detect_urgency("tengo la cara hinchada", ["Hinchazon de cara o cuello"])
        == "Hinchazon de cara o cuello"
    )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("Hinchazón", "inchason"),
        ("inchazon", "inchason"),
        ("MUCHÍSIMOOOO", "muchisimo"),
        ("¡¡No para!!", "no para"),
        ("dificil", "difisil"),
        ("vomito", "bomito"),
    ],
)
def test_normalization_forgives_accents_capitals_repeats_and_common_spelling(
    raw: str, normalized: str
) -> None:
    assert normalize(raw) == normalized
