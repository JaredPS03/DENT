"""The model behind the bot: Claude through the official SDK, behind a small interface so tests
drive the turn with a scripted model.

Each call has its own deadline and one retry by the SDK (429, 5xx, connection). Whatever still
fails becomes ModelUnavailableError, and the turn answers with the fixed fallback message. No
content of the conversation is ever logged: only the request id and the kind of failure.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic

from app.bot.costs import TokenUsage
from app.core.config import Settings

logger = logging.getLogger(__name__)

# One answer is short: thinking plus a tool call fits with room to spare.
MAX_TOKENS = 8000
CALL_TIMEOUT_SECONDS = 25.0


class ModelUnavailableError(Exception):
    """The call did not produce an answer. `kind` is a key for the turn log, never content."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ModelResponse:
    stop_reason: str | None
    tool_calls: list[ToolCall]
    # The assistant's content blocks as the API returned them (thinking included), to send back
    # unchanged on the next call of the same turn.
    content: list[dict[str, Any]]
    usage: TokenUsage = field(default_factory=TokenUsage)


class BotModel(Protocol):
    name: str

    async def complete(
        self,
        system: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
    ) -> ModelResponse: ...


class ClaudeModel:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        if settings.anthropic_api_key is None:
            raise ValueError("ANTHROPIC_API_KEY is required for the bot")
        self.name = settings.bot_model
        # The key is passed explicitly: the SDK must not pick up another credential of the host.
        self._client = client or anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            max_retries=1,
            timeout=CALL_TIMEOUT_SECONDS,
        )

    async def complete(
        self,
        system: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
    ) -> ModelResponse:
        try:
            message = await self._client.messages.create(
                model=self.name,
                max_tokens=MAX_TOKENS,
                system=list(system),  # type: ignore[arg-type]
                tools=list(tools),  # type: ignore[arg-type]
                messages=list(messages),  # type: ignore[arg-type]
            )
        except anthropic.APITimeoutError:
            raise ModelUnavailableError("timeout") from None
        except anthropic.APIConnectionError:
            raise ModelUnavailableError("connection") from None
        except anthropic.RateLimitError:
            raise ModelUnavailableError("rate_limited") from None
        except anthropic.BadRequestError as error:
            # Our request is wrong (a schema, the history): worth an alert, never retried as is.
            logger.error("bot.model_bad_request", extra={"request_id": error.request_id})  # noqa: TRY400
            raise ModelUnavailableError("bad_request") from None
        except anthropic.APIStatusError as error:
            logger.warning(
                "bot.model_error", extra={"status": error.status_code, "request_id": error.request_id}
            )
            raise ModelUnavailableError("api_error") from None
        usage = message.usage
        return ModelResponse(
            stop_reason=message.stop_reason,
            tool_calls=[
                ToolCall(block.id, block.name, dict(block.input))
                for block in message.content
                if block.type == "tool_use"
            ],
            content=[block.to_dict() for block in message.content],
            usage=TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
        )

    async def close(self) -> None:
        await self._client.close()
