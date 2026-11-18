#!/usr/bin/env python3
"""Meeting transcriber: audio file -> transcript + priorities + calendar + insights.

Usage:
    python transcribe.py meeting.m4a
    python transcribe.py meeting.m4a --project westcliff --date 2026-11-10
    python transcribe.py meeting.m4a --no-summary --open
    python transcribe.py --resume output/meeting_20261117-120000 --date 2026-11-15

--resume re-runs ONLY the summary step from the saved transcript: use it when
the summary step failed (no re-transcription, no ASR cost again) or when the
assumed meeting date was wrong (pass --date to correct it).

Outputs (under output/<name>_<timestamp>/):
    transcript.txt   full transcript (always written, even if later steps fail)
    asr_meta.json    transcription facts (duration, segments, cost, date) for --resume
    summary.md       overview + priorities + events + communication insights
    tasks.md         checkbox priority list (outstanding tasks)
    events.ics       calendar events — import into Google/Apple/Outlook

Or drag an audio file onto transcribe.cmd. For phone automation, run watch.cmd
and sync your phone's recordings into inbox/ (see README).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import asr_lib
import summarize

HERE = Path(__file__).resolve().parent
OUTCOME_LABELS = {
    "achieved": "goal achieved",
    "partial": "partially achieved",
    "not_achieved": "goal NOT achieved",
    "unclear": "unclear",
}


def load_projects() -> list[dict]:
    try:
        data = json.loads((HERE / "projects.json").read_text(encoding="utf-8"))
        return [p for p in data if isinstance(p, dict) and p.get("key")]
    except (OSError, ValueError):
        return []


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
    lines = [f"# Outstanding tasks — {title}", ""]
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
                  meeting_day: str, projects: list[dict] | None = None,
                  date_note: str = "") -> None:
    proj_name = None
    if data.get("project"):
        for p in projects or []:
            if p["key"] == data["project"]:
                proj_name = p.get("name") or p["key"]
                break
        proj_name = proj_name or data["project"]
    lines = [
        f"# Meeting summary — {title}",
        f"_Meeting date: {meeting_day}"
        + (f" — {date_note}" if date_note else "")
        + f" · audio {asr['duration_s']:.0f}s · "
        f"ASR cost ~¥{asr['cost_yuan_est']:.2f}"
        + (f" · project: {proj_name}_" if proj_name else "_"),
        "",
        "## Overview",
        data["summary"] or "(no summary returned)",
        "",
        "## Outstanding tasks",
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

    ins = data.get("insights") or {}
    if ins.get("objections") or ins.get("recommendations") or ins.get("outcome") not in (None, "unclear"):
        lines += ["", "## Communication insights",
                  f"_Outcome: {OUTCOME_LABELS.get(ins.get('outcome'), 'unclear')}_"]
        if ins.get("objections"):
            lines += ["", "**Objections raised**"]
            lines += [f"- {o}" for o in ins["objections"]]
        if ins.get("recommendations"):
            lines += ["", "**For the next conversation**"]
            lines += [f"- {r}" for r in ins["recommendations"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_CLAIM_MARKER_FD: int | None = None


def main() -> int:
    ap = argparse.ArgumentParser(description="Transcribe a meeting recording and extract priorities, calendar events, and communication insights.")
    ap.add_argument("audio", nargs="?", help="audio file (wav/mp3/m4a/ogg/opus); omit when using --resume")
    ap.add_argument("--resume", metavar="OUT_DIR",
                    help="re-run the summary step from a saved transcript (no ASR cost); "
                         "combine with --date to correct the meeting date")
    ap.add_argument("--date", help="meeting date YYYY-MM-DD (default: today) — anchors relative dates")
    ap.add_argument("--date-assumed", action="store_true",
                    help="(used by watch.cmd) label the date as assumed from the recording file")
    ap.add_argument("--project", help="project key from projects.json (e.g. westcliff, 2nimbus); default: inferred from the transcript")
    ap.add_argument("--no-summary", action="store_true", help="transcript only, skip the DeepSeek step")
    ap.add_argument("--out", help="output folder (default: output/<name>_<timestamp>)")
    ap.add_argument("--open", action="store_true", help="open the output folder when done")
    args = ap.parse_args()

    projects = load_projects()
    if args.project and args.project not in {p["key"] for p in projects}:
        keys = ", ".join(p["key"] for p in projects) or "(none defined)"
        print(f"ERROR: unknown project {args.project!r} — known keys: {keys}", file=sys.stderr)
        return 1
    if args.date:
        try:
            date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: --date must be YYYY-MM-DD, got {args.date!r}", file=sys.stderr)
            return 1

    asr: dict
    title: str
    if args.resume:
        if args.audio:
            print("ERROR: --resume works from the saved transcript — do not pass an audio file too.",
                  file=sys.stderr)
            return 1
        out_dir = Path(args.resume)
        tpath = out_dir / "transcript.txt"
        mpath = out_dir / "asr_meta.json"
        if not tpath.exists() or not mpath.exists():
            print(f"ERROR: {out_dir} has no transcript.txt/asr_meta.json to resume from.",
                  file=sys.stderr)
            return 1
        meta = json.loads(mpath.read_text(encoding="utf-8"))
        transcript = tpath.read_text(encoding="utf-8")
        asr = {
            "transcript": transcript,
            "duration_s": meta.get("duration_s", 0.0),
            "segments": meta.get("segments", 0),
            "segments_empty": meta.get("segments_empty", 0),
            "cost_yuan_est": meta.get("cost_yuan_est", 0.0),
            "model": meta.get("model", ""),
        }
        title = meta.get("title") or out_dir.name
        meeting_day = args.date or meta.get("meeting_day") or date.today().isoformat()
        date_note = "corrected date" if args.date else meta.get("date_note", "")
        print(f"Resuming from saved transcript in {out_dir.name} (no transcription cost).")
    else:
        if not args.audio:
            ap.error("an audio file is required (or use --resume OUT_DIR)")
        audio = Path(args.audio)
        # Ownership-first (watcher runs only): claim the recording by taking
        # the byte lock on its marker BEFORE touching it. Recovery must win
        # the same lock before moving the claim, so the OS decides who owns
        # the recording — there is no window between our claim and our first
        # read. If the marker cannot be opened, locked, or written, we refuse
        # to start: an unrecognizable transcription must not consume the
        # recording. The lock (and fd) stay held for the whole run; the OS
        # releases them when this process exits or crashes.
        if audio.name.endswith(".processing"):
            global _CLAIM_MARKER_FD
            marker = audio.with_name(audio.name + ".pid")
            try:
                fd = os.open(marker, os.O_RDWR | os.O_CREAT)
            except OSError as exc:
                print(f"ERROR: cannot open the processing marker {marker.name}: {exc} "
                      f"— refusing to start so the recording stays safe.", file=sys.stderr)
                return 1
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                print("ERROR: the processing marker is locked — a watcher is recovering "
                      "this recording. Not starting.", file=sys.stderr)
                return 1
            try:
                os.ftruncate(fd, 0)
                os.write(fd, str(os.getpid()).encode("utf-8"))
            except OSError as exc:
                os.close(fd)
                print(f"ERROR: cannot write our PID to the processing marker {marker.name}: "
                      f"{exc} — refusing to start so the recording stays safe.", file=sys.stderr)
                return 1
            _CLAIM_MARKER_FD = fd  # held until process exit; kept for diagnostics/tests
        if not audio.exists():
            print(f"ERROR: file not found: {audio}", file=sys.stderr)
            return 1
        meeting_day = args.date or date.today().isoformat()
        if args.date_assumed:
            date_note = ("assumed from the recording file — wrong? fix with: "
                         "python transcribe.py --resume <this folder> --date YYYY-MM-DD")
        elif args.date:
            date_note = ""
        else:
            date_note = ("today — if the meeting was earlier, re-run with --date YYYY-MM-DD")
        title = audio.stem

        out_dir = Path(args.out) if args.out else HERE / "output" / f"{audio.stem}_{datetime.now():%Y%m%d-%H%M%S}"
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"Transcribing {audio.name} ...")
        try:
            asr = asr_lib.transcribe(audio, lane="meeting")
        except Exception as exc:
            print(f"ERROR (transcription): {exc}", file=sys.stderr)
            return 1
        (out_dir / "transcript.txt").write_text(asr["transcript"], encoding="utf-8")
        (out_dir / "asr_meta.json").write_text(json.dumps({
            "title": title,
            "audio": audio.name,
            "meeting_day": meeting_day,
            "date_note": date_note,
            "duration_s": asr["duration_s"],
            "segments": asr["segments"],
            "segments_empty": asr.get("segments_empty", 0),
            "cost_yuan_est": asr["cost_yuan_est"],
            "model": asr["model"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {asr['duration_s']:.0f}s audio, {asr['segments']} segment(s), "
              f"~¥{asr['cost_yuan_est']:.2f} -> transcript.txt")
        if asr.get("segments_empty"):
            print(f"WARNING: {asr['segments_empty']} of {asr['segments']} audio segments "
                  "returned no text — the transcript may be missing parts of the meeting.",
                  file=sys.stderr)
        if not asr["transcript"].strip():
            print("WARNING: transcript is empty — nothing to summarize.", file=sys.stderr)
            return 1

    if not args.no_summary:
        print("Extracting priorities, events, and insights ...")
        try:
            data = summarize.summarize(asr["transcript"], meeting_day=meeting_day,
                                       projects=projects, project_key=args.project)
        except Exception as exc:
            print(f"ERROR (summary): {exc}", file=sys.stderr)
            print("Transcript is saved — no transcription cost to repeat. Once the "
                  "key/network is fixed, resume with:\n"
                  f"  python transcribe.py --resume {out_dir}", file=sys.stderr)
            return 1
        write_summary(data, asr, out_dir / "summary.md", title, meeting_day, projects,
                      date_note=date_note)
        write_tasks(data["priorities"], out_dir / "tasks.md", title)
        write_ics(data["events"], out_dir / "events.ics")
        print(f"  project: {data.get('project') or '(none)'} | "
              f"{len(data['priorities'])} tasks, {len(data['events'])} events, "
              f"outcome: {data['insights']['outcome']} "
              f"-> summary.md, tasks.md, events.ics")

    print(f"Done: {out_dir}")
    if args.open:
        try:
            os.startfile(out_dir)  # Windows
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
