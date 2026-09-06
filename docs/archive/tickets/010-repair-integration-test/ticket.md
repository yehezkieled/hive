# 010 — Repair the stale lead-worker integration test

## What

`tests/integration/test_lead_worker_roundtrip.py` still depends on the
headless `claude -p` runtime that Ticket 007 deleted, and injects no
Fake adapter — so under the autouse `_no_real_pty` conftest guard it
raises `RuntimeError` on `send_to_entity` (or would drive a real
`claude`). Re-base it onto the mocked PTY / Fake-adapter seam
(`tests.fakes.using_adapter`) and fix the `pyproject.toml` marker
description that still reads "tests that require real `claude -p`
sessions."

## Why

It is dead test rot the green-looking suite hides — direct fallout of
an already-shipped ticket (007). An integration test that cannot run
(or would hit a real subprocess) is worse than no test: it gives false
confidence in the lead→worker roundtrip on the post-007 PTY-only
runtime.

## Acceptance

- The test exercises a lead→worker roundtrip via a Fake / mocked PTY
  adapter — hermetic, no real `claude`.
- The `pyproject.toml` integration-marker description no longer
  references `claude -p`.
- The test passes under the standard conftest guards; the suite is
  green.
