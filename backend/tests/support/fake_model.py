"""Models for tests: a scripted one that answers what each test says, and one that echoes what the
patient wrote through `responder`, to test the round trip without calling an API."""

import re
from collections.abc import Callable, Sequence
from itertools import count
from typing import Any

from app.bot.costs import TokenUsage
from app.bot.model import ModelResponse, ModelUnavailableError, ToolCall

_IDS = count(1)
Step = ModelResponse | Exception | Callable[[Sequence[dict[str, Any]]], ModelResponse]


def tool_use(name: str, usage: TokenUsage | None = None, **arguments: Any) -> ModelResponse:
    """An answer that calls one tool."""
    return tool_uses((name, arguments), usage=usage)


def tool_uses(*calls: tuple[str, dict[str, Any]], usage: TokenUsage | None = None) -> ModelResponse:
    made = [ToolCall(f"toolu_{next(_IDS)}", name, arguments) for name, arguments in calls]
    return ModelResponse(
        stop_reason="tool_use",
        tool_calls=made,
        content=[{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in made],
        usage=usage or TokenUsage(input_tokens=100, output_tokens=20, cache_read_tokens=3000),
    )


def answer(texto: str, **choices: list[str]) -> ModelResponse:
    return tool_use(
        "responder",
        texto=texto,
        botones=choices.get("botones", []),
        horarios=choices.get("horarios", []),
        lista=choices.get("lista", []),
    )


def plain_text(text: str) -> ModelResponse:
    """The model ended without calling a tool."""
    return ModelResponse(stop_reason="end_turn", tool_calls=[], content=[{"type": "text", "text": text}])


class ScriptedModel:
    """Returns its steps in order; a step may be a function of the messages it receives."""

    name = "claude-haiku-5-5"

    def __init__(self, *steps: Step) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        system: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
    ) -> ModelResponse:
        self.calls.append(
            {"system": list(system), "tools": list(tools), "messages": [dict(m) for m in messages]}
        )
        if not self.steps:
            raise ModelUnavailableError("script_exhausted")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(messages)
        return step


_TAG = re.compile(r"^<(mensaje_paciente|transcripcion)>(.*)</\1>")


class EchoModel:
    """Answers "Recibí: …" with what the patient wrote since the bot last spoke, like the echo bot
    of milestone 1B did, but through the real turn: prompt, history, tools and responder."""

    name = "fake-echo"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self,
        system: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
    ) -> ModelResponse:
        self.calls += 1
        last = messages[-1]["content"]
        assert isinstance(last, str), "the echo model answers on the first call of a turn"
        parts = [_spoken(line) for line in last.splitlines() if line.strip()]
        if len(parts) == 1:
            text = f"Recibí: {parts[0]}"
        else:
            text = f"Recibí {len(parts)} mensajes: " + " / ".join(parts)
        return answer(text)


def _spoken(line: str) -> str:
    if line.startswith("(Nota de voz que no se pudo transcribir)"):
        return "(nota de voz sin transcribir)"
    match = _TAG.match(line)
    if match is None:
        return line
    kind, text = match.groups()
    return f"(nota de voz) {text}" if kind == "transcripcion" else text
