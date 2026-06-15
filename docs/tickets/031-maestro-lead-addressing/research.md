# Research — Ticket 031

Code evidence for the recipient-resolution path, the spawn/kickoff flow, and the
existing alias precedent. All refs are `file:line` at the time of writing.

## 1. Where the message dies

`MessageDispatcher._handle_actions` handles a `message` action
(`src/hive/process/message_dispatcher.py:310-323`):

```
requested_to   = action.to or ""
recipient_name = self._resolve_message_alias(entity, requested_to)   # :312
recipient      = self._mgr._entities.get(recipient_name) ...          # :313
if not recipient:
    logger.warning("Unknown recipient: %s", requested_to)            # :315
    await self._reject_action(... "unknown recipient ...")           # :316-322
    continue
```

`self.smoke` is **not** an alias, so it passes through `_resolve_message_alias`
unchanged, `_entities.get("self.smoke")` returns `None`, and the action is
rejected as Unknown recipient — the exact live-smoke failure.

## 2. The existing alias resolver (the pattern to mirror)

`_resolve_message_alias` (`message_dispatcher.py:617-633`):

```python
if to == "maestro":
    return sender.name.split(".")[0]          # org root (first segment)
if to == "parent":
    return ".".join(sender.name.split(".")[:-1])   # drop last segment
return to                                      # anything else: unchanged
```

Docstring (`:617-628`) states the design intent verbatim: aliases exist so an
entity "never has to remember — or invent — a dotted name (Ticket 023, design
D2)", and non-aliases pass through with **no fuzzy matching** "because a silent
misdelivery is worse than a drop." These are the **upward** aliases
(`maestro`, `parent`). 031 is the missing **downward** mirror.

- **No role gating** — the resolver applies to any `sender`, so the mirror is
  naturally generic.

## 3. The self-message ban handles the bare-`self` case for free

When an alias resolves back to the sender, the permission layer rejects it with a
tailored message (`message_dispatcher.py:324-334`):

```
if not can_message(entity.role, entity.name, recipient.role, recipient.name):
    if recipient.name == entity.name:
        reason = f"{requested_to!r} resolves to yourself ({entity.name}); "
                  "self-messages are not allowed. " + self._addressing_hint(entity)
```

So a bare `to:"self"` → resolves to the maestro → caught here with clear
feedback. No extra handling needed. (`can_message` self-ban lives in
`src/hive/bus/permissions.py`.)

## 4. Rejection feedback loop (reactive teaching)

`_reject_action` (`message_dispatcher.py:635-665`) audits the rejection and
routes a `system -> sender` note so the sender self-corrects next turn
(Ticket 023, D2). The note text comes partly from `_addressing_hint`
(`:667-678`), whose **org-root branch** (`:670-674`) currently says:

```
"You are an org root: address entities in your org by their full dotted
 name (e.g. \"yourname.team\")."
```

This is the natural place to *also* advertise the new `self.<team>` form.

## 5. Spawn / kickoff flow — the goal is a separate hop

`spawn_team` handling (`message_dispatcher.py:460-481`): registers the lead as
`<maestro>.<team>` (per `role-maestro.md:98-99`), records `lead.name`, and
queues a kickoff (`pending_kickoffs.append(lead.name)`, `:481`). After the loop,
each kickoff runs `_auto_kickoff` (`:511-516`).

`_auto_kickoff` (`wake_scheduler.py:55-68`) sends `_SPAWN_KICKOFF_TEXT` — a
**generic** wake prompt, not the maestro's goal. Confirms the maestro must send
its contract/goal as a follow-up `message` — the delegation that failed.

## 6. Naming / reserved words

Entity-name validation (`src/hive/models/entity.py:335`) blocks only `/` and
`..`. There is **no reserved-word list**, so `maestro`/`parent` already shadow a
same-named entity. `self`/`me` inherit the identical, already-accepted risk —
not a regression 031 introduces.

## 7. Role JD (proactive guidance)

`personalities/role-maestro.md:96-99` — the `spawn_team` bullet states the lead
"is registered as `<your-name>.<team_name>`" but does **not** mention `self`.
The lead-side JD already uses the "address it as `to:\"maestro\"` (no name
needed)" phrasing (`message_dispatcher.py:677`); the maestro bullet should gain
the symmetric `self.<team_name>` line.
