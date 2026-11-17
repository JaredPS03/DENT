"""Messages the code sends without the AI (docs/prompt-del-bot.md, "Mensajes fijos").

They go out when the AI must not or cannot answer: an urgency, the privacy notice, a failure.
Texts are in Mexican Spanish, in "tú" or "usted" as the clinic configured. Changing them needs a
review by revisor-clinico; in milestone 1E the dentist can adjust them in Configuración.
"""

from dataclasses import dataclass

TEAM = "el equipo del consultorio"


@dataclass(frozen=True, slots=True)
class Voice:
    clinic_name: str
    # How the bot refers to the dentist: "la doctora Pérez", "el doctor". None: the team.
    doctor: str | None
    formal: bool


def urgency(voice: Voice) -> str:
    """The 911 only for trouble breathing or swallowing (spec); worse pain goes to urgencias."""
    told = _to(voice.doctor or TEAM)
    if voice.formal:
        return (
            f"Lamento lo que está pasando. Ya avisé {told} para que se comunique con usted lo antes "
            "posible. Si le cuesta respirar o tragar, llame al 911 ahora. Si el dolor, el sangrado o "
            "la hinchazón empeoran mientras tanto, acuda a urgencias."
        )
    return (
        f"Lamento lo que estás pasando. Ya avisé {told} para que te contacte lo antes posible. "
        "Si te cuesta respirar o tragar, llama al 911 ahora. Si el dolor, el sangrado o la "
        "hinchazón empeoran mientras tanto, acude a urgencias."
    )


def fallback(voice: Voice) -> str:
    if voice.formal:
        return (
            f"Gracias por su mensaje. Alguien del equipo de {voice.clinic_name} le responderá "
            "lo antes posible."
        )
    return (
        f"Gracias por tu mensaje. Alguien del equipo de {voice.clinic_name} te responderá lo antes posible."
    )


def voice_note_unheard(voice: Voice) -> str:
    """A voice note nobody could transcribe: a person listens to it (it might be an urgency)."""
    if voice.formal:
        return (
            "Recibí su nota de voz, pero no pude escucharla bien. Alguien del equipo de "
            f"{voice.clinic_name} la escuchará y le responderá lo antes posible."
        )
    return (
        "Recibí tu nota de voz, pero no pude escucharla bien. Alguien del equipo de "
        f"{voice.clinic_name} la escuchará y te responderá lo antes posible."
    )


def consent_notice(voice: Voice, notice_url: str) -> str:
    """The purposes are spelled out (LFPDPPP: consent for sensitive data is for concrete purposes);
    they must match the clinic's full notice. The link goes on its own line."""
    if voice.formal:
        return (
            f"Hola, soy el asistente virtual de {voice.clinic_name}. Para agendar y recordarle sus "
            "citas y atender sus dudas, necesitamos su permiso para usar sus datos, incluidos datos "
            f"de salud. Aquí está nuestro aviso de privacidad:\n{notice_url}\n\n¿Acepta?"
        )
    return (
        f"Hola, soy el asistente virtual de {voice.clinic_name}. Para agendar y recordarte tus "
        "citas y atender tus dudas, necesitamos tu permiso para usar tus datos, incluidos datos "
        f"de salud. Aquí está nuestro aviso de privacidad:\n{notice_url}\n\n¿Aceptas?"
    )


def consent_reminder(voice: Voice, notice_url: str) -> str:
    """Goes under the AI's general answer while the patient has not accepted."""
    if voice.formal:
        return (
            "Para agendar o darle seguimiento necesitamos su permiso para usar sus datos, incluidos "
            f"datos de salud. Aviso de privacidad:\n{notice_url}\n\n¿Acepta?"
        )
    return (
        "Para agendar o darte seguimiento necesitamos tu permiso para usar tus datos, incluidos "
        f"datos de salud. Aviso de privacidad:\n{notice_url}\n\n¿Aceptas?"
    )


def consent_declined(voice: Voice) -> str:
    """The bot stops writing; the chat goes to the team, who is alerted (pending legal review)."""
    if voice.formal:
        return (
            f"Entendido. El asistente ya no le escribirá; alguien del equipo de {voice.clinic_name} "
            "le responderá por aquí."
        )
    return (
        f"Entendido. El asistente ya no te escribirá; alguien del equipo de {voice.clinic_name} "
        "te responderá por aquí."
    )


def _to(person: str) -> str:
    """'a la doctora Pérez', 'al equipo del consultorio'."""
    if person.startswith("el "):
        return "al " + person.removeprefix("el ")
    return "a " + person
