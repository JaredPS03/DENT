"""The versioned prompt, its clinic block and the history the AI reads."""

import re
from datetime import UTC, datetime, time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.bot.history import build_messages
from app.bot.prompt import PROMPTS_DIR, opening_hours, spoken_moment, template
from app.bot.reply import CONSENT_ACCEPT_ID, OfferedSlot
from app.core.config import Settings
from app.db.bot import HistoryRow
from app.scheduling.availability import WeeklyHours

DOC = Path(__file__).resolve().parents[3] / "docs" / "prompt-del-bot.md"
TZ = ZoneInfo("America/Mexico_City")


def test_the_versioned_prompt_is_the_one_in_the_docs() -> None:
    """bot-ia.md: the source is docs/prompt-del-bot.md; the copy in app/bot/prompts must not drift."""
    documented = re.search(r"## Prompt de sistema\n\n```text\n(.*?)\n```", DOC.read_text(), re.S)
    assert documented is not None
    version = Settings.model_fields["bot_prompt_version"].default
    assert (PROMPTS_DIR / f"v{version}.md").read_text() == documented.group(1) + "\n"
    assert f"Versión {version}." in DOC.read_text()


def test_the_prompt_ends_with_what_changes_each_turn() -> None:
    text = template("1.0")
    fixed_part, turn_part = text.split("<contexto>")
    for variable in ("{{ahora}}", "{{paciente.bloque}}"):
        assert variable not in fixed_part
        assert variable in turn_part


def test_opening_hours_join_days_with_the_same_hours() -> None:
    provider = uuid4()
    hours = [WeeklyHours(provider, day, time(9), time(19), time(14), time(16), 10) for day in range(1, 6)] + [
        WeeklyHours(provider, 6, time(9), time(14), None, None, 10)
    ]
    assert opening_hours(hours) == (
        "lunes a viernes de 9:00 am a 7:00 pm (cerrado de 2:00 pm a 4:00 pm); "
        "sábado de 9:00 am a 2:00 pm; domingo cerrado"
    )


def test_moments_are_written_as_people_say_them() -> None:
    moment = datetime(2026, 11, 17, 16, 35, tzinfo=TZ)
    assert spoken_moment(moment) == "martes 17 de noviembre de 2026, 4:35 pm"


def row(direction: str, content: str | None, **extra: object) -> HistoryRow:
    values: dict[str, object] = {
        "direction": direction,
        "sender": "patient" if direction == "inbound" else "bot",
        "type": "text",
        "content": content,
        "transcription": None,
        "reply_id": None,
        "created_at": datetime.now(UTC),
    }
    values.update(extra)
    return HistoryRow(**values)  # type: ignore[arg-type]


def test_history_starts_and_ends_with_the_patient_and_marks_what_the_code_adds() -> None:
    start = datetime(2026, 11, 19, 22, 0, tzinfo=UTC)
    slot = OfferedSlot("abcd2345", uuid4(), "Limpieza", start, "Jue 4:00 pm")
    messages = build_messages(
        [
            row("outbound", "Hola, ¿en qué te ayudo?"),  # before the patient wrote: dropped
            row("inbound", "quiero una limpieza"),
            row("outbound", "Tengo estos horarios:"),
            row("inbound", "Jue 4:00 pm", type="interactive", reply_id="2026-11-19T16:00:00-06:00"),
            row("inbound", None, type="audio", transcription="y si se puede temprano mejor"),
            row("inbound", None, type="audio"),
            row("inbound", "Acepto", type="interactive", reply_id=CONSENT_ACCEPT_ID),
        ],
        {start: slot},
        TZ,
    )
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[0]["content"] == "<mensaje_paciente>quiero una limpieza</mensaje_paciente>"
    assert messages[2]["content"].splitlines() == [
        "<mensaje_paciente>Jue 4:00 pm</mensaje_paciente> (Tocó el horario «Jue 4:00 pm» de Limpieza; "
        "inicio exacto: 2026-11-19T16:00:00-06:00)",
        "<transcripcion>y si se puede temprano mejor</transcripcion>",
        "(Nota de voz que no se pudo transcribir)",
        "(Tocó «Acepto» en el aviso de privacidad)",
    ]


def test_a_button_id_that_is_not_an_offered_slot_adds_nothing() -> None:
    messages = build_messages(
        [row("inbound", "Jue 4:00 pm", type="interactive", reply_id="2030-01-01T10:00:00-06:00")], {}, TZ
    )
    assert messages == [{"role": "user", "content": "<mensaje_paciente>Jue 4:00 pm</mensaje_paciente>"}]
