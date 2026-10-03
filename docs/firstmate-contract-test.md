# Firstmate contract test

A CI job (`firstmate-contract` in `.github/workflows/ci.yml`) that runs the **real
firstmate scripts** against a throwaway **primary home** and a throwaway **`hive`
second-mate home**. It proves what the Hive desk assumes of firstmate (ADR 0030) and
fails when a firstmate change breaks it, before the desk shows wrong data.

## What it proves

`tests/firstmate_contract/test_two_home_contract.py`:

| Scenario | Scripts |
|---|---|
| Main to mate handoff, then roll-up read back through the snapshot the gateway runs (`run_snapshot`) | `fm-backlog-handoff.sh`, `fm-home-summary-refresh.sh`, `fm-fleet-snapshot.sh --json` |
| A decision held in the mate, answered from the primary's keyed intake and closed in the owning home | `fm-captain-hold.sh hold`, `answers` |
| Reverse handoff, mate to main | `fm-backlog-handoff.sh --from hive main` |
| One-step new ticket in the mate, then a routed, receipted, idempotent edit from the primary | `fm-ticket.sh new`, `edit`, `owner` |
| Pinned schema ids still match (`fm-fleet-snapshot.v1`, `fm-captain-hold-buckets.v1`); an unknown major puts the desk on its read-only fallback | `parse_snapshot`, `run_snapshot` |

The desk side is covered too: `build_desk` reads second-mate tickets (queued, in flight
and working, in flight with a parked, paused or blocked worker, and captain holds the
bounded queued list cut off) from `secondmate_current`, each row and
needs-you item carrying its `owner`, and shows "+N more" for any the roll-up omits; and
`actions.answer_hold` feeds the owner-aware `answers` intake, so a decision held in a
second mate home closes in that home. In-flight tickets in other states (an idle worker,
or done and awaiting landing) have no surface in the mate's summary, so the desk can
neither show nor count them without a firstmate change.

## Hermetic by construction

Everything lives under pytest's `tmp_path`: the homes are `FM_HOME` temp dirs, scripts
resolve their code from `FM_ROOT_OVERRIDE`, `HOME` is redirected, and `tmux`,
`treehouse`, `no-mistakes`, `gh`, `gh-axi` and `herdr` are shadowed by inert stubs, so no
real session, backlog or agent is touched. The only network use is fetching the pinned
firstmate commit. Needs `tasks-axi`, `jq` and `flock` on `PATH`.

## Running it

```
scripts/fetch-firstmate.sh /tmp/firstmate            # shallow fetch of the pinned commit
HIVE_FIRSTMATE_ROOT=/tmp/firstmate uv run pytest tests/firstmate_contract --no-cov
```

Without `HIVE_FIRSTMATE_ROOT` the tests skip, so the normal check command is unchanged.
CI sets `HIVE_FIRSTMATE_REQUIRED=1`, which turns a missing checkout or tool into a failure.

## Bumping the pin

The pin is the full commit SHA in `tests/firstmate_contract/FIRSTMATE_PIN` (a commit on
`main` of `https://github.com/yehezkieled/firstmate`). To adopt a newer firstmate, run
the check after each `/updatefirstmate` or whenever the fork moves:

1. `scripts/fetch-firstmate.sh /tmp/firstmate <candidate-sha>` and run the test as above.
2. Green: write the SHA into `FIRSTMATE_PIN` and commit it with the change.
   Red: the failure is the news. Fix the desk (or ask firstmate for the gap), do not
   loosen the test to fit a changed contract unless that change is intended.
3. The `tasks-axi` version in the CI job is pinned too; bump it the same way.
