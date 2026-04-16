"""Tests for file-based blueprint storage and search."""

from __future__ import annotations

from pathlib import Path

from hive.knowledge.blueprints import BlueprintStore


class TestBlueprintSave:
    def test_save_creates_file(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        path = store.save("Auth Flow", "Steps to implement auth", ["auth", "guide"])

        assert path.exists()
        assert path.suffix == ".md"

    def test_save_content_has_frontmatter(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        path = store.save("Auth Flow", "Steps to implement auth", ["auth", "guide"])
        content = path.read_text()

        assert content.startswith("---\n")
        assert "title: Auth Flow" in content
        assert "tags:" in content
        assert "- auth" in content
        assert "Steps to implement auth" in content


class TestBlueprintLoad:
    def test_load_roundtrip(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        path = store.save("Auth Flow", "Steps to implement auth", ["auth"])
        bp = store.load(path)

        assert bp["title"] == "Auth Flow"
        assert bp["body"] == "Steps to implement auth"
        assert "auth" in bp["tags"]

    def test_load_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        assert store.load(tmp_path / "nope.md") is None


class TestBlueprintList:
    def test_list_empty(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        assert store.list_all() == []

    def test_list_returns_all(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        store.save("First", "body one", [])
        store.save("Second", "body two", [])

        items = store.list_all()
        titles = {item["title"] for item in items}
        assert titles == {"First", "Second"}


class TestBlueprintSearch:
    def test_search_by_title(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        store.save("Auth Flow", "steps for auth", ["auth"])
        store.save("Deploy Guide", "how to deploy", ["ops"])

        results = store.search("auth")
        assert len(results) == 1
        assert results[0]["title"] == "Auth Flow"

    def test_search_by_body(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        store.save("Guide", "deploy to production server", [])

        results = store.search("production")
        assert len(results) == 1

    def test_search_case_insensitive(self, tmp_path: Path) -> None:
        store = BlueprintStore(tmp_path)
        store.save("Auth Flow", "steps", [])

        assert len(store.search("AUTH")) == 1
        assert len(store.search("auth")) == 1
