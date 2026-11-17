"""The deterministic urgency filter: it runs before any AI, so it has to catch the ways patients
really write (typos, no accents, slang, voice-note transcriptions) without firing on questions
about a procedure."""

import pytest

from app.bot.urgency import DEFAULT_SIGNALS, active_signals, detect_urgency, normalize

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
    ("se me está hinchando la garganta", SWELLING),
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
    ("me golpearon en la boca", TRAUMA),
    ("tengo la boca golpeada", TRAUMA),
    ("hola, soy paciente nuevo, se me salió un diente", TRAUMA),
    ("mi bebé se cayó y se le salió un diente", TRAUMA),
    ("a mi hija se le zafó el diente", TRAUMA),
    ("mi hijo se cayó y se pegó en la boca, trae brackets", TRAUMA),
    ("se cayó de la bici y se pegó en la cara, usa placa", TRAUMA),
    ("se cayó, tiene brackets y le sangra la boca", TRAUMA),
    ("hola, tengo brackets\nmi hijo se cayó y se pegó en la boca", TRAUMA),
    ("se cayó y se pegó en el diente, trae brackets", TRAUMA),
    ("se le cayó un diente, trae brackets", TRAUMA),
    ("se me rompió un diente, traigo brackets", TRAUMA),
    ("mi hijo se cayó en la escuela y se le salió un diente", TRAUMA),
    # Trouble swallowing or breathing
    ("no puedo tragar", BREATHING),
    ("me cuesta respirar", BREATHING),
    ("siento que me ahogo", BREATHING),
    ("se me cierra la garganta", BREATHING),
    ("mi papá no puede tragar", BREATHING),
    ("me asfixio", BREATHING),
    ("le falta el aire", BREATHING),
    ("a mi hijo no le entra el aire", BREATHING),
    ("se está ahogando", BREATHING),
    ("se le está cerrando la garganta", BREATHING),
    ("no le pasa la saliva", BREATHING),
    ("tiene la garganta cerrada", BREATHING),
    ("no puede pasar ni el agua", BREATHING),
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
    "se cayó la resina de la muela",
    "no se pegó bien la resina del diente",
    "me lastimé el labio con el bracket",
    "se me zafó la corona del diente",
    "tengo una bolita en el párpado",
    "me salió una bolita en el labio",
    "tengo una bolita en la lengua",
    "no voy a poder ir, tengo gripa y la garganta inflamada",
    "ya se cayó el diente de leche de mi hijo",
    "me lastimé la encía con el cepillo",
    "la resina se me cayó del diente",
    "se me rompió la resina del diente",
    "a mi hijo se le cayó el bracket del diente",
]


@pytest.mark.parametrize(("text", "signal"), SHOULD_FIRE)
def test_urgent_messages_fire_with_their_signal(text: str, signal: str) -> None:
    assert detect_urgency(text, DEFAULT_SIGNALS) == signal


@pytest.mark.parametrize("text", SHOULD_NOT_FIRE)
def test_questions_and_ordinary_messages_do_not_fire(text: str) -> None:
    assert detect_urgency(text, DEFAULT_SIGNALS) is None


@pytest.mark.parametrize(
    "text",
    [
        "¿le duele mucho a los niños la extracción?",
        "a mi hija de 6 años se le cayó un diente, ¿la pueden revisar?",
    ],
)
def test_false_alarms_accepted_while_jared_decides(text: str) -> None:
    """The filter errs on the side of firing; whether these should stay is P14 in docs/avance.md."""
    assert detect_urgency(text, DEFAULT_SIGNALS) is not None


def test_the_task_asks_for_at_least_30_that_fire_and_15_that_do_not() -> None:
    assert len(SHOULD_FIRE) >= 30
    assert len(SHOULD_NOT_FIRE) >= 15


def test_a_burst_is_read_as_one_message() -> None:
    assert detect_urgency("me duele\nmuchísimo", DEFAULT_SIGNALS) == PAIN


def test_a_signal_the_clinic_removed_no_longer_fires() -> None:
    without_trauma = [signal for signal in DEFAULT_SIGNALS if signal != TRAUMA]
    assert detect_urgency("me di un golpe en la boca", without_trauma) is None


def test_trouble_breathing_cannot_be_turned_off_and_is_checked_first() -> None:
    signals = active_signals([PAIN, TRAUMA])
    assert signals == (BREATHING, PAIN, TRAUMA)
    assert detect_urgency("no puedo respirar", signals) == BREATHING
    assert detect_urgency("me duele mucho y no puedo respirar", active_signals(DEFAULT_SIGNALS)) == BREATHING


def test_the_clinic_spelling_of_the_breathing_signal_is_kept() -> None:
    assert active_signals([PAIN, "Dificultad para tragar o respirar"]) == (
        "Dificultad para tragar o respirar",
        PAIN,
    )


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
