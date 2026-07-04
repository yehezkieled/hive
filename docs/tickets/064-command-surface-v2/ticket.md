# 064 — Command surface v2 (behaviour changes to kept commands)

> Sibling of [050](../050-command-audit-trim/) (pure trim — *removes* dead
> commands, no behaviour change). 050 declared "changing what the kept commands
> do" a **non-goal**; 064 owns exactly that. Land 050 first so this touches a
> clean, smaller set.

## What

Five behaviour changes to commands the 050 audit kept, from the 2026-07 command
product-review:

1. **`/mode` — trim + standardise the default policy.** Drop `edit` and `auto`
   from the offered set. Enforce the intended default: **leads = `yotree`,
   maestros = `yolo`**. Today it is inconsistent — the `Entity` base default is
   `"default"` (edit), `lifecycle_manager.py:~274` force-sets a new maestro to
   `"yolo"`, and the role files say "prefer yotree". Make the base defaults match
   the policy so `/mode` is a rare override, not the thing that sets it. Plan mode
   stays reachable via the grill-me skill, **not** a `/mode plan` per-entity toggle.

2. **`/loop` → native CC `/goal`.** Remove Hive's `/loop` (loop-framework prompt
   injection) in favour of Claude Code's native **`/goal`** (completion-condition
   loop with a Haiku evaluator, v2.1.139+). Cleaner primitive and it doesn't
   collide with the loop-engineering direction. Needs one integration decision:
   how Hive seeds an entity's goal — entity self-invokes `/goal` vs Hive injects
   `/goal …` at spawn.

3. **`/model` — add `fable`, warn on API-billed.** Add `fable` to the valid set
   and surface a **billing warning** when an API-billed model is selected (today
   the command sets `--model` with zero cost signal; `dispatch.py:~792`). The
   *per-harness* extension (Codex/GLM models, a harness-aware valid set) is
   already carried by tickets [061](../061-codex-adapter/)/[062](../062-opencode-adapter/)
   — 064 only lands the `fable` + billing-warning piece.

4. **Remove `/approve` `/deny` `/vault` as typed commands.** They become the
   **card + inline button** built in [051](../051-unified-needs-you-lane/). Keep
   the vault/approval **backend** untouched — the buttons call the same logic; we
   remove only the typed surface. **Gated on 051 landing** (see Dependencies).

5. **`/commit` `/pr` `/merge` → one `/ship`.** Fold the three manual git commands
   into one intuitive verb mirroring the git-ship skill:
   - `/ship <entity>` → commit + push + open PR (the common case)
   - `/ship <entity> merge` → …then squash-merge (still env-gated by
     `HIVE_ALLOW_AUTO_MERGE`)
   - `/ship <entity> "msg"` → custom commit message

## Why

050 gives the redesign a smaller command set; 064 makes the kept commands
**behave right** before they get promoted to first-class UI (052/053). Each change
either removes a foot-gun (mode inconsistency, silent API billing), retires a
soon-to-be-confusing command (`/loop` vs loop-engineering), or makes a
three-command dance one obvious verb (`/ship`). Doing it now means the web wraps
UI around the *final* semantics, not semantics we're about to change.

## Acceptance

- **Mode:** offered set is `yotree` / `yolo` / (plan via grill-me, not a toggle);
  `edit`/`auto` removed. Spawn defaults enforce leads=`yotree`, maestros=`yolo`
  from one source of truth (base default matches, no downstream force-set drift).
- **Loop:** Hive `/loop` removed from dispatcher + help + autocomplete; entity
  goal-seeding path via `/goal` decided and wired; `/help` drift test updated.
- **Model:** `fable` accepted; selecting an API-billed model emits a clear
  one-line billing warning; plan-billed selections unchanged.
- **Security:** `/approve` `/deny` `/vault` removed as typed commands **only after
  051's button surface exists**; vault backend + money-approval rail unchanged;
  a payment is still approvable end-to-end at all times.
- **Ship:** `/ship <entity> [merge|"msg"]` works; `/commit` `/pr` `/merge` removed
  or aliased; auto-merge stays env-gated.
- `ruff` + full `pytest -m "not integration"` green; `tests/test_help.py` drift
  guard passes; deployed smoke of each changed command.

## Non-goals

- The pure removals + relabels (`/swarm` `/broadcast` `/budget`, `/agent`→`/message`,
  "workers"→"leads") — that's **050**.
- The per-harness `/model` valid-set extension — that's **061/062**.
- Building the approval card/button itself — that's **051**; 064 only removes the
  typed commands once it lands.
- Web UI for any kept command (052/053).

## Dependencies

- **051 (unified needs-you lane)** must land before change #4 (security-command
  removal) — deleting the typed approve/deny/vault before the button exists would
  leave no way to approve a payment. Changes #1–#3 and #5 are independent of 051.
- **050** should land first (clean set), but 064 is not hard-blocked on it.

## Open integration question (resolve at grilling)

- **`/goal` seeding (change #2):** does the entity self-invoke `/goal <condition>`,
  or does Hive inject it at spawn / on a `/goal` Hive-command? Affects whether we
  keep a thin Hive `/goal` passthrough or drop the Hive command entirely.
