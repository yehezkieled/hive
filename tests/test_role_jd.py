"""Tests for the role-JD loader that replaces the static MESSAGING_PROMPT
and AUTONOMY_PROMPT constants with markdown files at personalities/role-*.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestLoadRoleJd:
    """``load_role_jd(role, base_dir)`` reads ``base_dir/role-<role>.md``
    and returns its contents. Reads are cached per ``(role, base_dir)``
    so the file system isn't hit on every entity spawn.
    """

    def test_loads_maestro_jd(self, tmp_path: Path) -> None:
        (tmp_path / "role-maestro.md").write_text("# Maestro role\nspawn_team allowed.")
        from hive.process.loops import load_role_jd

        text = load_role_jd("maestro", base_dir=tmp_path)

        assert "Maestro role" in text
        assert "spawn_team" in text

    def test_loads_lead_jd(self, tmp_path: Path) -> None:
        (tmp_path / "role-lead.md").write_text("# Lead role\nspawn_worker allowed.")
        from hive.process.loops import load_role_jd

        text = load_role_jd("lead", base_dir=tmp_path)

        assert "Lead role" in text
        assert "spawn_worker" in text

    def test_loads_worker_jd(self, tmp_path: Path) -> None:
        (tmp_path / "role-worker.md").write_text("# Worker role\nNo autonomy actions.")
        from hive.process.loops import load_role_jd

        text = load_role_jd("worker", base_dir=tmp_path)

        assert "Worker role" in text
        assert "No autonomy" in text

    def test_unknown_role_raises_value_error(self, tmp_path: Path) -> None:
        from hive.process.loops import load_role_jd

        with pytest.raises(ValueError, match="role"):
            load_role_jd("emperor", base_dir=tmp_path)

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        from hive.process.loops import load_role_jd

        with pytest.raises(FileNotFoundError):
            load_role_jd("maestro", base_dir=tmp_path)

    def test_caches_reads(self, tmp_path: Path) -> None:
        """Second call returns cached content even after the file changes,
        so spawn-time reads stay cheap. (Cache invalidation is out of scope —
        role JDs are static within a process lifetime.)
        """
        path = tmp_path / "role-maestro.md"
        path.write_text("first version")
        from hive.process.loops import load_role_jd

        first = load_role_jd("maestro", base_dir=tmp_path)
        path.write_text("second version")
        second = load_role_jd("maestro", base_dir=tmp_path)

        assert first == second == "first version"


class TestRepoLevelRoleFiles:
    """Sanity check: the three role-JD files exist on disk and contain
    the action vocabulary that the entity prompts rely on.
    """

    def test_role_files_exist(self) -> None:
        repo_root = Path(__file__).parent.parent
        for role in ("maestro", "lead", "worker", "vault"):
            f = repo_root / "personalities" / f"role-{role}.md"
            assert f.exists(), f"missing {f}"

    def test_maestro_jd_documents_spawn_team(self) -> None:
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-maestro.md").read_text()
        assert "spawn_team" in text
        assert "hive_actions" in text

    def test_lead_jd_documents_spawn_worker(self) -> None:
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-lead.md").read_text()
        assert "spawn_worker" in text
        assert "hive_actions" in text

    def test_worker_jd_omits_spawn_actions(self) -> None:
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-worker.md").read_text()
        assert "hive_actions" in text
        assert "spawn_team" not in text
        assert "spawn_worker" not in text

    def test_vault_jd_documents_request_payment(self) -> None:
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-vault.md").read_text()
        assert "request_payment" in text
        assert "idempotency_key" in text
        assert "hive_actions" in text

    def test_vault_jd_omits_spawn_actions(self) -> None:
        """Vault is locked-down: no spawn / kill autonomy."""
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-vault.md").read_text()
        assert "spawn_team" not in text
        assert "spawn_worker" not in text
        assert "kill_entity" not in text

    def test_loads_vault_jd(self, tmp_path: Path) -> None:
        (tmp_path / "role-vault.md").write_text("# Vault role\nrequest_payment only.")
        from hive.process.loops import load_role_jd

        text = load_role_jd("vault", base_dir=tmp_path)
        assert "Vault role" in text
        assert "request_payment" in text
