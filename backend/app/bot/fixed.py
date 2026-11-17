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
            f"Hola, soy el asistente virtual de {voice.clinic_name}. Recibí su nota de voz, pero no "
            "pude escucharla bien; alguien del equipo la escuchará y le responderá lo antes posible."
        )
    return (
        f"Hola, soy el asistente virtual de {voice.clinic_name}. Recibí tu nota de voz, pero no "
        "pude escucharla bien; alguien del equipo la escuchará y te responderá lo antes posible."
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


def doctor_phrase(doctor: str) -> str:
    """For the middle of a sentence, with its article in lowercase: "La doctora Pérez" is
    "la doctora Pérez", and "Dra. Pérez" is "la Dra. Pérez"."""
    first, _, rest = doctor.partition(" ")
    if first.lower() in {"el", "la"} and rest:
        return f"{first.lower()} {rest}"
    if first.lower() == "doctora" and rest:
        return f"la doctora {rest}"  # the title in lowercase in the middle of a sentence
    if first.lower() == "doctor" and rest:
        return f"el doctor {rest}"
    if first.lower() in {"dra.", "dra"}:
        return f"la {doctor}"
    if first.lower() in {"dr.", "dr"}:
        return f"el {doctor}"
    return doctor


def patient_label(name: str | None) -> str:
    """The first name for the team's summaries, or that there is none."""
    first = name.split()[0] if name and name.split() else None
    return first or "sin nombre registrado"


def _to(person: str) -> str:
    """'a la doctora Pérez', 'al equipo del consultorio', even if the clinic wrote 'El Dr. Ramírez'."""
    person = doctor_phrase(person)
    if person.startswith("el "):
        return "al " + person.removeprefix("el ")
    return "a " + person


# Buttons of recordatorio_cita (milestone 1D). `when` is spoken by the caller: "mañana martes 18
# de noviembre a las 10:00 am", or "el martes 18 de noviembre a las 10:00 am".


def reminder_confirmed(voice: Voice, service: str, when: str) -> str:
    if voice.formal:
        return f"¡Gracias! Su cita de {service} queda confirmada para {when}. Le esperamos."
    return f"¡Gracias! Tu cita de {service} queda confirmada para {when}. Te esperamos."


def reminder_cancelled(voice: Voice, service: str, when: str) -> str:
    of_when = "del " + when.removeprefix("el ") if when.startswith("el ") else "de " + when
    if voice.formal:
        return (
            f"Listo, cancelé su cita de {service} {of_when}. Si quiere otra fecha, escríbame y le "
            "busco horario."
        )
    return (
        f"Listo, cancelé tu cita de {service} {of_when}. Si quieres otra fecha, escríbeme y te busco horario."
    )


def reminder_options(voice: Voice, service: str) -> str:
    """Goes with up to three slots as buttons. The appointment is not touched until one is chosen."""
    if voice.formal:
        return (
            f"Claro. Estos horarios están libres para su {service}. Su cita actual sigue en pie hasta "
            "que elija uno. Si ninguno le acomoda, dígame qué día prefiere."
        )
    return (
        f"Claro. Estos horarios están libres para tu {service}. Tu cita actual sigue en pie hasta que "
        "elijas uno. Si ninguno te acomoda, dime qué día prefieres."
    )


def reminder_no_options(voice: Voice) -> str:
    if voice.formal:
        return (
            "No encontré horarios libres en los próximos días. Su cita actual sigue en pie. Dígame qué "
            "día le acomoda y lo busco."
        )
    return (
        "No encontré horarios libres en los próximos días. Tu cita actual sigue en pie. Dime qué día "
        "te acomoda y lo busco."
    )


def reminder_moved(voice: Voice, service: str, when: str) -> str:
    if voice.formal:
        return f"Listo, su cita de {service} quedó para {when}. Le esperamos."
    return f"Listo, tu cita de {service} quedó para {when}. Te esperamos."


def reminder_slot_taken(voice: Voice, *, alternatives: bool) -> str:
    if alternatives:
        if voice.formal:
            return "Ese horario se acaba de ocupar. Estos siguen libres; su cita actual sigue en pie."
        return "Ese horario se acaba de ocupar. Estos siguen libres; tu cita actual sigue en pie."
    if voice.formal:
        return (
            "Ese horario se acaba de ocupar y su cita actual sigue en pie. Dígame qué día le acomoda "
            "y lo busco."
        )
    return "Ese horario se acaba de ocupar y tu cita actual sigue en pie. Dime qué día te acomoda y lo busco."


def reminder_not_moved(voice: Voice) -> str:
    """The agenda refused the change for another reason (the patient has another appointment then)."""
    if voice.formal:
        return "No pude mover su cita a ese horario y sigue en pie. Dígame qué día le acomoda y lo busco."
    return "No pude mover tu cita a ese horario y sigue en pie. Dime qué día te acomoda y lo busco."


def reminder_already_cancelled(voice: Voice) -> str:
    if voice.formal:
        return "Esa cita ya está cancelada. Si quiere agendar otra, escríbame y le busco horario."
    return "Esa cita ya está cancelada. Si quieres agendar otra, escríbeme y te busco horario."


def reminder_already_past(voice: Voice) -> str:
    if voice.formal:
        return "Esa cita ya pasó. Si quiere agendar otra, escríbame y le busco horario."
    return "Esa cita ya pasó. Si quieres agendar otra, escríbeme y te busco horario."


def reminder_already_moved(voice: Voice, when: str) -> str:
    """The patient tapped an old reminder: the appointment is now at another time."""
    if voice.formal:
        return f"Su cita cambió: ahora es {when}. Si quiere moverla o cancelarla, escríbame."
    return f"Tu cita cambió: ahora es {when}. Si quieres moverla o cancelarla, escríbeme."
