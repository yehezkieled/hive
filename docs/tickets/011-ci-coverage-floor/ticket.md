# 011 — Add a CI coverage floor

## What

CI runs `pytest -m "not integration" --cov=src/hive` but sets no
`--cov-fail-under` and uploads no report — the coverage number is
computed and thrown away. Measure current coverage, then add a
`fail_under` ratchet (set just under current) so a regression fails the
build.

## Why

After the 004 god-object breakup and 007 runtime removal churned the
test base, an unenforced coverage number is a false safety signal. A
ratchet turns it into a real gate at near-zero cost.

## Acceptance

- CI fails when line coverage drops below the configured floor.
- The floor is set just under current measured coverage (a ratchet, not
  an aspiration).
- The threshold lives in config (`[tool.coverage.report] fail_under` or
  the CI `--cov-fail-under` flag), documented in one place.
