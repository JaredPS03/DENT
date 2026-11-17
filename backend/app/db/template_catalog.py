"""The clinic's catalog of WhatsApp templates, kept in sync with Meta's notices (milestone 1D).

A template Meta pauses, rejects or moves to marketing stops being "approved as utility", and the
code that sends it (recordatorio_cita, retomar_conversacion) looks for exactly that.
"""

from psycopg import AsyncConnection

from app.whatsapp.account import TemplateCategoryNotice, TemplateStatusNotice


async def apply_status(conn: AsyncConnection, clinic_id: object, notice: TemplateStatusNotice) -> int:
    """Updates the template's status; a template the catalog did not have yet is added when Meta
    says its category. Returns how many rows changed."""
    if notice.status is None:
        return 0
    cursor = await conn.execute(
        """
        update public.templates
        set status = %(status)s, status_reason = %(reason)s,
            meta_template_id = coalesce(%(meta_id)s, meta_template_id),
            category = coalesce(%(category)s, category)
        where (name = %(name)s and language = %(language)s)
           or (%(meta_id)s::text is not null and meta_template_id = %(meta_id)s)
        """,
        _values(notice),
    )
    if cursor.rowcount or notice.category is None or not _valid_name(notice.name):
        return cursor.rowcount
    cursor = await conn.execute(
        """
        insert into public.templates
          (clinic_id, name, category, language, status, status_reason, meta_template_id)
        values (%(clinic_id)s, %(name)s, %(category)s, %(language)s, %(status)s, %(reason)s, %(meta_id)s)
        on conflict (clinic_id, name, language) do nothing
        """,
        {**_values(notice), "clinic_id": clinic_id},
    )
    return cursor.rowcount


async def apply_category(conn: AsyncConnection, notice: TemplateCategoryNotice) -> int:
    """Only a change Meta already made; one it announces for later is just an alert."""
    if not notice.done or notice.new_category is None:
        return 0
    cursor = await conn.execute(
        """
        update public.templates set category = %(category)s
        where (name = %(name)s and language = %(language)s)
           or (%(meta_id)s::text is not null and meta_template_id = %(meta_id)s)
        """,
        {
            "category": notice.new_category,
            "name": notice.name,
            "language": notice.language,
            "meta_id": notice.meta_template_id,
        },
    )
    return cursor.rowcount


async def clinic_display_phone(conn: AsyncConnection) -> str | None:
    cursor = await conn.execute("select whatsapp_display_phone from public.clinics")
    row = await cursor.fetchone()
    return row[0] if row else None


def _values(notice: TemplateStatusNotice) -> dict[str, object]:
    return {
        "status": notice.status,
        "reason": notice.reason,
        "meta_id": notice.meta_template_id,
        "category": notice.category,
        "name": notice.name,
        "language": notice.language,
    }


def _valid_name(name: str) -> bool:
    """The catalog's own rule for names (lowercase letters, digits and underscores)."""
    return bool(name) and len(name) <= 512 and all(c.islower() or c.isdigit() or c == "_" for c in name)
