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
    told = _to(voice.doctor or TEAM)
    if voice.formal:
        return (
            f"Lamento que se sienta así. Ya avisé {told} para que le contacte lo antes posible. "
            "Si el dolor o la hinchazón aumentan, o le cuesta respirar o tragar, llame al 911 "
            "o acuda a urgencias."
        )
    return (
        f"Lamento que te sientas así. Ya avisé {told} para que te contacte lo antes posible. "
        "Si el dolor o la hinchazón aumentan, o te cuesta respirar o tragar, llama al 911 "
        "o acude a urgencias."
    )


def fallback(voice: Voice) -> str:
    if voice.formal:
        return f"Gracias por su mensaje. En un momento le responde el equipo de {voice.clinic_name}."
    return f"Gracias por tu mensaje. En un momento te responde el equipo de {voice.clinic_name}."


def consent_notice(voice: Voice, notice_url: str) -> str:
    if voice.formal:
        return (
            f"Hola, soy el asistente virtual de {voice.clinic_name}. Para ayudarle necesitamos su "
            "autorización para tratar sus datos, incluidos datos de salud, según nuestro aviso de "
            f"privacidad: {notice_url}. ¿Acepta?"
        )
    return (
        f"Hola, soy el asistente virtual de {voice.clinic_name}. Para ayudarte necesitamos tu "
        "autorización para tratar tus datos, incluidos datos de salud, según nuestro aviso de "
        f"privacidad: {notice_url}. ¿Aceptas?"
    )


def consent_reminder(voice: Voice, notice_url: str) -> str:
    """Goes under the AI's general answer while the patient has not accepted."""
    if voice.formal:
        return (
            "Para agendar o darle seguimiento necesitamos su autorización para tratar sus datos, "
            f"incluidos datos de salud, según nuestro aviso de privacidad: {notice_url}. ¿Acepta?"
        )
    return (
        "Para agendar o darte seguimiento necesitamos tu autorización para tratar tus datos, "
        f"incluidos datos de salud, según nuestro aviso de privacidad: {notice_url}. ¿Aceptas?"
    )


def consent_declined(voice: Voice) -> str:
    if voice.formal:
        return "Entendido. El equipo del consultorio le atenderá directamente por este medio."
    return "Entendido. El equipo del consultorio te atenderá directamente por este medio."


def _to(person: str) -> str:
    """'a la doctora Pérez', 'al equipo del consultorio'."""
    if person.startswith("el "):
        return "al " + person.removeprefix("el ")
    return "a " + person
