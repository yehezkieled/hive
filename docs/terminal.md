# Terminal page

`/terminal` on the desk shows firstmate's own session, live, exactly as it is on
the PC: the one herdr pane firstmate runs in. It is for the steps nothing else
on the desk covers, such as a `!` command or a question only the terminal
answers, so the PC never has to be touched. It opens **read-only**. Typing needs
a second confirm, and only one tab can type at a time.

Open it from the firstmate chip in the header (**Open firstmate's terminal**),
from the Machine page, or at `/terminal`.

## States

The banner under the page title always shows the state and who is attached.

| Banner | What it means | What you can do |
|---|---|---|
| **Not open** | Nothing from the session is on the page yet. | Tap **Open terminal**, then **Tap again to open**. |
| **Connecting** / **Reconnecting** | The page is joining, or rejoining after the connection dropped. Read-only. | Wait. After 8 failed tries to rejoin it goes back to **Not open**. |
| **Looking** | The desk is finding firstmate's pane. | Wait a second. |
| **Read-only** (green) | You see the session live. Nothing you type reaches it. | Scroll back, change the text size, or tap **Take keyboard**. |
| **Read-only** (amber), "Another tab has the keyboard" | Another tab typed last and still holds the keyboard. | Release it in that tab, or wait for its 10 idle minutes to run out. |
| **Keyboard taken** (red) | Your typing goes straight into firstmate's session, as if you were at the PC. | Type, use the key row or the paste box, then tap **Release keyboard**. |
| **Session not found** | No herdr pane runs firstmate right now. The page offers nothing to type into. | Use **Wake firstmate** on the desk. The page shows the session again by itself once it is back. |

## Buttons

| Button | What it does |
|---|---|
| **Open terminal** | First tap: checks that firstmate's pane exists and arms the button. Second tap within 20 s: opens the terminal for 30 minutes. |
| **Take keyboard** | First tap: arms it and shows a short warning. **Tap again to take** within a minute: this tab can type. |
| **Release keyboard** | Back to read-only at once. |
| Key row: **Esc**, **Ctrl**, **Tab**, **← ↑ ↓ →**, **PgUp**, **PgDn**, **Enter** | Sends that key. **Ctrl** is sticky: tap it, then a letter, for Ctrl+letter (Ctrl, then C stops what firstmate is doing). On a phone the row scrolls sideways. |
| Paste box: **Send** / **Send + Enter** | Sends the box's text, then Enter for the second button (Cmd+Enter or Ctrl+Enter in the box does the same). Text with line breaks goes as one paste. At most 4096 characters at a time. With the keyboard taken, pasting straight into the terminal puts the text in the box first, to check before sending. |
| **A−** / **A+** / **Fit** | Text size. **Fit** sizes it to show the whole pane; A− and A+ keep your size on this device. |
| **Jump to latest ↓** | Appears when you scroll back; returns to the live screen. |

## Limits

- **30 minutes** per open, then the page asks you to confirm again. This hard limit
  holds whatever you do.
- The keyboard goes back to read-only after **10 minutes** without typing, when the
  30 minutes end, when the tab closes, and when firstmate's session ends or moves to
  another pane.
- **One keyboard holder.** A second tab sees everything but cannot type or take the
  keyboard until the first releases it.
- **6 opens per 15 minutes**, counted across desk restarts; **30 connection attempts
  per 10 minutes**; at most **4 tabs** at once.
- A desk restart forgets every open terminal: confirm again.

## When something goes wrong

| The page says | Cause | What to do |
|---|---|---|
| Firstmate's session was not found: no herdr pane runs Claude in the firstmate home. | Firstmate is down. | **Wake firstmate** (firstmate chip). |
| More than one herdr pane looks like firstmate's session, so the terminal will not pick one. | Two panes match; the desk never guesses. | Ask firstmate (Chat) to close the extra pane, or check at the PC. |
| herdr is not answering … / herdr was not found on this PC … | herdr is down or missing. | Check the Firstmate row on the Machine page. |
| Too many attempts to open the terminal. Try again in N min. | The open limit above. | Wait for the time it shows. |
| The terminal was open for its time limit. Confirm it is you to open it again. | 30 minutes passed. | Confirm again. |
| This confirmation is already in use in another tab. | One open serves one tab at a time. | Confirm again in this tab. |
| Too many tabs have the terminal open. | The 4-tab limit. | Close a tab. |
| herdr did not take that input. It may not have been typed. | herdr refused the input (for example a key name it does not know). | Look at the screen before typing again. |
| This page fell too far behind the terminal. Reload it. | A very slow connection. | Reload. |
| The terminal could not load. Reload the page. | The terminal script did not load. | Reload. |
| The terminal could not connect to the desk. Nothing was opened. | The desk refused the live connection (see "Not yet confirmed" below). | Reload and try once more; if it repeats, check the desk at the PC. |

## How it works

- The desk finds firstmate's pane by the rule the desk's Wake uses: exactly one herdr
  pane whose agent is `claude` and whose cwd and foreground cwd are the firstmate home
  (`HIVE_GATEWAY_FM_HOME`). No request names a pane, a session or a program, so there
  is no way to pick another target. It never starts a shell, on the PC or in Windows.
- The screen is read with `herdr pane read` four times a second while it changes and
  once a second after 10 still seconds, and the scrollback (up to 1000 lines) when you
  scroll back. Only colours are kept from what the pane prints: titles, clipboard
  writes, links and other terminal requests are dropped before they reach the browser.
- Typing goes in with `herdr pane send-text` (literal text, never submitted on its own)
  and `herdr pane send-keys` (named keys). Escape sequences the page does not know
  (mouse reports, function keys, modified arrows) are dropped, never typed.
- The emulator is xterm.js 6.0.0, served from the desk itself
  (`/vendor/xterm-6.0.0.js`, MIT, pinned by hash), not a CDN.

## Security

- Owner only, on the page, on the open action and on the websocket (`/terminal/ws`):
  loopback peer, the owner's `Tailscale-User-Login`, an allowed `Host`, and on the
  websocket an allowed `Origin`, which every browser sends. Anything else is closed
  before it sees a byte. The websocket is on the desk's own `127.0.0.1` port behind
  `tailscale serve`: no new port and no new listener.
- Opening needs the desk's two-tap step-up and its CSRF token; it hands the page a
  pass for one live connection that ends after 30 minutes. Taking the keyboard needs
  its own two taps.
- Nothing the session shows or you type is stored, logged, sent to the event stream,
  Web Push or Telegram. It lives in the desk's memory only while a tab watches.
- The audit log records who, which tab (a random id), what happened and when: open,
  attach, take, release, detach, expired and refused, each with a fixed reason word.
  It goes to `terminal-audit.log` (mode 0600) in `HIVE_GATEWAY_DATA_DIR` and, as
  `gateway-audit action=terminal` lines, to the journal.
- `HIVE_GATEWAY_TERMINAL=off` switches the page, the websocket and the vendored files
  off.

## Not yet confirmed on the PC

- [UNSURE] That `tailscale serve` passes the websocket upgrade with the
  `Tailscale-User-Login` header. If it does not, the desk refuses the websocket (it
  never opens one without the owner header) and the page says "The terminal could not
  connect to the desk."
- [UNSURE] herdr 0.9.1's names for keys other than Enter, Esc and Ctrl+letter
  (tab, shift+tab, up/down/left/right, home, end, pageup, pagedown, delete,
  backspace). herdr refuses a name it does not know with "unsupported key", and the
  page then says it did not take the input.
- [UNSURE] That Claude Code takes a multi-line paste from the paste box (sent as a
  bracketed paste) as one paste rather than one line per Enter.

Settings are in [DEPLOYMENT.md](DEPLOYMENT.md#gateway-desk-adr-0030).
