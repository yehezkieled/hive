# Design — Ticket 031

Make a maestro's first delegation to a freshly-spawned lead land on the first
attempt, by mirroring Hive's existing **upward** addressing aliases
(`maestro`/`parent`) with a **downward** `self`/`me` alias, plus sharpening the
guidance so `self.<team>` is the documented path.

## Chosen approach

A two-part fix — the alias is load-bearing, the guidance is supporting (D1).

### Part A — downward alias in `_resolve_message_alias` (load-bearing)

Extend the resolver (`message_dispatcher.py:617`) so `self`/`me` mean "the
sender's own name", symmetric with the existing `maestro`/`parent`:

```
to == "self" | "me"            →  sender.name
to startswith "self." | "me."  →  sender.name + "." + <rest after first dot>
(everything else)              →  unchanged   (no fuzzy matching — unchanged policy)
```

Properties (all settled in the grill):

- **Generic, no role gating** (D2) — applies to any sender, exactly like the two
  existing aliases. A maestro `hive_dev` → `self.smoke` = `hive_dev.smoke`; a
  lead would resolve `self.x` against its own name too (works even though leads
  have no children today).
- **Both `self` and `me`** (D2) — robust to the model's phrasing (acceptance
  prong 2).
- **Bare `self`/`me` → sender → existing self-message ban** (D2) — handled at
  `message_dispatcher.py:326-334` with the "resolves to yourself" feedback. No
  new code path.
- **Invalid recipients still reject** (acceptance prong 3) — non-aliases pass
  through unchanged, so `hive_dev.ghost` still fails the recipient lookup with
  the addressing hint. The fix adds delivery for the alias *without* loosening
  the no-fuzzy-match policy.

### Part B — guidance (supporting, D3)

Two touch points teach `self.<team>`:

- **Proactive** — `role-maestro.md` `spawn_team` bullet (`:98`): append
  "— address it as `self.<team_name>` (no name needed)", symmetric with the
  lead JD's existing `to:"maestro"` phrasing.
- **Reactive** — `_addressing_hint()` org-root branch
  (`message_dispatcher.py:670-674`): add ", or a direct child as `self.team`",
  so a rejected message points the maestro at the working alias and it
  self-corrects next turn via the existing `_reject_action` loop.

## Why this over the alternatives

| Alternative | Rejected because |
|-------------|------------------|
| **JD-only** (tell the maestro to always write its full dotted name) | Already in the JD (`:98`); the model still guessed `self` in the live smoke. Fighting the instinct already lost once. |
| **Alias-only**, no JD change | Works mechanically, but leaves the JD documenting a different form than the model uses — and gives no reactive hint when a maestro still gets it wrong. |
| **Spawn-confirmation echoes the exact name back** as the primary fix | Heavier (new feedback message + parsing), and still phrasing-dependent — the maestro could re-paraphrase. The alias makes the natural phrasing *work*; the JD echo is unnecessary on top of it. |
| **Reserve `self`/`me`/`maestro`/`parent` as entity names** | Broader change touching name validation and the two existing aliases — out of 031's scope. Shadow risk accepted as-is (D2), mirroring Ticket 023. |
| **Fuzzy-match the recipient** | Explicitly rejected by the existing resolver: "a silent misdelivery is worse than a drop." |

## Decisions log

- **D1** — Both alias + JD; alias load-bearing.
- **D2** — Generic resolution (`self`/`me` = sender's name); both words; bare
  form → self-message ban; shadow risk accepted + noted (mirrors Ticket 023).
- **D3** — Update the `role-maestro.md` `spawn_team` bullet **and** the
  `_addressing_hint` org-root branch.
- **D4** — Tests as scoped (below); **no** CONTEXT.md change; **no** new ADR
  (extends Ticket 023's documented decision); **direct** lane.

## Side-effects on shared docs

- **CONTEXT.md** — none. `self`/`me` are addressing mechanics, not domain
  entities; the glossary stays pure (the existing aliases aren't entries either).
- **docs/adr/** — none. The alias-vs-force-full-name trade-off is the same one
  Ticket 023 (D2) already decided and documented in the resolver docstring; a
  new ADR would only restate it. The docstring gains a one-line note for the
  downward mirror.

## Verification

- **Unit** (`_resolve_message_alias`): `self.smoke`→`hive_dev.smoke`;
  `me.smoke`→same; bare `self`/`me`→sender name; `maestro`/`parent` unchanged;
  arbitrary `foo.bar` passes through.
- **Flow** (`_handle_actions`): maestro → `to:"self.smoke"` routes to the lead
  (no Unknown recipient); bare `self` → self-message-ban feedback; `hive_dev.ghost`
  → still rejected with the addressing hint.
- **Deployed re-smoke** (S6 "behaviour, not deletion" rule, at build time): a
  real maestro spawns a team, addresses `self.<team>`, and the goal lands on the
  first attempt — no manual workaround.

## Out of scope

- maestro→user delivery (`Unknown recipient: user`) — Ticket 021.
- Bridging maestro interactive gates — Ticket 029.
- Reserving alias words as entity names — separate, broader change.
