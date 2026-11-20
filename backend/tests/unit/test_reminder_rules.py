"""Milestone 1D, the rules without a database: the slots of the scheduled runs, the clinic's time
zone, how the reminder and its answers are worded, which taps belong to a reminder, and what
Meta's notices about the account mean."""

from datetime import UTC, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.bot import fixed
from app.bot.fixed import Voice
from app.bot.prompt import at_time, spoken_day
from app.bot.reminder_taps import MOVE_PREFIX, closest_per_day, read_taps, when_phrase
from app.core.config import SettingsError, load_settings
from app.db.conversations import PendingMessage
from app.jobs.timing import clinic_zone, slot_of
from app.scheduling.availability import Slot
from app.whatsapp import templates
from app.whatsapp.account import (
    AccountNotice,
    QualityNotice,
    TemplateCategoryNotice,
    TemplateStatusNotice,
    read_account_notice,
    same_phone,
)

CDMX = ZoneInfo("America/Mexico_City")
INFORMAL = Voice("Clínica Dental Sonrisa", "la doctora Laura", formal=False)
FORMAL = Voice("Consultorio Dental Monte Albán", "el doctor Ramiro", formal=True)


# Slots and time zones -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "every", "slot"),
    [
        ("2026-11-17T18:14:59", timedelta(minutes=15), "2026-11-17T18:00:00"),
        ("2026-11-17T18:15:00", timedelta(minutes=15), "2026-11-17T18:15:00"),
        ("2026-11-17T18:59:59", timedelta(minutes=1), "2026-11-17T18:59:00"),
        ("2026-11-17T18:59:59", timedelta(hours=1), "2026-11-17T18:00:00"),
    ],
)
def test_a_run_belongs_to_the_slot_it_falls_in(moment: str, every: timedelta, slot: str) -> None:
    as_utc = datetime.fromisoformat(moment).replace(tzinfo=UTC)
    assert slot_of(as_utc, every) == datetime.fromisoformat(slot).replace(tzinfo=UTC)


def test_the_same_moment_in_another_zone_is_the_same_slot() -> None:
    utc = datetime(2026, 11, 17, 18, 7, tzinfo=UTC)
    assert slot_of(utc.astimezone(CDMX), timedelta(minutes=15)) == slot_of(utc, timedelta(minutes=15))


@pytest.mark.parametrize(
    ("zone", "local_hour_at_9_utc"),
    [("America/Mexico_City", 3), ("America/Cancun", 4), ("America/Tijuana", 1)],
)
def test_fixed_hours_follow_each_clinic_zone_not_the_server(zone: str, local_hour_at_9_utc: int) -> None:
    """In November Tijuana is on winter time (UTC-8); Mexico City and Cancún never change."""
    nine_utc = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
    assert nine_utc.astimezone(clinic_zone(zone)).hour == local_hour_at_9_utc


def test_an_unknown_zone_falls_back_to_mexico_city() -> None:
    assert clinic_zone("Marte/Olympus") == CDMX
    assert clinic_zone("") == CDMX


# How the reminder is worded -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clock", "spoken"),
    [
        (time(10, 0), "a las 10:00 am"),
        (time(13, 30), "a la 1:30 pm"),
        (time(1, 5), "a la 1:05 am"),
        (time(12, 0), "a las 12:00 pm"),
        (time(17, 45), "a las 5:45 pm"),
    ],
)
def test_one_o_clock_is_singular(clock: time, spoken: str) -> None:
    assert at_time(clock) == spoken


def test_the_reminder_parameters_carry_only_what_the_message_needs() -> None:
    start = datetime(2026, 11, 18, 10, 0, tzinfo=CDMX)
    parameters = templates.reminder_parameters(
        patient_name="  DRA. ANA   María López ",
        service_name="Limpieza\n dental",
        local_start=start,
        clinic_name="Clínica Dental Sonrisa",
    )
    assert parameters == [
        "Ana",
        "limpieza dental",
        "Clínica Dental Sonrisa",
        "miércoles 18 de noviembre a las 10:00 am",
    ]
    assert templates.fill(templates.REMINDER_BODY, parameters) == (
        "Hola Ana, un recordatorio: la cita de limpieza dental en Clínica Dental Sonrisa es mañana "
        "miércoles 18 de noviembre a las 10:00 am. ¿Confirmamos?"
    )


def test_one_o_clock_in_the_reminder_is_singular_too() -> None:
    parameters = templates.reminder_parameters(
        patient_name="ana",
        service_name="Valoración",
        local_start=datetime(2026, 11, 18, 13, 30, tzinfo=CDMX),
        clinic_name="X",
    )
    assert parameters[0] == "Ana"  # typed in lowercase, greeted with its capital
    assert parameters[3] == "miércoles 18 de noviembre a la 1:30 pm"


@pytest.mark.parametrize(
    ("service", "phrase"),
    [
        ("Limpieza", "limpieza"),
        ("Blanqueamiento LED", "blanqueamiento LED"),
        ("LED", "LED"),
        (None, "consulta"),
        ("  ", "consulta"),
    ],
)
def test_the_service_reads_in_the_middle_of_a_sentence(service: str | None, phrase: str) -> None:
    assert templates.service_phrase(service) == phrase


def test_a_patient_without_a_name_is_greeted_hola_de_nuevo() -> None:
    parameters = templates.reminder_parameters(
        patient_name=None,
        service_name="Valoración",
        local_start=datetime(2026, 11, 18, 9, tzinfo=CDMX),
        clinic_name="X",
    )
    assert parameters[0] == "de nuevo"


def test_the_body_and_buttons_are_the_ones_submitted_to_meta() -> None:
    """Revisor-clinico: not ending in a variable, asking for the confirmation, and labels that a
    quick thumb does not mix up (Cambiar and Cancelar looked alike)."""
    assert spoken_day(datetime(2026, 11, 20, tzinfo=CDMX)) == "viernes 20 de noviembre"
    assert templates.REMINDER_BODY.endswith("¿Confirmamos?")
    assert "{4}" in templates.REMINDER_BODY
    assert "{5}" not in templates.REMINDER_BODY
    assert templates.REMINDER_BUTTON_LABELS == ("Confirmo", "Cambiar horario", "Cancelar cita")
    assert all(len(label) <= 25 for label in templates.REMINDER_BUTTON_LABELS)


@pytest.mark.parametrize(
    ("moment", "spoken"),
    [
        (datetime(2026, 11, 17, 16, 0, tzinfo=CDMX), "hoy a las 4:00 pm"),
        (datetime(2026, 11, 18, 10, 0, tzinfo=CDMX), "mañana miércoles 18 de noviembre a las 10:00 am"),
        (datetime(2026, 11, 20, 13, 15, tzinfo=CDMX), "el viernes 20 de noviembre a la 1:15 pm"),
    ],
)
def test_the_answers_say_the_day_as_people_do(moment: datetime, spoken: str) -> None:
    now = datetime(2026, 11, 17, 9, 0, tzinfo=CDMX).astimezone(UTC)
    assert when_phrase(moment.astimezone(UTC), now, CDMX) == spoken


def test_the_fixed_answers_respect_tu_and_usted() -> None:
    when = "el viernes 20 de noviembre a las 10:00 am"
    informal = [
        fixed.reminder_confirmed(INFORMAL, "Limpieza", when),
        fixed.reminder_cancelled(INFORMAL, "Limpieza", when),
        fixed.reminder_options(INFORMAL, "Limpieza"),
        fixed.reminder_no_options(INFORMAL),
        fixed.reminder_moved(INFORMAL, "Limpieza", when),
        fixed.reminder_slot_taken(INFORMAL, alternatives=True),
        fixed.reminder_slot_taken(INFORMAL, alternatives=False),
        fixed.reminder_not_moved(INFORMAL),
        fixed.reminder_already_cancelled(INFORMAL),
        fixed.reminder_already_past(INFORMAL),
        fixed.reminder_already_moved(INFORMAL, when),
        fixed.reminder_running_late(INFORMAL, "hoy a las 10:00 am"),
    ]
    formal = [
        fixed.reminder_confirmed(FORMAL, "Limpieza", when),
        fixed.reminder_cancelled(FORMAL, "Limpieza", when),
        fixed.reminder_options(FORMAL, "Limpieza"),
        fixed.reminder_no_options(FORMAL),
        fixed.reminder_moved(FORMAL, "Limpieza", when),
        fixed.reminder_slot_taken(FORMAL, alternatives=True),
        fixed.reminder_slot_taken(FORMAL, alternatives=False),
        fixed.reminder_not_moved(FORMAL),
        fixed.reminder_already_cancelled(FORMAL),
        fixed.reminder_already_past(FORMAL),
        fixed.reminder_already_moved(FORMAL, when),
        fixed.reminder_running_late(FORMAL, "hoy a las 10:00 am"),
    ]
    # "Tu cita", "te esperamos", "quieres", "dime", "escríbeme", "elijas": only with "tú".
    tu_words = (" tu ", " te ", "quieres", " dime ", "escríbeme", "elijas")
    for text in formal:
        assert not any(word in f" {text.lower()}" for word in tu_words), text
    for text in informal[:3]:
        assert any(word in f" {text.lower()}" for word in tu_words), text
    assert all(len(text) <= 1024 for text in informal + formal)
    assert fixed.reminder_cancelled(INFORMAL, "Limpieza", when).startswith(
        "Listo, cancelé tu cita de Limpieza del viernes 20 de noviembre"
    )
    assert "de mañana miércoles" in fixed.reminder_cancelled(INFORMAL, "Limpieza", "mañana miércoles 18")


def test_cambiar_offers_one_slot_a_day_near_the_appointment_time() -> None:
    provider = uuid4()

    def slot(day: int, hour: int) -> Slot:
        start = datetime(2026, 11, day, hour, 0, tzinfo=CDMX).astimezone(UTC)
        return Slot(provider, start, start + timedelta(minutes=45), f"s{day}{hour}", f"{day} {hour}:00")

    slots = [slot(18, 9), slot(18, 17), slot(19, 11), slot(20, 9), slot(20, 16), slot(21, 18)]
    assert [s.id for s in closest_per_day(slots, CDMX, 3, time(18, 0))] == ["s1817", "s1911", "s2016"]
    assert [s.id for s in closest_per_day(slots, CDMX, 3, time(9, 0))] == ["s189", "s1911", "s209"]


# Which taps belong to a reminder ------------------------------------------------------------------


def _message(kind: str, content: str | None, reply_id: str | None = None) -> PendingMessage:
    return PendingMessage(
        uuid4(), kind, content, None, None, None, None, datetime.now(UTC), reply_id, "wamid.X"
    )


def test_the_three_buttons_are_read_from_their_label_however_written() -> None:
    batch = [
        _message("button", "Confirmo", "Confirmo"),
        _message("button", "CANCELAR CITA ", "CANCELAR CITA "),
        _message("button", "Cambiar horario", None),
        _message("button", "Cancelar", "Cancelar"),  # the short label of the specification
        _message("button", "Sí, seguimos", "Sí, seguimos"),  # another template's button
        _message("text", "Confirmo"),  # typed, not tapped: the AI reads it
    ]
    assert [tap.action for tap in read_taps(batch)] == ["confirm", "cancel", "change", "cancel"]


def test_a_slot_of_cambiar_carries_its_reminder_and_slot_key() -> None:
    reminder = uuid4()
    batch = [
        _message("interactive", "Jue 10:00 am", f"{MOVE_PREFIX}{reminder}:abcd2345"),
        _message("interactive", "Jue 10:00 am", f"{MOVE_PREFIX}no-es-uuid:abcd2345"),
        _message("interactive", "Jue 10:00 am", f"{MOVE_PREFIX}{reminder}:"),
        _message("interactive", "Jue 10:00 am", f"{MOVE_PREFIX}{reminder}:ab cd; drop"),
        _message("interactive", "Jue 10:00 am", "2026-11-19T10:00:00-06:00"),  # a slot of the AI
    ]
    [tap] = read_taps(batch)
    assert (tap.action, tap.reminder_id, tap.slot_key) == ("move", reminder, "abcd2345")
    assert isinstance(tap.reminder_id, UUID)


# Meta's notices about the account -----------------------------------------------------------------


def _status(event: str, **extra: Any) -> TemplateStatusNotice:
    value = {
        "event": event,
        "message_template_id": 1689556908129832,
        "message_template_name": "recordatorio_cita",
        "message_template_language": "es_MX",
        "reason": "NONE",
        **extra,
    }
    notice = read_account_notice("message_template_status_update", value)
    assert isinstance(notice, TemplateStatusNotice)
    return notice


@pytest.mark.parametrize(
    ("event", "status", "alert"),
    [
        ("APPROVED", "approved", None),
        ("REINSTATED", "approved", None),
        ("PENDING", "pending", None),
        ("IN_APPEAL", "pending", None),
        ("REJECTED", "rejected", "template"),
        ("PAUSED", "paused", "template"),
        ("DISABLED", "disabled", "template"),
        ("PENDING_DELETION", "disabled", "template"),
        ("FLAGGED", None, "template"),  # still usable, but its quality is low
        ("LOCKED", None, None),
        ("UNARCHIVED", None, None),
    ],
)
def test_template_events_map_to_the_catalog_and_alert_when_a_template_stops(
    event: str, status: str | None, alert: str | None
) -> None:
    notice = _status(event)
    assert (notice.status, notice.alert) == (status, alert)
    assert notice.meta_template_id == "1689556908129832"


def test_a_status_notice_keeps_meta_codes_only_and_the_catalog_language() -> None:
    notice = _status("REJECTED", reason="Texto libre <script>", message_template_language="en-US")
    assert notice.reason is None
    assert notice.language == "en_US"
    assert read_account_notice("message_template_status_update", {"event": "APPROVED"}) is None


def test_a_category_change_counts_only_once_meta_made_it() -> None:
    done = read_account_notice(
        "template_category_update",
        {
            "message_template_id": 1,
            "message_template_name": "recordatorio_cita",
            "message_template_language": "es_MX",
            "previous_category": "UTILITY",
            "new_category": "MARKETING",
        },
    )
    announced = read_account_notice(
        "template_category_update",
        {
            "message_template_id": 1,
            "message_template_name": "recordatorio_cita",
            "message_template_language": "es_MX",
            "correct_category": "MARKETING",
            "new_category": "UTILITY",
            "category_update_timestamp": 1751247548,
        },
    )
    assert isinstance(done, TemplateCategoryNotice)
    assert isinstance(announced, TemplateCategoryNotice)
    assert (done.done, done.new_category, done.alert) == (True, "marketing", "template")
    assert (announced.done, announced.alert) == (False, "template")


@pytest.mark.parametrize(
    ("value", "worse"),
    [
        ({"event": "THROUGHPUT_UPGRADE", "current_limit": "TIER_UNLIMITED"}, False),
        ({"event": "ONBOARDING", "current_limit": "TIER_250"}, False),
        ({"event": "FLAGGED", "current_limit": "TIER_250"}, True),
        ({"event": "DOWNGRADE", "old_limit": "TIER_1K", "current_limit": "TIER_250"}, True),
        ({"event": "UPGRADE", "old_limit": "TIER_2K", "current_limit": "TIER_250"}, True),
        ({"event": "SOMETHING_NEW"}, True),  # unknown news counts as bad news
    ],
)
def test_a_quality_or_limit_drop_alerts(value: dict[str, Any], worse: bool) -> None:
    notice = read_account_notice(
        "phone_number_quality_update", {"display_phone_number": "15550783881", **value}
    )
    assert isinstance(notice, QualityNotice)
    assert notice.worse is worse
    assert notice.alert == ("quality" if worse else None)


@pytest.mark.parametrize(
    ("value", "serious"),
    [
        (
            {
                "event": "ACCOUNT_RESTRICTION",
                "restriction_info": [{"restriction_type": "RESTRICTED_BIZ_INITIATED_MESSAGING"}],
            },
            True,
        ),
        ({"event": "ACCOUNT_VIOLATION", "violation_info": {"violation_type": "ADULT"}}, True),
        ({"event": "DISABLED_UPDATE", "ban_info": {"waba_ban_state": "DISABLE"}}, True),
        ({"event": "DISABLED_UPDATE", "ban_info": {"waba_ban_state": "REINSTATE"}}, False),
        ({"event": "PARTNER_ADDED"}, False),
    ],
)
def test_account_restrictions_alert(value: dict[str, Any], serious: bool) -> None:
    notice = read_account_notice("account_update", value)
    assert isinstance(notice, AccountNotice)
    assert notice.serious is serious


def test_a_quality_notice_is_matched_to_the_clinic_number_by_its_digits() -> None:
    assert same_phone("529510000001", "+529510000001")
    assert same_phone("+52 951 000 0001", "+529510000001")
    assert not same_phone("529510000002", "+529510000001")
    assert same_phone(None, "+529510000001")


def test_jared_phone_for_alerts_must_be_e164_and_is_never_echoed(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "ENVIRONMENT": "development",
        "DATABASE_URL": "postgresql://axon_app:x@127.0.0.1:54322/postgres",
        "WHATSAPP_APP_SECRET": "a" * 32,
        "WHATSAPP_VERIFY_TOKEN": "b" * 32,
        "WHATSAPP_ACCESS_TOKEN": "c" * 32,
        "META_GRAPH_VERSION": "v24.0",
        "SUPABASE_URL": "http://127.0.0.1:54321",
        "OPS_ALERT_PHONE": "951 123 4567",
    }.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(SettingsError) as error:
        load_settings(env_file=None)
    assert "OPS_ALERT_PHONE" in str(error.value)
    assert "951" not in str(error.value)
