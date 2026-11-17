"""The Claude client of the bot: where it sends patients' messages and how it reports failures."""

import pytest

from app.bot.model import API_BASE_URL, ClaudeModel
from tests.conftest import make_settings


def test_the_bot_only_talks_to_anthropic_even_if_the_environment_says_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://somewhere-else.example")
    model = ClaudeModel(make_settings(anthropic_api_key="sk-ant-test"))
    assert str(model._client.base_url).rstrip("/") == API_BASE_URL


def test_without_a_key_there_is_no_model() -> None:
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        ClaudeModel(make_settings())


def test_the_model_comes_from_settings() -> None:
    model = ClaudeModel(make_settings(anthropic_api_key="sk-ant-test", bot_model="claude-sonnet-5-5"))
    assert model.name == "claude-sonnet-5-5"
