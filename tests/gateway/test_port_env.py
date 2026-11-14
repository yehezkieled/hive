"""``HIVE_GATEWAY_PORT`` falls back to the default when blank or not a number."""

from __future__ import annotations

import pytest

from hive.gateway.settings import DEFAULT_PORT, port_from_env


@pytest.mark.parametrize(
    ("env", "port"),
    [
        ({}, DEFAULT_PORT),
        ({"HIVE_GATEWAY_PORT": ""}, DEFAULT_PORT),
        ({"HIVE_GATEWAY_PORT": "  "}, DEFAULT_PORT),
        ({"HIVE_GATEWAY_PORT": "abc"}, DEFAULT_PORT),
        ({"HIVE_GATEWAY_PORT": "8481"}, 8481),
        ({"HIVE_GATEWAY_PORT": " 9000 "}, 9000),
    ],
)
def test_port_from_env(env: dict[str, str], port: int) -> None:
    assert port_from_env(env) == port


@pytest.mark.parametrize("value", ["", "abc"])
def test_desk_and_bot_start_on_the_default_port(monkeypatch, value: str) -> None:
    import uvicorn

    from hive.gateway import __main__ as desk

    monkeypatch.setenv("HIVE_GATEWAY_PORT", value)
    seen: dict[str, int] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(port=kw["port"]))
    monkeypatch.setattr(desk, "create_app", lambda: object())
    desk.main()
    assert seen["port"] == DEFAULT_PORT


class _StopError(Exception):
    pass


@pytest.mark.parametrize("value", ["", "abc"])
def test_bot_starts_on_the_default_port(monkeypatch, value: str) -> None:
    from hive.telegram import remote

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:" + "a" * 35)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "42")
    monkeypatch.setenv("HIVE_GATEWAY_PORT", value)
    seen: dict[str, int] = {}

    def fake_bot(*_args, desk_port: int, **_kw):
        seen["port"] = desk_port
        raise _StopError

    monkeypatch.setattr(remote, "RemoteBot", fake_bot)
    with pytest.raises(_StopError):
        remote.main()
    assert seen["port"] == DEFAULT_PORT
