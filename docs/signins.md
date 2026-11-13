# Sign-ins page

`/signins` on the desk shows each sign-in on the PC that can run out, and renews
one from the iPad with no terminal. It is reached from **Sign-ins** in the menu, or from
the Machine page (the link in its first line, and the advice on its Claude and
Tailscale rows).

It is for the owner only, on the tailnet only, like every desk page.

## The sign-ins

| Sign-in | What the page shows | What Renew does | If Renew fails |
|---|---|---|---|
| Claude | Days left and the date the sign-in ends. Read from one date field of Claude Code's credentials file; nothing else in that file is read. | Runs `claude auth login` on the PC. The page shows a big link. Open it, sign in at Claude, copy the code Claude then shows and paste it into the box on the page. When it finishes, the card shows the new end date. | Start it again. If it fails twice, sign in at the PC: run `claude auth login` in a terminal there. |
| GitHub | Signed in, Not signed in or Not working. A GitHub sign-in has no end date, so there is no date to show. Read with `gh auth status`, keeping one state word. | Runs `gh auth login --hostname github.com --web` on the PC. The page shows a link and a one-time code such as `AB12-CD34`. Open the link, sign in at GitHub and type the code there. Nothing is pasted back: the page shows when GitHub has signed the PC in. | Start it again. If it fails twice, sign in at the PC: run `gh auth login` in a terminal there. |
| Tailscale, this PC | Connected or not, and the date the PC's key expires. | **No Renew.** The desk reaches you through this PC's Tailscale, and signing it in again from the desk would cut the page that asked. The card links to Tailscale's admin page instead; see "Tailscale" below. | Not applicable. |

The Codex (OpenAI) sign-in is not on the page.

## Can Claude answer right now?

A valid sign-in does not mean Claude's service is answering. Claude can turn every
request away (a Cloudflare "Attention Required!" page, a `503`) while the sign-in
still has weeks left. The Claude card therefore carries a second, live light under
its expiry date: a chip and a few words. Tap it (or hover on a desktop) for a
one-line reminder of what it checks.

| Light | The card says | What it means | What to do |
|---|---|---|---|
| Green, **Answers** | Claude's service answers | Claude's servers answered this PC. | Nothing. |
| Red, **Not answering** | Claude's service refused the request (Cloudflare 403), or (403) | Cloudflare, in front of Claude, blocked the request from this PC (a plain 403: something refused it without Cloudflare's block page). | Nothing on the PC fixes it; wait and look again. Do not renew the sign-in for it. |
| Red, **Not answering** | Claude's service is down (503), or (500), (502) … | Claude's servers are failing. | Wait; Claude's status page says more. |
| Red, **Not answering** | Claude's service is overloaded (529), or is turning requests away (429) | Claude is answering but turning work away. | Wait. |
| Grey, **Unknown** | Could not check (timed out), (no network or DNS), (the request failed) or: an answer not known here (code) | The desk got no answer it can read. Never shown as green. | Check the PC's network on the Machine page. |
| Grey, **Unknown** | Checking Claude's service… | No answer from the last two minutes yet. | Wait a moment: the page refreshes itself. |
| Grey, **Off** | Not checked: the live check is turned off | `HIVE_GATEWAY_CLAUDE_REACH=off`. | Nothing, or unset it. |

A red light beside a sign-in that still reads as valid adds "— your sign-in is
fine": the problem is on Claude's side and renewing will not help. A signed-out or
expired sign-in keeps its own red state and its **Renew**, whatever the light says.

**What the check is.** One HTTPS `GET` of `https://api.anthropic.com/v1/models`,
the host Claude Code itself talks to, carrying no key, token or cookie. Unsigned,
the API answers `401`; that answer came from Claude's own servers, past Cloudflare,
so it counts as "answers". Nothing is billed. The desk reads only the status code,
Cloudflare's `cf-mitigated` header and, for a `403` only, the first 8 KiB of the
answer, searched for Cloudflare's block-page words and then dropped. Nothing of the
answer is kept, logged or shown, and the sign-in file is never read for it.

**What it does not prove.** That your own signed-in requests will work. A green
light says Claude's front door answers this PC; your account could still be turned
away (a used-up plan, a revoked sign-in).

**How often.** Only while a Sign-ins page is open: a page view, or the page's own
30-second refresh, starts a check when the last one started at least 60 seconds
ago, however many pages are open. One check at a time, cut off after 2.5 seconds,
never inside the page load: the page shows the last answer at once. An answer older
than about two minutes shows grey, not its old colour. Nothing runs when no page is
open. `HIVE_GATEWAY_CLAUDE_REACH` sets another https address, or `off`.

## Renewing

1. Tap **Renew**, then **Tap again to renew** within 15 seconds. This is the
   same second step the desk asks for before a merge word or a restart.
2. The panel at the top shows the link, and for GitHub the one-time code. Do
   what it says.
3. When the vendor has signed the PC in, the panel says so and the card shows
   the new state. Claude also shows the new end date.

Only one sign-in runs at a time. It stops by itself after 10 minutes; the panel
shows the time. **Cancel** stops it at once.

The link and the code are shown only on the page that started the sign-in. A
second tab, or another device, sees "in progress" and a **Stop that sign-in**
button, and nothing else. If the page that started it is gone, stop the
sign-in and start again. Reloading the starting page keeps the panel.

Renew while the present sign-in still has days left. Then a failed attempt
leaves time to do it at the PC.

## When it does not work

The panel gives one of these reasons. The login command's own output is never
shown.

| The page says | What it means | What to do |
|---|---|---|
| The login command was not found on this PC, so nothing was started. | The desk could not find `claude` or `gh`. The card also says so, and has no Renew. | Set `HIVE_GATEWAY_CLAUDE` or `HIVE_GATEWAY_GH` to the program's path and restart the desk. |
| The login command's output was not understood, so no link is offered. Sign in at the PC instead. | The command printed no link the desk recognises within 30 seconds, most likely because a new version prints something different. The desk never guesses: it offers nothing and stops the command. | Sign in at the PC. The desk's reading of that command then needs updating (`gateway/signins.py`). |
| That code was not accepted. Paste the whole code again. | Claude turned the pasted code down. The sign-in is still running. | Copy the whole code, including the part after `#`, and paste it again. |
| That does not look like a sign-in code. | What was pasted had a space or an unusual character, so it was not sent. | Paste the code exactly as shown. |
| The sign-in was not finished in 10 min, so it was stopped. | The time ran out. | Start it again. |
| The login command reported that the sign-in failed. | The vendor refused it. | Start it again. |
| The one-time code ran out before it was used. | GitHub's code was not entered in time. | Start it again. |
| This PC signs in to GitHub with an access token set in its environment. | `GH_TOKEN` or `GITHUB_TOKEN` is set for the desk, so `gh` has no saved sign-in to renew. The card has no Renew. | Replace the token where it is set. |
| The login command could not be started. | The program is there but would not run. Nothing was started. | Sign in at the PC, and check the program named by `HIVE_GATEWAY_CLAUDE` or `HIVE_GATEWAY_GH`. |
| The login command stopped before the sign-in was finished. | It exited by itself. | Start it again, then sign in at the PC if it repeats. |
| The login command finished, but the sign-in still does not read as valid. | The command reported success and the state read disagrees. | Look at the card again in a minute. If it is still not valid, sign in at the PC. |
| The desk restarted, which stopped the sign-in. | A sign-in never outlives the desk. | Start it again. |
| A … sign-in is already running. Cancel it first. | Only one runs at a time. | Wait for it, or stop it. |
| Too many sign-in attempts. Try again in … | More than 5 starts in 15 minutes. A desk restart does not reset the count. | Wait. |
| This page did not start the sign-in, so it cannot act on it. | The code was sent from a tab that does not own the sign-in. | Use the tab that started it, or stop the sign-in and start again. |

## Tailscale

A Tailscale key expires some months after the device signed in (180 days by
default), unless key expiry is turned off for the device. Once the PC's key
has expired the desk cannot be reached at all.

Do this before the date on the card:

1. Open Tailscale's admin page (the link on the card), on the Machines page.
2. Open this PC's menu and choose **Disable key expiry**.

The card then reads "Nothing to renew: this PC's key does not expire."

If the key has already expired, the same menu offers **Temporarily extend
key**, which Tailscale documents as lasting 30 minutes. Use that time to
disable key expiry. Whether disabling expiry alone revives a key that has
already expired is not stated by Tailscale `[UNSURE]`.

Why the desk does not do it: `tailscale login` first switches the PC to an
empty profile (`cmd/tailscale/cli/login.go` in Tailscale's source), and
`tailscale up --help` says of `--force-reauth`: "this may bring down the
Tailscale connection and thus should not be done remotely over SSH or RDP".
Either would cut the link the request came over. The admin-page steps above
are from <https://tailscale.com/docs/features/access-control/key-expiry>.

## What is kept, and what never is

- **Who.** The same owner check as every desk page, and the form's CSRF
  token. Renew also needs the step-up token from the first tap, bound to that
  one sign-in and that one request; a used request cannot start a second one.
- **What runs.** A fixed list of two commands with fixed arguments, started
  without a shell. Nothing from a request reaches a command line. A request
  names `claude` or `github`, nothing else.
- **The pasted code.** It goes to the login command's terminal and nowhere
  else. It must be printable characters with no spaces, at most 512. The
  terminal's echo is off, and from the paste on the desk keeps no more than
  the last 64 characters of the command's output, in memory, to recognise its
  success or failure words. The code is not stored, logged or sent back to
  the page.
- **The link and the one-time code.** Held in memory only while the sign-in
  runs, and returned only to the page that started it. That page holds a
  random token, kept in the tab's session storage and sent only in request
  bodies, never in an address. They are cleared when the sign-in ends, is
  cancelled or times out, and the page blanks its panel at the time-out even
  if the desk cannot be reached.
- **Never written anywhere.** The link, the code and the command's output are
  not in the journal, the audit log, firstmate's status files, the event
  stream, Web Push or Telegram.
- **Credential files.** Never copied, moved or shown. The only read is the one
  date field, the same read the Machine page makes. The live Claude light reads
  nothing from them and sends no credential.
- **Audit.** `signin-audit.log` under `HIVE_GATEWAY_DATA_DIR` (mode 0600) gets
  one line per event:
  `<time> who=<owner> via=desk provider=<claude|github> event=<started|ok|failed|cancelled|refused> [reason=<word>]`.
  The journal gets the matching `gateway-audit action=signin` line. The reason
  is one of a fixed set of words, never text from the command.
- **Rate limit.** 5 starts in 15 minutes, counted in `signin-starts` in the
  same directory.

## Not yet tried against the real services

Development was not allowed to attempt a real sign-in. The reading of each
command's output was built from Claude Code 2.1.322 and gh 2.101.0 on the PC
(their help text and the messages inside the programs) and is tested only with
stand-in commands. The first real renewal of each is therefore unverified
`[UNSURE]`. If a command prints something the desk does not recognise, the
page says "not understood" and offers nothing.

Also not established: whether `claude auth login` opens a browser window on
the PC as well as printing its link `[UNSURE]`. A window opened there is
harmless and can be closed.

## Settings

`HIVE_GATEWAY_CLAUDE` and `HIVE_GATEWAY_GH` name the two login commands; see
[DEPLOYMENT.md](DEPLOYMENT.md#gateway-desk-adr-0030). The state reads use the
Machine page's settings (`HIVE_GATEWAY_CLAUDE_CREDENTIALS`,
`HIVE_TAILSCALE_BIN`).
