"""The AI part of a bot turn: the tool loop (bot-ia.md, steps 6 to 9).

At most MAX_TOOL_CALLS calls per turn, a deadline per model call and one for the whole loop.
The turn ends when the AI hands its answer with `responder`, or escalates as urgent (the code then
sends the fixed urgency message). If the AI stops without `responder` it is asked once to use it.
Anything else (an API failure, a refusal, a cut answer, too many calls, the deadline) ends in
"fallback": the orchestrator sends the fixed fallback message and hands the chat to a person.

Errors of our own systems (the database) are not caught here: the worker retries the turn.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from app.bot.costs import TokenUsage
from app.bot.model import BotModel, ModelUnavailableError
from app.bot.tools import TOOL_DEFINITIONS, ToolError, Tools

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 6
TURN_SECONDS = 60.0
NUDGE = "Entrega tu mensaje al paciente con la función responder."

Outcome = Literal["replied", "escalated", "urgent", "fallback"]


@dataclass
class AgentResult:
    outcome: Outcome
    usage: TokenUsage = field(default_factory=TokenUsage)
    model_calls: int = 0
    tools: list[str] = field(default_factory=list)
    error_kind: str | None = None


async def run_agent(  # noqa: PLR0911, PLR0912, PLR0913 - each way a turn can end is spelled out
    model: BotModel,
    tools: Tools,
    *,
    system: Sequence[dict[str, Any]],
    messages: list[dict[str, Any]],
    renew: Callable[[], Awaitable[None]],
    turn_seconds: float = TURN_SECONDS,
) -> AgentResult:
    result = AgentResult(outcome="fallback")
    deadline = time.monotonic() + turn_seconds
    nudged = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _failed(result, "turn_deadline")
        await renew()
        try:
            response = await asyncio.wait_for(model.complete(system, TOOL_DEFINITIONS, messages), remaining)
        except TimeoutError:
            return _failed(result, "turn_deadline")
        except ModelUnavailableError as unavailable:
            return _failed(result, unavailable.kind)
        result.usage += response.usage
        result.model_calls += 1
        if response.stop_reason == "refusal":
            return _failed(result, "refusal")
        if response.stop_reason == "max_tokens":
            return _failed(result, "max_tokens")
        messages.append({"role": "assistant", "content": response.content})
        if not response.tool_calls:
            if nudged:
                return _failed(result, "no_reply")
            nudged = True
            messages.append({"role": "user", "content": NUDGE})
            continue

        results: list[dict[str, Any]] = []
        # responder goes last: the other calls of the same answer happen first.
        for call in sorted(response.tool_calls, key=lambda c: c.name == "responder"):
            result.tools.append(call.name)
            if len(result.tools) > MAX_TOOL_CALLS:
                return _failed(result, "too_many_tools")
            try:
                output = await tools.run(call.name, call.input)
                results.append({"type": "tool_result", "tool_use_id": call.id, "content": output})
            except ToolError as refused:
                results.append(
                    {"type": "tool_result", "tool_use_id": call.id, "content": str(refused), "is_error": True}
                )
            state = tools.state
            if state.escalation is not None and state.escalation.priority == "urgent":
                result.outcome = "urgent"
                return result
            if state.reply is not None:
                result.outcome = "escalated" if state.escalation is not None else "replied"
                return result
        messages.append({"role": "user", "content": results})


def _failed(result: AgentResult, kind: str) -> AgentResult:
    logger.warning("bot.turn_failed", extra={"kind": kind, "model_calls": result.model_calls})
    result.outcome = "fallback"
    result.error_kind = kind
    return result
