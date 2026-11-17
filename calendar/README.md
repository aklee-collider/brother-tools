# My Work Calendar

One page with **all** the work in one place — a month grid, open tasks, and
upcoming events, collected from every processed meeting **plus anything you
add yourself**. Nothing is copied: the page is rebuilt from the transcriber's
`output\` folders each time, so new meetings show up automatically.

## Use it

- **After a meeting is processed** (or any time): double-click `open.cmd` —
  rebuilds the page and opens it.
- **Add your own event or task:** double-click `add.cmd` and answer the
  prompts. Or scripted:
  ```
  python add_entry.py --event --date 2026-11-30 --subject "Dentist"
  python add_entry.py --task --text "Renew passport" --due 2026-12-15 --priority high
  ```
- **Remove / edit a manual entry:** open `calendar.json` in Notepad, edit,
  save, then `open.cmd`.
- Month arrows move around; **today** jumps back.

## Where the data comes from

| Shown as | From |
|---|---|
| 📌 Blue chips / upcoming list | `events.ics` of each processed meeting |
| ⚑ Yellow chips (red when overdue) | `tasks.md` of each processed meeting |
| ✎ "added by you" items | `calendar/calendar.json` (via `add.cmd`) |
| Meetings list | each meeting's date + title |

Tasks and events stay in the meeting folders where they were created — the
calendar page is a **view**, so it can never drift out of sync with results.

## Cost & privacy

Zero. Building the page is all local — no network, no API calls, no keys.
