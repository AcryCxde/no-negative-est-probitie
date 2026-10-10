from pathlib import Path

import pytest

from seabattle.config import DEFAULT_URL, ConfigError, Secret, load_config, read_env_file
from seabattle.model import BotMode, BuiltInBot


def test_defaults_are_safe_training() -> None:
    config = load_config(None, {"TOKEN": "abc"})
    assert (config.mode, config.opponent, config.url) == (
        BotMode.TRAINING,
        BuiltInBot.EASY,
        DEFAULT_URL,
    )


def test_token_is_required_and_never_printed() -> None:
    with pytest.raises(ConfigError):
        load_config(None, {})
    config = load_config(None, {"TOKEN": "super-secret"})
    assert "super-secret" not in repr(config)
    assert "super-secret" not in str(config.token)


def test_env_file_is_read_and_environment_wins(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\n\nTOKEN='from-file'\nBOT_MODE=RANKING\n", encoding="utf-8")
    assert read_env_file(env) == {"TOKEN": "from-file", "BOT_MODE": "RANKING"}
    config = load_config(env, {"BOT_MODE": "TRAINING"})
    assert config.token == Secret("from-file")
    assert config.mode is BotMode.TRAINING


@pytest.mark.parametrize(
    "url", ["ws://hackathon.example/ws", "http://x/y", "wss://", "hackathon.no-offence.ru"]
)
def test_insecure_urls_are_rejected(url: str) -> None:
    with pytest.raises(ConfigError):
        load_config(None, {"TOKEN": "t", "BOT_URL": url})


def test_plain_ws_is_allowed_for_loopback_only() -> None:
    assert load_config(None, {"TOKEN": "t", "BOT_URL": "ws://127.0.0.1:8080/x"}).url.startswith(
        "ws://"
    )


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(ConfigError):
        load_config(None, {"TOKEN": "t", "BOT_MODE": "TOURNAMENT"})


def test_match_limit_is_optional_and_must_be_positive() -> None:
    assert load_config(None, {"TOKEN": "t"}).max_matches is None
    assert load_config(None, {"TOKEN": "t", "BOT_MATCHES": "10"}).max_matches == 10
    for bad in ("0", "-3", "ten"):
        with pytest.raises(ConfigError):
            load_config(None, {"TOKEN": "t", "BOT_MATCHES": bad})
