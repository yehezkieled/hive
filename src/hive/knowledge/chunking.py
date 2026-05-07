"""Markdown-aware splitter for blueprint bodies.

Embedding a whole long blueprint as one vector loses precision: a 5-page
spec gets ranked against a 1-paragraph note in the same space. This
splitter cuts a blueprint into ~target_tokens chunks so each chunk
embeds the topic of *that section* rather than averaging the whole doc.

Design choices:
- ``len(text) // 4`` token estimate. Voyage ships no public tokeniser
  and ~4 chars/token is the standard heuristic for English; exact
  accuracy only matters near the 32K hard cap, which a single chunk
  never reaches.
- Markdown-aware: split on ``##``/``###`` headings first, then on
  paragraph breaks for sections that still exceed the budget.
- Code-fence safe: tracks open/close ``` and never splits inside.
- Overlap: each chunk after the first carries the tail of the previous
  chunk so a fact straddling a boundary still appears in one full chunk.
- Short-body fast path: anything under ``target_tokens * 1.6`` chars
  returns as a single chunk, preserving Sprint 11's behaviour for
  short notes (most personal blueprints).
"""

from __future__ import annotations

import re

_HEADING_RE = re.compile(r"^(#{2,3}) ", re.MULTILINE)
_FENCE_RE = re.compile(r"^```", re.MULTILINE)


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def _split_on_headings(body: str) -> list[str]:
    """Split a markdown body into sections at H2/H3 boundaries.

    Headings inside code fences are ignored — the regex sees them, but
    we walk the result and rejoin any sections whose start sits inside
    an open fence.
    """
    matches = list(_HEADING_RE.finditer(body))
    if not matches:
        return [body]

    boundaries = [0] + [m.start() for m in matches] + [len(body)]
    raw_sections = [body[boundaries[i] : boundaries[i + 1]] for i in range(len(boundaries) - 1)]
    raw_sections = [s for s in raw_sections if s.strip()]

    sections: list[str] = []
    fence_open = False
    for section in raw_sections:
        if fence_open and sections:
            sections[-1] += section
        else:
            sections.append(section)
        fence_open = (fence_open + len(_FENCE_RE.findall(section))) % 2 == 1
    return sections


def _split_on_paragraphs(section: str, target_chars: int) -> list[str]:
    """Pack paragraphs into chunks under ``target_chars``.

    Code fences are kept intact: a paragraph that opens a fence pulls in
    subsequent paragraphs until the closing fence is seen.
    """
    paragraphs = [p for p in section.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    fence_open = False

    for para in paragraphs:
        para_with_sep = (para + "\n\n") if not para.endswith("\n") else para
        in_fence = len(_FENCE_RE.findall(para)) % 2 == 1
        if buf and not fence_open and buf_len + len(para_with_sep) > target_chars:
            chunks.append("".join(buf).rstrip() + "\n")
            buf = []
            buf_len = 0
        buf.append(para_with_sep)
        buf_len += len(para_with_sep)
        if in_fence:
            fence_open = not fence_open

    if buf:
        chunks.append("".join(buf).rstrip() + "\n")
    return chunks


def _apply_overlap(chunks: list[str], overlap_chars: int) -> list[str]:
    """Prepend the tail of chunk N-1 to chunk N for context continuity."""
    if overlap_chars <= 0 or len(chunks) < 2:
        return chunks
    overlapped = [chunks[0]]
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1][-overlap_chars:]
        overlapped.append(prev_tail + chunks[i])
    return overlapped


def split_blueprint(
    body: str,
    *,
    target_tokens: int = 500,
    overlap_tokens: int = 50,
) -> list[str]:
    """Split a blueprint body into chunks of roughly ``target_tokens``.

    Empty / whitespace-only input returns ``[]``. Bodies under
    ``target_tokens * 1.6`` chars × 4 (the short-body fast path) return
    as a single chunk so short notes don't get fragmented.
    """
    if not body.strip():
        return []

    target_chars = target_tokens * 4
    overlap_chars = overlap_tokens * 4
    short_threshold = int(target_chars * 1.6)

    if len(body) <= short_threshold:
        return [body]

    sections = _split_on_headings(body)

    chunks: list[str] = []
    for section in sections:
        if _approx_tokens(section) <= int(target_tokens * 1.5):
            chunks.append(section)
        else:
            chunks.extend(_split_on_paragraphs(section, target_chars))

    chunks = [c for c in chunks if c.strip()]
    return _apply_overlap(chunks, overlap_chars)
