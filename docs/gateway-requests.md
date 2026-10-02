# Gateway requests to the first mate

The website never edits backlog files. Anything that needs the first mate's
judgment arrives as a note through `fm-inbox.sh note` (stdin body, with an
idempotent `--request-id web-<16 hex>`). Notes whose first line is one of the
markers below are structured; the first mate applies them with `tasks-axi`
(or does the merge) and answers with `fm-inbox.sh reply <note-id> <text>`.

The website shows each note's state from `fm-inbox.sh receipts`:
**waiting** (not acknowledged), **picked up** (acknowledged, no reply),
**answered** (a reply exists; the reply text is shown). Acknowledge, then reply
with what was applied (or why not) so the owner sees "applied" on the page.

Shape: marker line, `key: value` header lines, a line `---`, then free text.
Header values are single lines; `from:` records the owner login.

## `HIVE-WEB TICKET REQUEST v1`

```
HIVE-WEB TICKET REQUEST v1
action: edit | create
ticket: <task id> | (new)
project: <project name as shown on the desk>
field: title | body | priority | new
from: hive web (<owner login>)
---
<new text>
```

- `edit`: replace `field` of ticket `ticket` with the text. Only listed,
  not-done tickets can be requested.
- `create`: `field` is `new`; the first line of the text is the title, the rest
  (after a blank line) the details. Pick the delivery mode and ticket id.

## `HIVE-WEB MERGE WORD v1`

```
HIVE-WEB MERGE WORD v1
task: <task id>
pr: <url or (none)>
from: hive web (<owner login>)
---
<fixed sentence>
```

The owner's explicit word to merge that PR (the page showed a confirm step).
It is a record, not an action: the first mate still merges.

## `HIVE-WEB DECISION ANSWER v1`

```
HIVE-WEB DECISION ANSWER v1
task: <task id>
decision: <open decision key from the worker's status>
from: hive web (<owner login>)
---
<the owner's answer>
```

Answers a worker's open `needs-decision` (a status-file decision, not a
captain hold). Captain holds are answered directly with
`fm-captain-hold.sh answer`, with the web provenance appended to the words.

Plain chat messages are unstructured notes with no marker.
