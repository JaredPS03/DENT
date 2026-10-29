"""Operator commands.

    uv run python -m app.cli send-test-message --to +5295XXXXXXXX
    uv run python -m app.cli replay-dead-letters [--limit 100]

send-test-message sends Meta's `hello_world` template (or free text with --text, only inside the
24 h window) from WHATSAPP_TEST_PHONE_NUMBER_ID, only to numbers listed in
WHATSAPP_ALLOWED_TEST_NUMBERS, and never in production. This is the first half of the Phase 0 gate;
docs/puerta-fase-0.md has the whole check, including receiving the reply through the webhook.

replay-dead-letters puts the jobs that failed all their attempts back in the queue, once the cause
is fixed. Jobs carry ids only and are idempotent, so a replay never duplicates a message.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import httpx

from app.core.config import Environment, SettingsError, load_settings
from app.core.logs import mask_message_id, mask_phone
from app.db.connection import Database, create_pool
from app.jobs.queue import replay_dead_letters
from app.whatsapp.client import (
    NotSentError,
    OutcomeUnknownError,
    RecipientNotAllowedError,
    WhatsAppApiError,
    WhatsAppClient,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Comandos de Axon Dental")
    commands = parser.add_subparsers(dest="command", required=True)

    send = commands.add_parser("send-test-message", help="Manda un mensaje de prueba por la API de WhatsApp")
    send.add_argument("--to", required=True, help="Número destino en E.164, por ejemplo +5295...")
    send.add_argument("--template", default="hello_world", help="Plantilla aprobada (default: hello_world)")
    send.add_argument("--language", default="en_US", help="Idioma de la plantilla (default: en_US)")
    send.add_argument("--text", help="Texto libre en vez de plantilla; solo dentro de la ventana de 24 h")
    send.add_argument("--phone-number-id", help="Número que envía; por defecto WHATSAPP_TEST_PHONE_NUMBER_ID")

    replay = commands.add_parser(
        "replay-dead-letters",
        help="Devuelve a la cola los trabajos que fallaron todos sus intentos (después de corregir la causa)",
    )
    replay.add_argument("--limit", type=int, default=100, help="Cuántos devolver como máximo (default: 100)")
    return parser


async def _replay_dead_letters(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= 1000:
        print("--limit va de 1 a 1000.")
        return 2
    settings = load_settings()
    pool = create_pool(settings)
    await pool.open(wait=True, timeout=10)
    try:
        moved = await replay_dead_letters(Database(pool), args.limit)
    finally:
        await pool.close()
    print(f"{moved} trabajo(s) de vuelta en la cola; el worker los toma en cuanto esté corriendo.")
    return 0


async def _send_test_message(args: argparse.Namespace) -> int:
    settings = load_settings()
    if settings.environment is Environment.PRODUCTION:
        # In production every message goes out through the bot, which logs it and counts it
        # against the clinic's cap.
        print("Este comando es solo para desarrollo y staging; en producción no envía nada.")
        return 2
    phone_number_id = args.phone_number_id or settings.whatsapp_test_phone_number_id
    if not phone_number_id:
        print("Falta el número que envía: usa --phone-number-id o WHATSAPP_TEST_PHONE_NUMBER_ID.")
        return 2
    async with httpx.AsyncClient() as http:
        client = WhatsAppClient(settings, http)
        if args.text:
            sent = await client.send_text(phone_number_id, args.to, args.text)
        else:
            sent = await client.send_template(phone_number_id, args.to, args.template, args.language)
    # The full id carries the recipient's number: only its tail is shown.
    print(f"Mensaje enviado a {mask_phone(args.to)}. Id de Meta: {mask_message_id(sent.meta_message_id)}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:  # noqa: PLR0911 - one exit code per outcome
    args = _parser().parse_args(argv)
    try:
        if args.command == "send-test-message":
            return asyncio.run(_send_test_message(args))
        if args.command == "replay-dead-letters":
            return asyncio.run(_replay_dead_letters(args))
    except (SettingsError, RecipientNotAllowedError, ValueError) as exc:
        print(f"No se envió: {exc}")
        return 2
    except WhatsAppApiError as exc:
        print(f"No se envió: {exc}. Revisa el token, el número y que el destino esté verificado en Meta.")
        return 1
    except NotSentError as exc:
        print(f"No se envió: no hubo conexión con Meta ({exc}).")
        return 1
    except OutcomeUnknownError:
        print("Meta no contestó a tiempo: revisa en el teléfono si llegó antes de volver a intentar.")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
