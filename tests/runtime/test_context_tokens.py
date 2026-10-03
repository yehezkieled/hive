from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock, patch

import hive.config as config
from hive.models.entity import Entity
from hive.process.manager import ProcessManager
from hive.runtime.adapter_config import AdapterConfig
from hive.runtime.claude_adapter import ClaudeAdapter
from hive.runtime.claude_headless import ClaudeHeadlessAdapter
from hive.runtime.codex_adapter import CodexAdapter
from hive.runtime.pi_adapter import PiAdapter
from tests.runtime.fake_cli import jsonl, make_fake_cli


class ContextTokensTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    async def test_pi_final_context_controls_compaction_and_preserves_totals(self):
        for cached, should_compact in ((30000, False), (60000, True)):
            with self.subTest(cached=cached):
                output = jsonl(
                    *[
                        {
                            "type": "message_end",
                            "message": {
                                "role": "assistant",
                                "stopReason": "stop",
                                "content": [{"type": "text", "text": "done"}],
                                "usage": {
                                    "input": 1000,
                                    "cacheRead": count,
                                    "cacheWrite": 50,
                                    "output": 10,
                                },
                            },
                        }
                        for count in (90000, cached)
                    ]
                )
                binary = make_fake_cli(self.root, "pi", stdout=output)
                adapter = PiAdapter(AdapterConfig(name="lead"), self.root)
                token_store = Mock(record=AsyncMock())
                manager = ProcessManager(
                    router=Mock(has_pending=Mock(return_value=False)), token_store=token_store
                )
                manager._entities["lead"] = Entity(name="lead", role="lead")
                manager._get_or_create_adapter = AsyncMock(return_value=adapter)
                manager.compact_entity = AsyncMock()
                manager._notify = AsyncMock()
                manager._audit = AsyncMock()
                with (
                    patch.object(config, "PI_BINARY", str(binary)),
                    patch("hive.process.manager.mcp_servers_enabled", return_value=False),
                    patch("hive.process.manager.AUTO_RETRIEVE_ENABLED", False),
                    patch("hive.process.manager.AUTO_COMPACT_ENABLED", True),
                    patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
                ):
                    self.assertEqual(await manager.send_to_entity("lead", "go"), "done")
                usage = token_store.record.await_args.args[1]
                self.assertEqual(usage["context_tokens"], 1000 + cached + 50)
                self.assertEqual(usage["input_tokens"], 2000)
                self.assertEqual(usage["cache_read_input_tokens"], 90000 + cached)
                self.assertEqual(usage["cache_creation_input_tokens"], 100)
                self.assertEqual(manager.compact_entity.await_count, int(should_compact))
                if should_compact:
                    self.assertIn("61,050", manager._notify.await_args.args[0])
                    audit = next(
                        call
                        for call in manager._audit.await_args_list
                        if call.args[0] == "entity.auto_compact"
                    )
                    self.assertEqual(audit.kwargs["details"]["input_tokens"], 61050)

    async def test_claude_headless_uses_final_message_not_accounting_totals(self):
        output = jsonl(
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 1000,
                        "cache_read_input_tokens": 30000,
                        "cache_creation_input_tokens": 50,
                    }
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "usage": {"input_tokens": 9000, "cache_read_input_tokens": 90000},
            },
        )
        binary = make_fake_cli(self.root, "claude", stdout=output)
        with patch.object(config, "CLAUDE_BINARY", str(binary)):
            _, usage = await ClaudeHeadlessAdapter(
                AdapterConfig(model="haiku"), self.root
            ).send_turn("go")
        self.assertEqual(usage["context_tokens"], 31050)
        self.assertEqual(usage["input_tokens"], 9000)
        self.assertEqual(usage["cache_read_input_tokens"], 90000)

    async def test_claude_pty_reports_final_context(self):
        adapter = ClaudeAdapter(AdapterConfig(model="haiku"))
        adapter._pty = Mock(
            send=AsyncMock(
                return_value=(
                    "done",
                    {
                        "input_tokens": 1000,
                        "cache_read_input_tokens": 30000,
                        "cache_creation_input_tokens": 50,
                    },
                )
            )
        )
        _, usage = await adapter.send_turn("go")
        self.assertEqual(usage["context_tokens"], 31050)
        self.assertEqual(usage["input_tokens"], 1000)

    async def test_codex_accounting_totals_do_not_trigger_compaction(self):
        for input_tokens in (31000, 63000):
            with self.subTest(input_tokens=input_tokens):
                output = jsonl(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": input_tokens,
                            "cached_input_tokens": 30000,
                        },
                    }
                )
                binary = make_fake_cli(self.root, "codex", stdout=output)
                adapter = CodexAdapter(AdapterConfig(), self.root)
                token_store = Mock(record=AsyncMock())
                manager = ProcessManager(
                    router=Mock(has_pending=Mock(return_value=False)), token_store=token_store
                )
                manager._entities["lead"] = Entity(name="lead", role="lead")
                manager._get_or_create_adapter = AsyncMock(return_value=adapter)
                manager.compact_entity = AsyncMock()
                manager._notify = AsyncMock()
                manager._audit = AsyncMock()
                with (
                    patch.object(config, "CODEX_BINARY", str(binary)),
                    patch("hive.process.manager.mcp_servers_enabled", return_value=False),
                    patch("hive.process.manager.AUTO_RETRIEVE_ENABLED", False),
                    patch("hive.process.manager.AUTO_COMPACT_ENABLED", True),
                    patch("hive.process.manager.AUTO_COMPACT_THRESHOLD", 50000),
                ):
                    await manager.send_to_entity("lead", "go")
                usage = token_store.record.await_args.args[1]
                self.assertIsNone(usage["context_tokens"])
                self.assertEqual(usage["input_tokens"], input_tokens - 30000)
                self.assertEqual(usage["cache_read_input_tokens"], 30000)
                manager.compact_entity.assert_not_awaited()
                manager._notify.assert_not_awaited()
                self.assertFalse(
                    any(
                        call.args[0] == "entity.auto_compact"
                        for call in manager._audit.await_args_list
                    )
                )

    async def test_claude_aggregate_without_final_message_has_no_context(self):
        output = jsonl(
            {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "usage": {"input_tokens": 63000},
            }
        )
        binary = make_fake_cli(self.root, "claude", stdout=output)
        with patch.object(config, "CLAUDE_BINARY", str(binary)):
            _, usage = await ClaudeHeadlessAdapter(
                AdapterConfig(model="haiku"), self.root
            ).send_turn("go")
        self.assertIsNone(usage["context_tokens"])
        self.assertEqual(usage["input_tokens"], 63000)
