#!/usr/bin/env python3
"""Add a manual event or task to the work calendar (calendar.json).

Interactive (just answer the prompts):

    add.cmd            (double-click)

Or fully scripted:

    python add_entry.py --event --date 2026-11-30 --subject "Dentist" --notes "11:00"
    python add_entry.py --task --text "Call John" --due 2026-11-25 --priority high --owner John

Then rebuild the page (open.cmd) to see it. Delete entries by editing
calendar.json in Notepad.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANUAL_FILE = HERE / "calendar.json"


def _ask(prompt: str, required: bool = True, default: str = "") -> str:
    while True:
        try:
            v = input(prompt).strip()
        except EOFError:
            print("\nNo console available for prompts — pass the values as flags "
                  "instead (see add_entry.py --help).", file=sys.stderr)
            raise SystemExit(1)
        if v:
            return v
        if default:
            return default
        if not required:
            return ""


def _ask_date(prompt: str, required: bool) -> str:
    while True:
        try:
            v = input(prompt).strip()
        except EOFError:
            print("\nNo console available for prompts — pass the values as flags "
                  "instead (see add_entry.py --help).", file=sys.stderr)
            raise SystemExit(1)
        if not v:
            if not required:
                return ""
            print("  A date is needed here (YYYY-MM-DD).")
            continue
        try:
            date.fromisoformat(v)
            return v
        except ValueError:
            print("  That is not a valid date — use YYYY-MM-DD, e.g. 2026-11-27.")


def add(kind: str, fields: dict) -> None:
    data = {"events": [], "tasks": []}
    if MANUAL_FILE.exists():
        try:
            loaded = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data["events"] = loaded.get("events") or []
                data["tasks"] = loaded.get("tasks") or []
        except ValueError as exc:
            print(f"ERROR: {MANUAL_FILE.name} is not valid JSON ({exc}) — fix it "
                  "in Notepad first; nothing was added.", file=sys.stderr)
            raise SystemExit(1)
    if kind == "event":
        data["events"].append({"date": fields["date"], "subject": fields["subject"],
                               "notes": fields.get("notes", "")})
        print(f"Event added: {fields['date']} — {fields['subject']}")
    else:
        data["tasks"].append({"task": fields["text"], "priority": fields["priority"],
                              "due": fields["due"], "owner": fields.get("owner", "")})
        print(f"Task added: {fields['text']}"
              + (f" (due {fields['due']})" if fields["due"] else ""))
    MANUAL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print("Now double-click open.cmd to refresh the calendar page.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Add a manual event or task to the work calendar.")
    ap.add_argument("--event", action="store_true", help="add an event")
    ap.add_argument("--task", action="store_true", help="add a task")
    ap.add_argument("--date", help="event date YYYY-MM-DD")
    ap.add_argument("--subject", help="event title")
    ap.add_argument("--notes", default="", help="event notes")
    ap.add_argument("--text", help="task description")
    ap.add_argument("--due", default="", help="task due date YYYY-MM-DD (optional)")
    ap.add_argument("--priority", default="medium", choices=["high", "medium", "low"])
    ap.add_argument("--owner", default="", help="who does it")
    args = ap.parse_args(argv)

    if args.event and args.task:
        ap.error("choose one: --event or --task")
    if not args.event and not args.task:
        # interactive
        kind = _ask("Add an (e)vent or a (t)ask? ").lower()
        if kind.startswith("e"):
            args.event = True
        elif kind.startswith("t"):
            args.task = True
        else:
            print("Please answer e or t.")
            return 1

    # Scripted when every required field came in as a flag; prompts are for
    # the double-click path only. Omitted optionals keep their defaults.
    if args.event:
        if args.date and args.subject:
            fields = {"date": args.date, "subject": args.subject, "notes": args.notes}
        else:
            fields = {
                "date": args.date or _ask_date("Event date (YYYY-MM-DD): ", required=True),
                "subject": args.subject or _ask("Event title: "),
                "notes": args.notes or _ask("Notes (optional, Enter to skip): ", required=False),
            }
        add("event", fields)
    else:
        if args.text:
            priority = args.priority
            fields = {"text": args.text, "due": args.due,
                      "priority": priority if priority in ("high", "medium", "low") else "medium",
                      "owner": args.owner}
        else:
            fields = {
                "text": _ask("What needs doing? "),
                "due": _ask_date("Due date (YYYY-MM-DD, Enter to skip): ", required=False),
                "priority": _ask("Priority high/medium/low (Enter = medium): ",
                                 required=False, default="medium").lower(),
                "owner": _ask("Who does it (optional, Enter to skip): ", required=False),
            }
            if fields["priority"] not in ("high", "medium", "low"):
                fields["priority"] = "medium"
        add("task", fields)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
