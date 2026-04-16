"""File-based blueprint storage with YAML frontmatter.

Blueprints are Markdown files stored in a configurable directory. Each file
has YAML frontmatter (title, tags, created_at) and a body with the blueprint
content. Search is simple case-insensitive text matching — no embeddings
needed for the initial implementation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path


class BlueprintStore:
    """Manages blueprint files in a directory."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        title: str,
        content: str,
        tags: list[str],
    ) -> Path:
        """Save a blueprint as a YAML-frontmatter Markdown file.

        Returns the path to the created file.
        """
        slug = _slugify(title)
        path = self.directory / f"{slug}.md"

        # Avoid overwriting — append a counter if needed
        counter = 1
        while path.exists():
            path = self.directory / f"{slug}-{counter}.md"
            counter += 1

        tags_yaml = "\n".join(f"- {tag}" for tag in tags) if tags else "[]"
        now = datetime.now(UTC).isoformat()

        frontmatter = (
            f"---\n"
            f"title: {title}\n"
            f"tags:\n{tags_yaml}\n"
            f"created_at: {now}\n"
            f"---\n"
        )
        path.write_text(frontmatter + content)
        return path

    def load(self, path: Path) -> dict | None:
        """Parse a blueprint file into a dict with title, tags, body.

        Returns None if the file does not exist.
        """
        if not path.exists():
            return None

        text = path.read_text()
        return _parse_frontmatter(text, path)

    def list_all(self) -> list[dict]:
        """List all blueprints with their metadata."""
        results = []
        for path in sorted(self.directory.glob("*.md")):
            bp = self.load(path)
            if bp:
                results.append(bp)
        return results

    def search(self, query: str, limit: int = 5) -> list[dict]:
        """Case-insensitive text search across titles and body content."""
        query_lower = query.lower()
        matches = []
        for bp in self.list_all():
            title = bp.get("title", "").lower()
            body = bp.get("body", "").lower()
            if query_lower in title or query_lower in body:
                matches.append(bp)
                if len(matches) >= limit:
                    break
        return matches


def _slugify(text: str) -> str:
    """Convert a title to a filesystem-safe slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")


def _parse_frontmatter(text: str, path: Path) -> dict:
    """Extract YAML frontmatter and body from a Markdown string."""
    if not text.startswith("---\n"):
        return {"title": path.stem, "tags": [], "body": text, "path": str(path)}

    end = text.find("\n---\n", 4)
    if end == -1:
        return {"title": path.stem, "tags": [], "body": text, "path": str(path)}

    front = text[4:end]
    body = text[end + 5:]  # skip the closing ---\n

    title = path.stem
    tags: list[str] = []

    for line in front.splitlines():
        if line.startswith("title:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("- "):
            tags.append(line[2:].strip())

    return {"title": title, "tags": tags, "body": body.strip(), "path": str(path)}
