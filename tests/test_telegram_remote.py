"""The standalone Telegram bot: config, allowlist, fixed commands, /health, no secret leaks.

Nothing here contacts Telegram: messages are fakes and the Application is never built,
except where ``start`` is patched to raise.
"""

from __future__ import annotations

import asyncio
import json
import logging
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from telegram.error import InvalidToken, NetworkError

from hive.telegram import remote
from hive.telegram.remote import (
    COMMANDS,
    EX_CONFIG,
    HELP_TEXT,
    REJECTED_MARK,
    BotConfigError,
    RedactingFormatter,
    RemoteBot,
    RemoteConfig,
    desk_probe,
    load_config,
    tailscale_state,
    token_digest,
)
from tests.gateway.test_wake import Fake, _Msg, _service

# Shaped like a BotFather token, and obviously not one.
FAKE_TOKEN = "123456789:" + "FAKE" * 9
OWNER = 42


def _env(token: str = FAKE_TOKEN, ids: str = str(OWNER)) -> dict[str, str]:
    return {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_ALLOWED_USER_IDS": ids}


# ---- config ------------------------------------------------------------------------------


def test_config_present() -> None:
    cfg = load_config(_env(ids=" 42, 7 ,42"))
    assert cfg.token == FAKE_TOKEN and cfg.allowed_user_ids == (42, 7)


@pytest.mark.parametrize("env", [{}, _env(token=""), _env(ids="")])
def test_config_absent(env: dict[str, str]) -> None:
    with pytest.raises(BotConfigError, match="must both be set"):
        load_config(env)


@pytest.mark.parametrize(
    ("env", "why"),
    [
        (_env(token="not-a-token"), "BotFather"),
        (_env(token=FAKE_TOKEN + " rm -rf /"), "BotFather"),
        (_env(token="123:short"), "BotFather"),
        (_env(ids="@me"), "numeric"),
        (_env(ids="42;7"), "numeric"),
        (_env(ids="-42"), "numeric"),
        (_env(ids="0"), "0"),
    ],
)
def test_config_malformed_never_echoes_a_value(env: dict[str, str], why: str) -> None:
    with pytest.raises(BotConfigError, match=why) as info:
        load_config(env)
    assert env["TELEGRAM_BOT_TOKEN"] not in str(info.value)
    assert "FAKE" not in str(info.value)


def test_config_rotated() -> None:
    new = "555555:" + "ROTATED" * 5
    assert load_config(_env()).token != load_config(_env(token=new)).token == new


def test_config_repr_hides_the_token() -> None:
    cfg = load_config(_env())
    assert FAKE_TOKEN not in repr(cfg) and FAKE_TOKEN not in str(cfg)


def test_only_the_fixed_commands_exist() -> None:
    assert set(COMMANDS) == {"status", "health", "wake", "restart", "watchdog", "help"}
    for name in COMMANDS:
        assert f"/{name}" in HELP_TEXT


# ---- the bot -----------------------------------------------------------------------------


def _bot(tmp_path: Path, state: str = "alive", allowed: tuple[int, ...] = (OWNER,)) -> RemoteBot:
    wake = _service(tmp_path, state, Fake())
    return RemoteBot(
        RemoteConfig(FAKE_TOKEN, allowed),
        wake=wake,
        desk_port=1,
        tailscale=None,
        state_dir=tmp_path / "bot-state",
    )


def _update(text: str | None, user_id: int = OWNER, chat_type: str = "private") -> SimpleNamespace:
    return SimpleNamespace(
        message=_Msg(text),
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(type=chat_type),
    )


async def _send(bot: RemoteBot, text: str | None, **kw: object) -> _Msg:
    update = _update(text, **kw)  # type: ignore[arg-type]
    await bot._handle_update(update, None)  # type: ignore[arg-type]
    return update.message


async def test_unknown_sender_gets_no_reply_and_is_logged_without_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bot = _bot(tmp_path)
    caplog.set_level(logging.INFO)
    secret_text = "/wake please, my password is hunter2"
    msg = await _send(bot, secret_text, user_id=999)
    assert msg.replies == []
    assert "999" in caplog.text and "not on the allowlist" in caplog.text
    assert "hunter2" not in caplog.text and "/wake" not in caplog.text


async def test_unknown_sender_logging_is_throttled(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bot = _bot(tmp_path)
    for _ in range(5):
        await _send(bot, "/status", user_id=999)
    assert caplog.text.count("not on the allowlist") == 1


async def test_allowed_user_in_a_group_gets_no_reply(tmp_path: Path) -> None:
    msg = await _send(_bot(tmp_path), "/status", chat_type="group")
    assert msg.replies == []


async def test_empty_allowlist_serves_nobody(tmp_path: Path) -> None:
    msg = await _send(_bot(tmp_path, allowed=()), "/status")
    assert msg.replies == []


@pytest.mark.parametrize(
    "text", ["hello", "/kill dev", "/m:vault pay 100", "/approve mode 1", "rm -rf /", None, "/"]
)
async def test_anything_else_only_gets_the_command_list(tmp_path: Path, text: str | None) -> None:
    bot = _bot(tmp_path)
    msg = await _send(bot, text)
    assert msg.replies == [(HELP_TEXT, None)]
    assert not bot.wake.audit_path.exists()


async def test_status_and_bot_suffix(tmp_path: Path) -> None:
    msg = await _send(_bot(tmp_path), "/status@hive_remote_bot")
    ((text, markup),) = msg.replies
    assert text.startswith("Firstmate alive") and markup is None


@pytest.mark.parametrize(
    "text",
    [
        "/status extra",
        "/health extra",
        "/wake extra",
        "/restart please restart",
        "/help extra",
    ],
)
async def test_commands_reject_extra_text_without_dispatching(tmp_path: Path, text: str) -> None:
    bot = _bot(tmp_path, "no-beat")
    msg = await _send(bot, text)
    assert msg.replies == [("That command takes no extra text", None)]
    assert not bot.wake.audit_path.exists()
    assert not bot._restart_confirms


async def test_restart_when_not_stuck_is_not_offered(tmp_path: Path) -> None:
    msg = await _send(_bot(tmp_path, "alive"), "/restart")
    ((text, markup),) = msg.replies
    assert "Restart is no longer offered" in text and markup is None


async def test_restart_when_stuck_asks_to_confirm(tmp_path: Path) -> None:
    msg = await _send(_bot(tmp_path, "no-beat"), "/restart")
    ((text, markup),) = msg.replies
    assert "Confirm?" in text and markup is not None


async def test_watchdog_takes_only_its_own_words(tmp_path: Path) -> None:
    bot = _bot(tmp_path)
    msg = await _send(bot, "/watchdog status")
    assert msg.replies[0][0].startswith("Automatic recovery: off")
    msg = await _send(bot, "/watchdog reboot")
    assert msg.replies[0][0] == "Usage: /watchdog on|off|status"


async def test_health_states_the_chain_in_plain_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot(tmp_path, "alive")
    monkeypatch.setattr(remote, "last_activity", lambda _home, _pid: 180.0)

    async def desk(_port: int) -> bool:
        return True

    async def ts(_binary: Path | None) -> str:
        return "connected"

    monkeypatch.setattr(remote, "desk_probe", desk)
    monkeypatch.setattr(remote, "tailscale_state", ts)
    msg = await _send(bot, "/health")
    ((text, _),) = msg.replies
    lines = text.splitlines()
    assert lines[0] == "Hive health"
    assert "Desk: up" in lines
    assert any(ln.startswith("Firstmate session: up (watcher beat") for ln in lines)
    assert any(ln.startswith("Last firstmate activity: 3m ago (at ") for ln in lines)
    assert "Automatic recovery: off" in lines
    assert "Tailscale on the PC: connected" in lines
    assert FAKE_TOKEN not in text


async def test_health_when_everything_is_down(tmp_path: Path) -> None:
    bot = _bot(tmp_path, "down")  # desk port 1 is closed, no tailscale binary
    msg = await _send(bot, "/health")
    text = msg.replies[0][0]
    assert "Desk: down" in text
    assert "Firstmate session: down" in text
    assert "Tailscale on the PC: unknown" in text


# ---- probes ------------------------------------------------------------------------------


async def test_desk_probe_up_and_down() -> None:
    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readline()
        writer.write(b"HTTP/1.1 403 Forbidden\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        assert await desk_probe(port) is True
    assert await desk_probe(port) is False


def _fake_tailscale(tmp_path: Path, payload: object) -> Path:
    data = tmp_path / "ts.json"
    data.write_text(json.dumps(payload) if not isinstance(payload, str) else payload)
    script = tmp_path / "tailscale"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.write(open({str(data)!r}).read())\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.mark.parametrize(
    ("payload", "want"),
    [
        ({"BackendState": "Running", "Self": {"Online": True}}, "connected"),
        ({"BackendState": "Running", "Self": {"Online": False}}, "running but offline"),
        ({"BackendState": "NeedsLogin"}, "needs a sign-in"),
        ({"BackendState": "Stopped"}, "not connected (state: stopped)"),
        ("not json", "unreadable"),
    ],
)
async def test_tailscale_state(tmp_path: Path, payload: object, want: str) -> None:
    assert want in await tailscale_state(_fake_tailscale(tmp_path, payload))


async def test_tailscale_state_missing_binary(tmp_path: Path) -> None:
    assert "could not run" in await tailscale_state(tmp_path / "nope")
    assert "not found" in await tailscale_state(None)


# ---- secrets in logs ---------------------------------------------------------------------


def test_redacting_formatter_strips_the_token_from_messages_and_tracebacks() -> None:
    fmt = RedactingFormatter("%(message)s", (FAKE_TOKEN,))
    try:
        raise RuntimeError(f"POST https://api.telegram.org/bot{FAKE_TOKEN}/getMe failed")
    except RuntimeError:
        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "url %s", (f"bot{FAKE_TOKEN}/x",), sys.exc_info()
        )
    out = fmt.format(record)
    assert FAKE_TOKEN not in out and "[bot token]" in out


def test_setup_logging_quiets_httpx_and_redacts(capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        remote.setup_logging(FAKE_TOKEN)
        assert logging.getLogger("httpx").level == logging.WARNING
        logging.getLogger("hive.telegram").warning("token is %s", FAKE_TOKEN)
        err = capsys.readouterr().err
        assert FAKE_TOKEN not in err and "[bot token]" in err
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)


# ---- start-up ----------------------------------------------------------------------------


async def test_rejected_token_exits_78_and_leaves_a_digest_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bot = _bot(tmp_path)

    async def start() -> None:
        raise InvalidToken("Unauthorized")

    monkeypatch.setattr(bot, "start", start)
    assert await remote.run(bot, asyncio.Event()) == EX_CONFIG
    mark = (tmp_path / "bot-state" / REJECTED_MARK).read_text().strip()
    assert mark == token_digest(FAKE_TOKEN) and FAKE_TOKEN not in mark
    assert FAKE_TOKEN not in caplog.text


async def test_rejected_token_still_exits_78_when_digest_cannot_be_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    bot = _bot(tmp_path)
    attempts = 0

    async def start() -> None:
        nonlocal attempts
        attempts += 1
        raise InvalidToken("Unauthorized")

    def fail_to_mark() -> None:
        raise OSError("disk full")

    monkeypatch.setattr(bot, "start", start)
    monkeypatch.setattr(bot, "mark_rejected", fail_to_mark)

    assert await remote.run(bot, asyncio.Event()) == EX_CONFIG
    assert attempts == 1
    assert "Could not persist the rejected-token marker" in caplog.text
    assert FAKE_TOKEN not in caplog.text


async def test_unreachable_telegram_is_retried_then_polls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = _bot(tmp_path)
    attempts: list[int] = []
    stop = asyncio.Event()

    async def start() -> None:
        attempts.append(1)
        if len(attempts) < 3:
            raise NetworkError("no route")
        stop.set()

    async def stopped() -> None:
        attempts.append(0)

    monkeypatch.setattr(remote, "RETRY_FIRST_S", 0.01)
    monkeypatch.setattr(bot, "start", start)
    monkeypatch.setattr(bot, "stop", stopped)
    assert await remote.run(bot, stop) == 0
    assert attempts == [1, 1, 1, 0]


def test_main_without_config_exits_78_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        assert remote.main() == EX_CONFIG
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)
    assert "not starting" in capsys.readouterr().err
