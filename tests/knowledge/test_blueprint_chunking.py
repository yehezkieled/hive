"""Unit tests for the markdown-aware blueprint splitter.

The splitter is pure-function (no I/O), so these tests are straight
input → output checks. Boundary correctness — code fences, overlap,
short-body fast path — is the load-bearing behaviour; ranking quality
is covered by ``test_blueprints_pgvector.py``.
"""

from __future__ import annotations

from hive.knowledge.chunking import split_blueprint


def test_short_body_returns_single_chunk_unchanged() -> None:
    """Bodies under the short threshold come back as one chunk verbatim."""
    body = "auth uses bearer tokens. expires after 1 hour."
    chunks = split_blueprint(body)
    assert chunks == [body]


def test_empty_body_returns_empty_list() -> None:
    assert split_blueprint("") == []
    assert split_blueprint("   \n\n  \t  ") == []


def test_long_markdown_splits_at_headings() -> None:
    """A body with multiple ## sections splits one chunk per section."""
    section_body = "lorem ipsum dolor sit amet. " * 200  # ~5400 chars
    body = (
        "# Top\n\nintro paragraph.\n\n"
        f"## Section A\n\n{section_body}\n\n"
        f"## Section B\n\n{section_body}\n\n"
        f"## Section C\n\n{section_body}\n"
    )
    chunks = split_blueprint(body, target_tokens=500, overlap_tokens=0)
    # Three ## sections + leading prose → at least 3 chunks. Each ##
    # section is just under 1.5×target so it should NOT sub-split.
    assert len(chunks) >= 3
    assert any("## Section A" in c for c in chunks)
    assert any("## Section B" in c for c in chunks)
    assert any("## Section C" in c for c in chunks)


def test_oversized_section_subsplits_on_paragraphs() -> None:
    """A single section above 1.5×target splits on paragraph breaks."""
    paragraph = "lorem ipsum dolor sit amet. " * 50  # ~1400 chars
    body = "## Big\n\n" + "\n\n".join(paragraph for _ in range(8))  # ~11K chars
    chunks = split_blueprint(body, target_tokens=500, overlap_tokens=0)
    # 11K chars / 2K-char target → at least 4 chunks for this one section.
    assert len(chunks) >= 4
    # No chunk should exceed roughly 2× target_chars (sloppy upper bound
    # to catch packing regressions, not exact size guarantee).
    target_chars = 500 * 4
    assert all(len(c) <= target_chars * 2 for c in chunks)


def test_code_fence_stays_intact() -> None:
    """A fenced code block must not be split mid-fence."""
    fence_body = "```python\n" + ("x = 1\n" * 400) + "```\n"  # ~2400 chars in fence
    body = (
        "## Intro\n\n"
        + ("filler paragraph. " * 100)
        + "\n\n## Code\n\n"
        + fence_body
        + "\n\n## After\n\n"
        + ("trailing text. " * 100)
    )
    chunks = split_blueprint(body, target_tokens=300, overlap_tokens=0)
    # The opening ``` and closing ``` must end up in the *same* chunk.
    fence_chunks = [c for c in chunks if "```python" in c]
    assert len(fence_chunks) == 1
    assert fence_chunks[0].count("```") >= 2  # opening + closing


def test_overlap_tail_appears_in_next_chunk() -> None:
    """Each chunk after the first prepends the previous chunk's tail.

    Body has no heading so the splitter falls straight into paragraph
    packing, which keeps chunk[0] large enough to slice ``overlap_chars``
    off its tail.
    """
    paragraph = "alpha bravo charlie delta echo foxtrot. " * 30  # ~1200 chars
    body = "\n\n".join(paragraph for _ in range(6))
    overlap_chars = 80  # 20 tokens × 4
    chunks = split_blueprint(body, target_tokens=300, overlap_tokens=20)
    assert len(chunks) >= 2
    # chunk[1] starts with chunk[0]'s last overlap_chars chars verbatim.
    assert chunks[1].startswith(chunks[0][-overlap_chars:])
