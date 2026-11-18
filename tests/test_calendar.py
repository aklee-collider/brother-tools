#!/usr/bin/env python3
"""Offline test suite for the calendar tool — no network, no API cost.

Controls follow the repo convention: each parser is fed a violation
(malformed date, folded ICS lines, corrupt manual store, empty-output birth
shape) and must respond as documented — degrade the field, never the data,
or fail loudly when the only copy of data is unreadable. Run:

    python tests\\test_calendar.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "calendar"))
sys.path.insert(0, str(ROOT / "transcriber"))

import build_calendar  # noqa: E402
import add_entry  # noqa: E402
import transcribe  # transcriber's write_ics — the real producer  # noqa: E402


SAMPLE_TASKS_MD = """# Outstanding tasks — weekly sync

## High
- [ ] Call the bank before the deadline
- [ ] Send the proposal (John, due 2026-11-25)

## Medium
- [ ] Review draft (due 2026-11-27)
- [x] Book the room (Priya, due 2026-11-20)

## Low
- [ ] Tidy the shared drive (Sam)

## Bad section junk
- not a task line
broken line without bracket
"""


class TestTasksMdParser(unittest.TestCase):
    def _md(self, text: str) -> Path:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "tasks.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_full_parse(self):
        tasks = build_calendar.parse_tasks_md(self._md(SAMPLE_TASKS_MD), "weekly sync")
        self.assertEqual(len(tasks), 5)
        by_text = {t["task"]: t for t in tasks}
        self.assertEqual(by_text["Send the proposal"]["due"], "2026-11-25")
        self.assertEqual(by_text["Send the proposal"]["owner"], "John")
        self.assertEqual(by_text["Send the proposal"]["priority"], "high")
        self.assertEqual(by_text["Review draft"]["owner"], "")  # due-only line
        self.assertEqual(by_text["Book the room"]["priority"], "medium")  # [x] still a task row
        self.assertEqual(by_text["Tidy the shared drive"]["priority"], "low")
        self.assertEqual(by_text["Tidy the shared drive"]["due"], "")
        for t in tasks:
            self.assertEqual(t["source"], "weekly sync")

    def test_neg_corrupted_date_keeps_task(self):
        # Baseline behavior of a naive parser would drop or crash the row;
        # documented behavior: lose the field, keep the task.
        tasks = build_calendar.parse_tasks_md(
            self._md("- [ ] Pay invoice (John, due not-a-date)\n"), "x")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task"], "Pay invoice")
        self.assertEqual(tasks[0]["due"], "", "unparseable date must not survive")
        self.assertEqual(tasks[0]["owner"], "John")

    def test_no_section_defaults_medium(self):
        tasks = build_calendar.parse_tasks_md(self._md("- [ ] Floating task\n"), "x")
        self.assertEqual(tasks[0]["priority"], "medium")


class TestIcsRoundTrip(unittest.TestCase):
    def test_roundtrip_through_real_writer(self):
        # The producer is the transcriber's own write_ics — including the
        # escaping and 75-octet folding it applies. Parse must invert it.
        events = [
            {"date": "2026-11-27", "subject": "Launch plan review",
             "notes": "Bring the deck"},
            {"date": "2026-12-01", "subject": "Call with, Dr. Smith; MRI follow-up",
             "notes": "line one\nline two, with; specials \\ done"},
        ]
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        p = Path(td.name) / "events.ics"
        transcribe.write_ics(events, p)
        parsed = build_calendar.parse_ics(p, "test meeting")
        self.assertEqual(len(parsed), 2)
        for original, got in zip(events, parsed):
            self.assertEqual(got["date"], original["date"])
            self.assertEqual(got["subject"], original["subject"])
            self.assertEqual(got["notes"], original["notes"])
            self.assertEqual(got["source"], "test meeting")
            self.assertFalse(got["manual"])


class TestAggregationBirthShapes(unittest.TestCase):
    """The page must work from day zero: no meetings, no manual file, both."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.dir = Path(td.name)
        self.addCleanup(td.cleanup)
        self.old_out = build_calendar.TRANSCRIBER_OUTPUT
        self.old_manual = build_calendar.MANUAL_FILE
        build_calendar.TRANSCRIBER_OUTPUT = self.dir / "output"
        build_calendar.MANUAL_FILE = self.dir / "calendar.json"

    def tearDown(self):
        build_calendar.TRANSCRIBER_OUTPUT = self.old_out
        build_calendar.MANUAL_FILE = self.old_manual

    def _meeting_folder(self, name: str, tasks_md: str = "", ics_text: str = "",
                        meta: dict | None = None) -> Path:
        folder = build_calendar.TRANSCRIBER_OUTPUT / name
        folder.mkdir(parents=True)
        if tasks_md:
            (folder / "tasks.md").write_text(tasks_md, encoding="utf-8")
        if ics_text:
            (folder / "events.ics").write_text(ics_text, encoding="utf-8")
        if meta:
            (folder / "asr_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return folder

    def test_fresh_install_builds_empty(self):
        events, tasks, meetings = build_calendar.collect()
        self.assertEqual((events, tasks, meetings), ([], [], []))

    def test_manual_only(self):
        build_calendar.MANUAL_FILE.write_text(json.dumps({
            "events": [{"date": "2026-12-05", "subject": "Dentist", "notes": "11:00"}],
            "tasks": [{"task": "Renew passport", "priority": "high", "due": "",
                       "owner": ""}]}), encoding="utf-8")
        events, tasks, meetings = build_calendar.collect()
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["manual"])
        self.assertEqual(events[0]["source"], "added by you")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(meetings, [])

    def test_meeting_plus_manual_merged(self):
        self._meeting_folder(
            "weekly_20261120", meta={"title": "Weekly sync", "meeting_day": "2026-11-20"})
        (build_calendar.TRANSCRIBER_OUTPUT / "weekly_20261120" / "events.ics").write_text(
            "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:1@x\n"
            "DTSTART;VALUE=DATE:20261127\nSUMMARY:Launch plan review\n"
            "END:VEVENT\nEND:VCALENDAR\n", encoding="utf-8")
        (build_calendar.TRANSCRIBER_OUTPUT / "weekly_20261120" / "tasks.md").write_text(
            "## High\n- [ ] Send proposal (John, due 2026-11-25)\n", encoding="utf-8")
        build_calendar.MANUAL_FILE.write_text(json.dumps({
            "events": [{"date": "2026-12-05", "subject": "Dentist", "notes": ""}],
            "tasks": []}), encoding="utf-8")
        events, tasks, meetings = build_calendar.collect()
        self.assertEqual(len(events), 2)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(meetings, [{"title": "Weekly sync", "date": "2026-11-20",
                                     "folder": "weekly_20261120"}])
        sources = {e["subject"]: e["manual"] for e in events}
        self.assertEqual(sources, {"Launch plan review": False, "Dentist": True})

    def test_neg_corrupt_manual_store_fails_loudly(self):
        # calendar.json is the ONLY copy of manual data — a corrupt store must
        # stop the build, never quietly publish a page missing someone's notes.
        build_calendar.MANUAL_FILE.write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            build_calendar.collect()
        self.assertIn("not valid JSON", str(ctx.exception))
        self.assertIn("Nothing was built", str(ctx.exception))

    def test_neg_manual_entry_bad_date_skipped_with_warning(self):
        build_calendar.MANUAL_FILE.write_text(json.dumps({
            "events": [{"date": "not-a-date", "subject": "Ghost"}],
            "tasks": []}), encoding="utf-8")
        with mock.patch.object(sys, "stderr"):
            events, _, _ = build_calendar.collect()
        self.assertEqual(events, [], "an entry without a real date must not render")


class TestAddEntry(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.dir = Path(td.name)
        self.addCleanup(td.cleanup)
        self.old_manual = add_entry.MANUAL_FILE
        add_entry.MANUAL_FILE = self.dir / "calendar.json"

    def tearDown(self):
        add_entry.MANUAL_FILE = self.old_manual

    def test_scripted_task_creates_store(self):
        # Birth shape: no calendar.json yet — first add must create it.
        rc = add_entry.main(["--task", "--text", "Call John", "--due", "2026-11-25",
                             "--priority", "high", "--owner", "John"])
        self.assertEqual(rc, 0)
        data = json.loads(add_entry.MANUAL_FILE.read_text(encoding="utf-8"))
        self.assertEqual(data["tasks"][0], {"task": "Call John", "priority": "high",
                                            "due": "2026-11-25", "owner": "John"})
        self.assertEqual(data["events"], [])

    def test_scripted_event_appends(self):
        add_entry.main(["--event", "--date", "2026-11-30", "--subject", "Dentist"])
        add_entry.main(["--event", "--date", "2026-12-01", "--subject", "Follow-up",
                        "--notes", "bring x-ray"])
        data = json.loads(add_entry.MANUAL_FILE.read_text(encoding="utf-8"))
        self.assertEqual(len(data["events"]), 2)
        self.assertEqual(data["events"][1]["notes"], "bring x-ray")

    def test_neg_corrupt_store_refused_untouched(self):
        add_entry.MANUAL_FILE.write_text("{broken", encoding="utf-8")
        with self.assertRaises(SystemExit) as ctx:
            add_entry.main(["--event", "--date", "2026-11-30", "--subject", "X"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(add_entry.MANUAL_FILE.read_text(encoding="utf-8"), "{broken",
                         "a refused add must not have modified the store")


class TestPageBuild(unittest.TestCase):
    def test_payload_embedded_and_no_placeholder_left(self):
        payload = {"generated_at": "2026-11-18T00:00:00+00:00",
                   "events": [{"date": "2026-11-27", "subject": "Launch plan review",
                               "notes": "", "source": "Weekly sync", "manual": False}],
                   "tasks": [{"task": "Send proposal", "priority": "high",
                              "due": "2026-11-25", "owner": "John",
                              "source": "Weekly sync", "manual": False}],
                   "meetings": [{"title": "Weekly sync", "date": "2026-11-20",
                                 "folder": "weekly_20261120"}]}
        html = build_calendar.build_page(payload)
        self.assertNotIn("__PAYLOAD__", html)
        self.assertIn("Launch plan review", html)
        # hostile subject must arrive escaped inside the payload
        hostile = dict(payload, events=[dict(payload["events"][0],
                                             subject="</script><b>x")])
        hostile_html = build_calendar.build_page(hostile)
        self.assertNotIn("</script><b>x", hostile_html.split("<script id=\"data\"")[1].split("</script>")[0],
                         "payload must be sanitized against script breakout")

    def test_check_mode_reports_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            old_page = build_calendar.PAGE
            build_calendar.PAGE = Path(td) / "index.html"
            build_calendar.MANUAL_FILE = Path(td) / "calendar.json"
            build_calendar.TRANSCRIBER_OUTPUT = Path(td) / "output"
            try:
                rc = build_calendar.main(["--check"])
                self.assertEqual(rc, 0)
                self.assertFalse(build_calendar.PAGE.exists(), "--check must not write")
            finally:
                build_calendar.PAGE = old_page


if __name__ == "__main__":
    unittest.main(verbosity=2)
