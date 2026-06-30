# 049 — Deep-link composer-clear polish (048 follow-up)

> Follow-up to [048](../048-useful-deep-link/). Cosmetic chat-UX; **next sprint**.

## What

When a "needs you" push deep-links into the chat (048), it currently pre-fills
the composer with the raw `/m:<entity> ` prefix. The chat already shows a
**"Talking to: /m:<entity> ×" target chip**, so the literal prefix in the box is
redundant. Instead: set the target chip and leave the composer **empty**, so the
user types only their bare reply (the `/m:` routing is applied on send, hidden
from view).

## Why

The deep-link works and is verified on-device (048), but the doubled-up
`/m:otter` (chip + composer text) looks raw. This is the "make it feel finished"
polish — small, purely presentational.

## Acceptance

- A needs-you deep-link sets the "Talking to" target to `<entity>` and leaves
  `#chat-input` empty + focused.
- Sending the bare reply still routes to that maestro (the `/m:` prefix is
  applied transparently, or the existing chip-target drives routing).
- The raw `/m:<entity>` text no longer appears in the composer on deep-link.
- Verified on an iPad: tap a decision push → chip shows the maestro, box empty,
  type + send works.

## Non-goals

- Changing how non-deep-link `/m:` addressing works (typing `/m:otter` by hand
  stays as-is).
- Run-ended (`?focus`) card-highlight behaviour — unchanged from 048.

## Open question (for design)

Does the "Talking to" chip drive routing on its own, or is it a live parse of the
composer `/m:` prefix? If the latter, "clear composer but keep target" needs a
small persistent-target mechanism in the chat JS. Settle in design before build.
