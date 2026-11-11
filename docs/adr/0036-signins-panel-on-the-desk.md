# Renew sign-ins from the desk by driving the vendor's own login command

## Status

Accepted, 2026-11-11. Builds on [ADR 0030](0030-firstmate-implemented-in-hive.md)
(the desk is the front door) and the Machine page, which already shows when a
sign-in runs out.

## Context

The owner wants to use firstmate without touching the PC, and picked the desk,
not SSH or a remote desktop, as the place for interactive steps. The step that
recurs is signing in again: the PC's Claude sign-in ends on a date, and when it
does every agent stops. Each vendor's login is an interactive terminal program
that prints a link and, for Claude, waits for a pasted code.

A sign-in link and a pasted code are secrets for as long as the flow runs. The
desk already has an owner check, a CSRF token, a second-step token for
dangerous actions and an audit line per write.

## Decision

A Sign-ins page (`/signins`, `gateway/signins.py`) shows the state of each
sign-in and runs the vendor's own login command for the owner.

- **A fixed table of commands.** `claude auth login` and
  `gh auth login --hostname github.com --web`, each with fixed arguments and
  started without a shell. A request can only name a row of the table. The desk
  never handles a password or a token itself: it only relays the link out and
  the code in.
- **A pseudo-terminal the desk owns**, in its own process group, so Cancel, the
  10 minute time-out and a desk shutdown all kill the whole command.
- **One flow at a time, owned by the page that started it.** Starting returns a
  random token to that page. The link, GitHub's one-time code and the code box
  answer only requests that carry it, in the request body. Any other owner page
  learns that a sign-in is running and may stop it.
- **Secrets stay in memory and are short-lived.** The link, the one-time code
  and the command's output are never logged, audited, written to a file or
  published. They are dropped when the flow ends.
- **Fail closed on output the desk cannot read.** A link is offered only when it
  is `https`, on the vendor's own domain and on the path a sign-in uses. If no
  such link appears within 30 seconds the command is stopped and the page says
  the output was not understood. The command's own text is never shown; a
  failure is one of a fixed set of reasons.
- **Start is behind the step-up, and rate limited** (5 starts in 15 minutes,
  kept on disk so a restart does not reset it).
- **State is read without touching a token:** Claude's one expiry date field,
  one state word from `gh auth status`, and the key expiry from
  `tailscale status`.
- **Tailscale has a row but no Renew.** The desk is reached through this PC's
  Tailscale. `tailscale login` first switches the PC to an empty profile, and
  `tailscale up --force-reauth` is documented as able to bring the connection
  down, so either would cut the page that asked. And once the PC's key has
  expired the desk cannot be reached to press anything. The row shows the key's
  date and points at Tailscale's admin page, where key expiry is turned off.
- **The page is not on the event stream, Web Push or Telegram.** An open page
  asks the desk itself.

## Consequences

- The Claude sign-in can be renewed from the iPad. The Machine page's advice
  now points at the Sign-ins page instead of "at the PC".
- The desk now starts a process that talks to a vendor on the owner's behalf.
  That is why the merge of this change goes to the owner.
- The desk depends on what two command-line programs print. A new version that
  prints something else makes Renew say "not understood" until
  `gateway/signins.py` is updated; signing in at the PC still works.
- No real sign-in was attempted while building this, by instruction. The first
  real renewal of each sign-in is the first real test. See
  [signins.md](../signins.md).
- The Tailscale key is the one expiry the desk cannot fix by itself. A guarded
  re-login button could be added later if the owner accepts that it may drop
  the connection.
- The Codex (OpenAI) sign-in is not covered.
