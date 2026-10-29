"""Who a WhatsApp user is: a phone number in E.164 and, since April 2026, Meta's business-scoped
user id (BSUID). A user who adopts a username may reach the clinic with only the BSUID."""

import re

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{7,14}$")
# "US.13491208655302741918": country code, a dot and an alphanumeric id. Parent BSUIDs
# ("US.ENT.…") are only for businesses with several portfolios; Axon does not use them.
BSUID_PATTERN = re.compile(r"^[A-Z]{2}\.[A-Za-z0-9]{1,128}$")
_DIGITS = re.compile(r"^[0-9]+$")
# Mexican mobiles kept a "1" after the country code until 2020, and WhatsApp still reports it for
# older accounts. Meta notes it may change that prefix, so one number has a single form: +52 and
# its 10 digits.
_MEXICO_OLD_MOBILE = re.compile(r"^521([0-9]{10})$")


def normalize_phone(raw: str | None) -> str | None:
    """A wa_id ("5219511234567") or an E.164 number as E.164 ("+529511234567"); None if it is not
    a phone number."""
    if not raw:
        return None
    digits = raw.removeprefix("+")
    if not _DIGITS.fullmatch(digits):
        return None
    mexican = _MEXICO_OLD_MOBILE.fullmatch(digits)
    if mexican:
        digits = "52" + mexican.group(1)
    number = "+" + digits
    return number if E164_PATTERN.fullmatch(number) else None


def normalize_bsuid(raw: str | None) -> str | None:
    return raw if raw and BSUID_PATTERN.fullmatch(raw) else None


def recipient_fields(phone: str | None, bsuid: str | None) -> dict[str, str]:
    """How the Graph API addresses the patient: by phone when there is one (it takes precedence
    for Meta anyway), otherwise by BSUID."""
    if phone:
        return {"to": phone.removeprefix("+")}
    if bsuid:
        return {"recipient": bsuid}
    raise ValueError("el paciente no tiene teléfono ni BSUID: no hay a quién enviarle")
