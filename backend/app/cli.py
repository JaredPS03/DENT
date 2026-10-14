"""Operator commands.

    uv run python -m app.cli send-test-message --to +5295XXXXXXXX

Sends Meta's `hello_world` template (or free text with --text, only inside the 24 h window)
from WHATSAPP_TEST_PHONE_NUMBER_ID. Outside production it only sends to numbers listed in
WHATSAPP_ALLOWED_TEST_NUMBERS. This is the Phase 0 gate: send, then reply from your phone and
check that the webhook stored the reply.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

import httpx

from app.core.config import SettingsError, load_settings
from app.core.logs import mask_phone
from app.whatsapp.client import RecipientNotAllowedError, WhatsAppApiError, WhatsAppClient


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Comandos de Axon Dental")
    commands = parser.add_subparsers(dest="command", required=True)

    send = commands.add_parser("send-test-message", help="Manda un mensaje de prueba por la API de WhatsApp")
    send.add_argument("--to", required=True, help="Número destino en E.164, por ejemplo +5295...")
    send.add_argument("--template", default="hello_world", help="Plantilla aprobada (default: hello_world)")
    send.add_argument("--language", default="en_US", help="Idioma de la plantilla (default: en_US)")
    send.add_argument("--text", help="Texto libre en vez de plantilla; solo dentro de la ventana de 24 h")
    send.add_argument("--phone-number-id", help="Número que envía; por defecto WHATSAPP_TEST_PHONE_NUMBER_ID")
    return parser


async def _send_test_message(args: argparse.Namespace) -> int:
    settings = load_settings()
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
    print(f"Mensaje enviado a {mask_phone(args.to)}. Id de Meta: {sent.meta_message_id}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "send-test-message":
            return asyncio.run(_send_test_message(args))
    except (SettingsError, RecipientNotAllowedError, ValueError) as exc:
        print(f"No se envió: {exc}")
        return 2
    except WhatsAppApiError as exc:
        print(f"No se envió: {exc}. Revisa el token, el número y que el destino esté verificado en Meta.")
        return 1
    except httpx.HTTPError as exc:
        print(f"No se envió: no hubo conexión con Meta ({type(exc).__name__}).")
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
