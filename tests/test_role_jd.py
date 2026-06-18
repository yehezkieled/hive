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

    def test_worker_role_raises_value_error(self, tmp_path: Path) -> None:
        """Ticket 018 retired the Worker entity: ``worker`` is no longer a
        valid role, so ``load_role_jd("worker")`` raises like any unknown
        role (it is not in ``_VALID_ROLES``).
        """
        from hive.process.loops import load_role_jd

        with pytest.raises(ValueError, match="role"):
            load_role_jd("worker", base_dir=tmp_path)

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
        for role in ("maestro", "lead", "vault"):
            f = repo_root / "personalities" / f"role-{role}.md"
            assert f.exists(), f"missing {f}"

    def test_maestro_jd_documents_spawn_team(self) -> None:
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-maestro.md").read_text()
        assert "spawn_team" in text
        assert "hive_actions" in text
        # Worker creation is retired on all paths (Ticket 016, ADR 0013);
        # kill_entity stays — maestros still kill leads.
        assert "spawn_worker" not in text
        assert "kill_entity" in text

    def test_maestro_jd_teaches_self_alias(self) -> None:
        """The maestro JD teaches the downward addressing alias (Ticket 031):
        a maestro addresses its freshly-spawned lead as ``self.<team>`` — no
        dotted name to remember or invent, mirroring the lead→maestro alias.
        """
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-maestro.md").read_text()
        assert "self.<team_name>" in text
        assert "no name needed" in text

    def test_maestro_jd_lists_pattern_menu_not_recipe(self) -> None:
        """Ticket 034 / ADR 0020 (D2 asymmetric depth): the maestro JD gets
        only the pattern MENU — names + when-to-use, so it can name a pattern
        in the contract — never the executable recipe. The full skeleton
        (debaters blind to each other, the `parallel(` script) is lead-only.
        """
        repo_root = Path(__file__).parent.parent
        flat = " ".join((repo_root / "personalities" / "role-maestro.md").read_text().split())
        # The menu: the name is present and the maestro is told it delegates it.
        assert "## Interaction patterns" in flat
        assert "debate" in flat
        assert "the lead runs it" in flat
        # Asymmetric depth: the executable recipe details are NOT here.
        assert "blind to each other" not in flat
        assert "parallel(" not in flat

    def test_maestro_names_pattern_but_only_lead_runs_it(self) -> None:
        """Ticket 034 / ADR 0020 (D3): the maestro NAMES a pattern; only the
        lead RUNS it. The menu says the maestro cannot drive a Workflow, and the
        tool policy enforces exactly that (Workflow stays in the maestro
        denylist, absent from the lead's). This guards the doc and the
        enforcement from drifting apart — the whole reason Lead-only execution
        is free (no denylist change).
        """
        from hive.process.tool_policy import role_tool_denylist

        repo_root = Path(__file__).parent.parent
        flat = " ".join((repo_root / "personalities" / "role-maestro.md").read_text().split())
        assert "cannot drive a Workflow" in flat  # the doc
        assert "Workflow" in role_tool_denylist("maestro")  # the enforcement agrees
        assert "Workflow" not in role_tool_denylist("lead")  # the lead may run it

    def test_lead_jd_documents_workflow_leaf_path(self) -> None:
        """The lead JD teaches the Workflow leaf engine (ADR 0010):
        fan out via the Workflow tool, sync-wait on TaskOutput, isolate
        same-file writers per worktree, cancel with TaskStop — and keeps
        the hive_actions reporting contract. The spawn_worker legacy
        path is gone (Ticket 016, ADR 0013): Worker creation is retired
        on all paths, so the JD must not teach the verb.
        """
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-lead.md").read_text()
        assert "Workflow" in text
        assert "TaskOutput" in text
        assert "TaskStop" in text
        assert "isolation" in text
        assert "spawn_worker" not in text  # retired by 016 (ADR 0013)
        assert "hive_actions" in text

    def test_lead_jd_rules_failure_enumeration(self) -> None:
        """Ticket 016 design D4 rule 1: a failed/unusable leaf result is
        retried once with a sharpened prompt; still failing, it is named
        explicitly in the synthesis to the maestro — never silently
        dropped (replaces the old path's ``report_failure`` escalation).
        """
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-lead.md").read_text()
        assert "never silently drop" in text
        assert "sharpened prompt" in text

    def test_lead_jd_rules_bounded_fanout_distilled_results(self) -> None:
        """Ticket 016 design D4 rule 2: ~10–20 agents per Workflow run,
        bigger jobs as sequential runs; leaf agents return schema-shaped
        summaries, never full dumps — the sync-wait returns everything
        into the lead's context (compaction + 5-hour quota pressure).
        """
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-lead.md").read_text()
        assert "10–20 agents" in text
        assert "sequential runs" in text
        assert "schema-shaped" in text
        assert "5-hour" in text

    def test_lead_jd_rules_tag_hygiene(self) -> None:
        """Ticket 016 design D4 rule 3: leaf-agent prompts must forbid
        emitting ``<hive_actions>`` or any literal angle-bracket tag,
        and the synthesis paraphrases leaf output rather than quoting
        raw tags (a nested tag rejects the whole turn).
        """
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-lead.md").read_text()
        assert "forbid emitting" in text
        assert "angle-bracket" in text
        assert "paraphrase" in text
        assert "never quote raw tags" in text

    def test_lead_jd_rules_worktree_release_granularity(self) -> None:
        """Ticket 016 design D5: worktree mode follows release
        granularity, not parallelism. One deliverable split for speed →
        default: agents edit the lead's worktree on disjoint files, one
        commit, one PR. Independently-shippable slices →
        ``isolation: 'worktree'`` per agent + per-slice PRs. Escape-hatch
        isolation for parallel same-file edits → the lead merges the
        agent branch back and removes the worktree in the same turn.
        """
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-lead.md").read_text()
        assert "release granularity" in text
        assert "disjoint files" in text
        assert "one commit, one PR" in text
        assert "Independently-shippable" in text
        assert "isolation: 'worktree'" in text
        assert "in the same turn" in text

    def test_lead_jd_teaches_maestro_alias(self) -> None:
        """The lead JD teaches the addressing alias (Ticket 023, design D2):
        a lead addresses its maestro as ``"maestro"`` — no dotted name to
        remember or invent.
        """
        repo_root = Path(__file__).parent.parent
        text = (repo_root / "personalities" / "role-lead.md").read_text()
        assert '"to": "maestro"' in text
        assert "no name needed" in text

    def test_lead_jd_documents_debate_pattern(self) -> None:
        """Ticket 034 / ADR 0020: the lead JD carries the FULL `debate`
        interaction-pattern recipe — pushed at spawn via load_role_jd, the
        only delivery path with a spawn-time guarantee. The recipe must
        teach: the named section, the independence rule (debaters blind to
        each other), the embedded Workflow skeleton, and the result shape.
        """
        repo_root = Path(__file__).parent.parent
        # Normalise whitespace: markdown wraps prose across lines, so assert
        # against the collapsed text rather than raw substrings.
        flat = " ".join((repo_root / "personalities" / "role-lead.md").read_text().split())
        assert "## Interaction patterns" in flat
        assert "debate" in flat
        assert "blind to each other" in flat  # the independence rule
        assert "judge" in flat
        assert "one round, one answer per agent" in flat
        assert "parallel(" in flat  # the embedded Workflow skeleton
        assert "positions" in flat and "verdict" in flat  # the result shape

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
