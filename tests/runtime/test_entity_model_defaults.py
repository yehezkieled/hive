from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from hive.commands.dispatch import CommandDispatcher
from hive.models.entity import Entity, parse_personality
from hive.process.lifecycle_manager import _adapter_config_from_entity
from hive.process.manager import ProcessManager
from hive.runtime.harness import RunMode, RuntimeContext
from hive.runtime.registry import default_specs


class EntityModelDefaultsTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Mock(upsert=AsyncMock())
        self.manager = ProcessManager(
            router=Mock(), entity_store=self.store, personalities_dir=self.root
        )

    def assert_claude_model(self, entity, expected):
        ctx = RuntimeContext(_adapter_config_from_entity(entity))
        for mode in (RunMode.HEADLESS, RunMode.PTY):
            adapter = default_specs()["claude"].build(mode, ctx)
            self.assertEqual(adapter._config.model, expected)
        argv = entity.build_cli_args()
        self.assertEqual(argv[argv.index("--model") + 1], expected)

    async def test_omitted_creation_models_reach_role_defaults(self):
        maestro = await self.manager.register_maestro("dev")
        lead = await self.manager.create_team("dev", "backend")
        self.assertEqual(maestro.model, "")
        self.assertEqual(lead.model, "")
        self.assert_claude_model(maestro, "claude-opus-5-5")
        self.assert_claude_model(lead, "claude-sonnet-5-5")
        self.assertEqual(self.store.upsert.await_args_list[-1].args[0].model, "")

    async def test_explicit_choices_survive_creation(self):
        maestro = await self.manager.register_maestro("dev", model="haiku")
        lead = await self.manager.create_team("dev", "backend", model="opus")
        self.assert_claude_model(maestro, "haiku")
        self.assert_claude_model(lead, "opus")

    async def test_generated_lead_personality_preserves_omission(self):
        await self.manager.register_maestro("dev")
        lead = await self.manager.create_team(
            "dev", "backend", display_name="Backend", personality="Be precise"
        )
        self.assertEqual(parse_personality(lead.personality_path).model, "")
        self.assertEqual(lead.model, "")
        self.assert_claude_model(lead, "claude-sonnet-5-5")

    async def test_new_maestro_flow_preserves_omission_and_personality_choice(self):
        dispatcher = CommandDispatcher.__new__(CommandDispatcher)
        dispatcher.process_manager = self.manager
        dispatcher.personalities_dir = self.root
        dispatcher._pending_new = {}
        await dispatcher._execute_new("maestro", "fresh", actor="user")
        await dispatcher._advance_new_flow("user", "Manage projects")
        await dispatcher._advance_new_flow("user", "Terse")
        maestro = self.manager.entities["fresh"]
        self.assertEqual(maestro.model, "")
        self.assert_claude_model(maestro, "claude-opus-5-5")
        (self.root / "chosen.md").write_text(
            "## Identity\n- **Name**: chosen\n- **Role**: maestro\n"
            "- **Model**: haiku\n\n## System Prompt\nBe precise\n"
        )
        await dispatcher._execute_new("maestro", "chosen")
        self.assert_claude_model(self.manager.entities["chosen"], "haiku")

    def test_existing_opus_selection_is_preserved(self):
        self.assert_claude_model(Entity(name="dev", role="maestro", model="opus"), "opus")
