#!/usr/bin/env python3
"""Meeting transcriber: audio file -> transcript + priorities + calendar.

Usage:
    python transcribe.py meeting.m4a
    python transcribe.py meeting.m4a --date 2026-11-10 --no-summary --open

Outputs (under output/<name>_<timestamp>/):
    transcript.txt   full transcript (always written, even if later steps fail)
    summary.md       overview + priorities + events
    tasks.md         checkbox priority list
    events.ics       calendar events — import into Google/Apple/Outlook

Or drag an audio file onto transcribe.cmd.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import asr_lib
import summarize

HERE = Path(__file__).resolve().parent


def _ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """RFC 5545: lines <= 75 octets; continuation lines start with a space."""
    out = []
    while len(line.encode("utf-8")) > 75:
        cut = 75
        while len(line[:cut].encode("utf-8")) > 75:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return "\r\n".join(out)


def write_ics(events: list[dict], path: Path) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//brother-tools//meeting transcriber//EN",
        "CALSCALE:GREGORIAN",
    ]
    for e in events:
        uid = hashlib.sha1(f"{e['date']}|{e['subject']}".encode("utf-8")).hexdigest()
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}@brother-tools",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{e['date'].replace('-', '')}",
            f"SUMMARY:{_ics_escape(e['subject'])}",
        ]
        if e.get("notes"):
            lines.append(f"DESCRIPTION:{_ics_escape(e['notes'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    path.write_bytes(("\r\n".join(_fold(l) for l in lines) + "\r\n").encode("utf-8"))


def write_tasks(priorities: list[dict], path: Path, title: str) -> None:
    lines = [f"# Priorities — {title}", ""]
    for level in ("high", "medium", "low"):
        group = [p for p in priorities if p["priority"] == level]
        if not group:
            continue
        lines.append(f"## {level.capitalize()}")
        for p in group:
            extra = []
            if p.get("owner"):
                extra.append(p["owner"])
            if p.get("due"):
                extra.append(f"due {p['due']}")
            suffix = f" ({', '.join(extra)})" if extra else ""
            lines.append(f"- [ ] {p['task']}{suffix}")
        lines.append("")
    undated = [p for p in priorities if not p.get("due")]
    if undated:
        lines.append(f"_{len(undated)} task(s) have no date — decide when._")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_summary(data: dict, asr: dict, path: Path, title: str,
                  meeting_day: str) -> None:
    lines = [
        f"# Meeting summary — {title}",
        f"_Meeting date: {meeting_day} · audio {asr['duration_s']:.0f}s · "
        f"ASR cost ~¥{asr['cost_yuan_est']:.2f}_",
        "",
        "## Overview",
        data["summary"] or "(no summary returned)",
        "",
        "## Priorities",
    ]
    if data["priorities"]:
        for p in data["priorities"]:
            extra = []
            if p.get("owner"):
                extra.append(p["owner"])
            if p.get("due"):
                extra.append(f"due {p['due']}")
            suffix = f" — {', '.join(extra)}" if extra else ""
            lines.append(f"- **[{p['priority']}]** {p['task']}{suffix}")
    else:
        lines.append("(none extracted)")
    lines += ["", "## Calendar events"]
    if data["events"]:
        for e in data["events"]:
            note = f" — {e['notes']}" if e.get("notes") else ""
            lines.append(f"- {e['date']}: {e['subject']}{note}")
        lines += ["", "_Import `events.ics` into your calendar app._"]
    else:
        lines.append("(no dated events found)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Transcribe a meeting recording and extract priorities + calendar events.")
    ap.add_argument("audio", help="audio file (wav/mp3/m4a/ogg/opus)")
    ap.add_argument("--date", help="meeting date YYYY-MM-DD (default: today) — anchors relative dates")
    ap.add_argument("--no-summary", action="store_true", help="transcript only, skip the DeepSeek step")
    ap.add_argument("--out", help="output folder (default: output/<name>_<timestamp>)")
    ap.add_argument("--open", action="store_true", help="open the output folder when done")
    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        print(f"ERROR: file not found: {audio}", file=sys.stderr)
        return 1
    meeting_day = args.date or date.today().isoformat()
    try:
        date.fromisoformat(meeting_day)
    except ValueError:
        print(f"ERROR: --date must be YYYY-MM-DD, got {meeting_day!r}", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else HERE / "output" / f"{audio.stem}_{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Transcribing {audio.name} ...")
    try:
        asr = asr_lib.transcribe(audio, lane="meeting")
    except Exception as exc:
        print(f"ERROR (transcription): {exc}", file=sys.stderr)
        return 1
    (out_dir / "transcript.txt").write_text(asr["transcript"], encoding="utf-8")
    print(f"  {asr['duration_s']:.0f}s audio, {asr['segments']} segment(s), "
          f"~¥{asr['cost_yuan_est']:.2f} -> transcript.txt")
    if not asr["transcript"].strip():
        print("WARNING: transcript is empty — nothing to summarize.", file=sys.stderr)
        return 1

    if not args.no_summary:
        print("Extracting priorities and events ...")
        try:
            data = summarize.summarize(asr["transcript"], meeting_day=meeting_day)
        except Exception as exc:
            print(f"ERROR (summary): {exc}", file=sys.stderr)
            print("Transcript is saved; fix the key/network and re-run with the same file.", file=sys.stderr)
            return 1
        title = audio.stem
        write_summary(data, asr, out_dir / "summary.md", title, meeting_day)
        write_tasks(data["priorities"], out_dir / "tasks.md", title)
        write_ics(data["events"], out_dir / "events.ics")
        print(f"  {len(data['priorities'])} priorities, {len(data['events'])} events "
              f"-> summary.md, tasks.md, events.ics")

    print(f"Done: {out_dir}")
    if args.open:
        try:
            import os
            os.startfile(out_dir)  # Windows
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
