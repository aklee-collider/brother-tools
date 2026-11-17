#!/usr/bin/env python3
"""My Work Calendar — one page with ALL the work, from one source of truth.

Reads (never duplicates) the transcriber's results:
  transcriber/output/<meeting>/tasks.md   -> open tasks
  transcriber/output/<meeting>/events.ics -> dated events
  transcriber/output/<meeting>/asr_meta.json -> meeting titles + dates
plus your own manual entries in calendar/calendar.json (add them with
add.cmd), and renders a self-contained index.html: a month grid + open-task
list + upcoming events.

Stdlib only. Rebuild any time:  double-click open.cmd  (or
python build_calendar.py). Delete a manual entry: edit calendar.json in
Notepad and rebuild. Meet outputs are re-read on every build, so new
meetings appear automatically.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import webbrowser
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TRANSCRIBER_OUTPUT = ROOT / "transcriber" / "output"
MANUAL_FILE = HERE / "calendar.json"
PAGE = HERE / "index.html"
PRIORITIES = ("high", "medium", "low")


# ---------------------------------------------------------------- parsing
def parse_tasks_md(path: Path, source: str) -> list[dict]:
    """Parse write_tasks output: '## High' sections + '- [ ] task (owner, due YYYY-MM-DD)'.

    A malformed line keeps its task text and loses only what it misstates —
    a broken date must not silently drop the task itself.
    """
    tasks: list[dict] = []
    level = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        m = re.match(r"^##\s+(High|Medium|Low)$", line, re.IGNORECASE)
        if m:
            level = m.group(1).lower()
            continue
        m = re.match(r"^-\s+\[[ xX]\]\s+(.+)$", line)
        if not m:
            continue
        text = m.group(1).strip()
        owner, due = "", ""
        m = re.match(r"^(.*?)\s*\((.+)\)$", text)
        if m:
            text = m.group(1).strip()
            for part in m.group(2).split(","):
                part = part.strip()
                if part.startswith("due "):
                    candidate = part[4:].strip()
                    try:
                        date.fromisoformat(candidate)
                        due = candidate
                    except ValueError:
                        pass  # not a real date — leave it out, keep the task
                elif part:
                    owner = part
        tasks.append({"task": text, "priority": level or "medium",
                      "due": due, "owner": owner, "source": source, "manual": False})
    return tasks


def _ics_unescape(text: str) -> str:
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            c = text[i + 1]
            out.append("\n" if c == "n" else c)
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def parse_ics(path: Path, source: str) -> list[dict]:
    """Parse write_ics output: unfold continuation lines, read VEVENTs."""
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]  # RFC 5545 unfolding
        else:
            lines.append(raw)
    events: list[dict] = []
    cur: dict | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT":
            if cur and cur.get("date") and cur.get("subject"):
                events.append({"date": cur["date"], "subject": cur["subject"],
                               "notes": cur.get("notes", ""), "source": source,
                               "manual": False})
            cur = None
        elif cur is not None and ":" in line:
            name, value = line.split(":", 1)
            name = name.split(";")[0].upper()
            value = _ics_unescape(value)
            if name == "DTSTART":
                m = re.match(r"^(\d{4})(\d{2})(\d{2})", value)
                if m:
                    cur["date"] = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            elif name == "SUMMARY":
                cur["subject"] = value.strip()
            elif name == "DESCRIPTION":
                cur["notes"] = value.strip()
    return events


def load_manual() -> tuple[list[dict], list[dict]]:
    """Manual entries from calendar.json. Missing file = fresh install (no
    entries). Corrupt file = loud failure — it is the only copy of that data."""
    if not MANUAL_FILE.exists():
        return [], []
    try:
        data = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(
            f"{MANUAL_FILE.name} is not valid JSON ({exc}) — fix it in Notepad "
            "(it holds your manually added entries) and re-run. Nothing was built."
        ) from exc
    events: list[dict] = []
    tasks: list[dict] = []
    for e in data.get("events") or []:
        try:
            date.fromisoformat(str(e.get("date", "")))
        except ValueError:
            print(f"WARNING: manual event skipped (bad or missing date): {e!r}",
                  file=sys.stderr)
            continue
        events.append({"date": e["date"], "subject": str(e.get("subject") or "").strip(),
                       "notes": str(e.get("notes") or "").strip(),
                       "source": "added by you", "manual": True})
    for t in data.get("tasks") or []:
        text = str(t.get("task") or "").strip()
        if not text:
            print(f"WARNING: manual task skipped (empty text): {t!r}", file=sys.stderr)
            continue
        due = str(t.get("due") or "").strip()
        if due:
            try:
                date.fromisoformat(due)
            except ValueError:
                print(f"WARNING: manual task '{text}' has a bad due date ({due!r}) "
                      "— listing it without a date instead.", file=sys.stderr)
                due = ""
        tasks.append({"task": text,
                      "priority": str(t.get("priority") or "medium").lower(),
                      "due": due, "owner": str(t.get("owner") or "").strip(),
                      "source": "added by you", "manual": True})
    return events, tasks


def collect() -> tuple[list[dict], list[dict], list[dict]]:
    """Gather everything. Returns (events, tasks, meetings)."""
    events: list[dict] = []
    tasks: list[dict] = []
    meetings: list[dict] = []
    if TRANSCRIBER_OUTPUT.is_dir():
        for folder in sorted(TRANSCRIBER_OUTPUT.iterdir()):
            if not folder.is_dir() or folder.name in ("processed", "failed"):
                continue
            meta = {}
            meta_path = folder / "asr_meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except ValueError:
                    meta = {}
            source = str(meta.get("title") or folder.name)
            if meta.get("meeting_day"):
                meetings.append({"title": source, "date": meta["meeting_day"],
                                 "folder": folder.name})
            if (folder / "events.ics").exists():
                events += parse_ics(folder / "events.ics", source)
            if (folder / "tasks.md").exists():
                tasks += parse_tasks_md(folder / "tasks.md", source)
    manual_events, manual_tasks = load_manual()
    return events + manual_events, tasks + manual_tasks, meetings


# ---------------------------------------------------------------- page
def build_page(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>My Work Calendar</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #f4f5f7; color: #1c1e21;
         font: 16px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .wrap { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px; margin-bottom: 12px; }
  h1 { font-size: 26px; margin: 0; }
  .stamp { color: #667; font-size: 13px; }
  .card { background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
          padding: 18px 22px; margin-top: 16px; }
  .card h2 { font-size: 18px; margin: 0 0 10px; }
  table.cal { width: 100%; border-collapse: collapse; table-layout: fixed; }
  table.cal th { font-size: 12px; color: #667; padding: 6px 4px; text-align: center; }
  table.cal td { border: 1px solid #eef0f2; vertical-align: top; height: 76px;
                 padding: 4px; font-size: 13px; }
  td.off { background: #fafbfc; }
  td.today { background: #eef4ff; }
  .dnum { font-weight: 600; color: #445; }
  .chip { display: block; margin-top: 3px; padding: 1px 6px; border-radius: 8px;
          background: #eef4ff; color: #14459c; font-size: 12px; overflow: hidden;
          text-overflow: ellipsis; white-space: nowrap; }
  .chip.task { background: #fff3cd; color: #7a5b00; }
  .chip.task.overdue { background: #fde8e8; color: #b42318; }
  .nav { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
  .nav button { border: 1px solid #d8dbe0; background: #fff; border-radius: 6px;
                padding: 4px 12px; cursor: pointer; font-size: 14px; }
  .nav button:hover { background: #f0f2f5; }
  #mlabel { font-weight: 600; min-width: 150px; text-align: center; }
  ol { margin: 0; padding-left: 22px; }
  li { padding: 6px 0; border-top: 1px solid #f0f1f3; }
  li:first-child { border-top: 0; }
  .meta { color: #889; font-size: 12px; margin-left: 6px; white-space: nowrap; }
  .overdue { color: #b42318; font-weight: 600; }
  .badge { display: inline-block; background: #fde8e8; color: #b42318;
           border-radius: 8px; padding: 0 8px; font-size: 12px; margin-left: 6px; }
  .empty { color: #889; font-style: italic; }
  .hint { color: #667; font-size: 13px; margin-top: 10px; }
  code { background: #eceef1; border-radius: 4px; padding: 1px 5px; font-size: 12px; }
  .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  @media (max-width: 700px) { .cols { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>My Work Calendar</h1>
    <span class="stamp" id="stamp"></span>
  </header>
  <div class="card">
    <div class="nav">
      <button id="prev">&larr; previous</button>
      <span id="mlabel"></span>
      <button id="next">next &rarr;</button>
      <button id="todaybtn">today</button>
    </div>
    <div id="grid"></div>
    <p class="hint">Blue = scheduled event &middot; yellow = task due that day (red = overdue).
       Rebuild after new meetings: <code>open.cmd</code></p>
  </div>
  <div class="cols">
    <div class="card">
      <h2>Open tasks</h2>
      <div id="tasks"></div>
      <p class="hint">From meeting notes + your own additions. Add one: <code>add.cmd</code></p>
    </div>
    <div class="card">
      <h2>Upcoming events</h2>
      <div id="events"></div>
      <p class="hint">Next 90 days. Import per-meeting files too:
         <code>output\\&lt;meeting&gt;\\events.ics</code></p>
    </div>
  </div>
  <div class="card">
    <h2>Meetings processed</h2>
    <div id="meetings"></div>
  </div>
</div>
<script id="data" type="application/json">__PAYLOAD__</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById("data").textContent);
  var today = new Date(); today.setHours(0, 0, 0, 0);
  var y = today.getFullYear(), m = today.getMonth();
  var MONTHS = ["January","February","March","April","May","June","July",
                "August","September","October","November","December"];
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }
  function iso(d) {
    return d.getFullYear() + "-" + String(d.getMonth()+1).padStart(2,"0") +
           "-" + String(d.getDate()).padStart(2,"0");
  }
  function byDate(dateStr) {
    var p = dateStr.split("-");
    return new Date(+p[0], +p[1]-1, +p[2]);
  }
  document.getElementById("stamp").textContent =
    "built " + new Date(data.generated_at).toLocaleString();
  function chip(item, cls, isTask) {
    var overdue = isTask && item.due && byDate(item.due) < today;
    return '<span class="chip ' + cls + (overdue ? " overdue" : "") + '">' +
           esc(item.subject || item.task) + "</span>";
  }
  function render() {
    document.getElementById("mlabel").textContent = MONTHS[m] + " " + y;
    var first = new Date(y, m, 1);
    var start = new Date(y, m, 1 - first.getDay());  // Sunday-first grid
    var eventsByDay = {}, tasksByDay = {};
    data.events.forEach(function (e) {
      (eventsByDay[e.date] = eventsByDay[e.date] || []).push(e);
    });
    data.tasks.forEach(function (t) {
      if (t.due) (tasksByDay[t.due] = tasksByDay[t.due] || []).push(t);
    });
    var html = "<table class='cal'><tr>" +
      ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"].map(function (d) {
        return "<th>" + d + "</th>"; }).join("") + "</tr>";
    for (var row = 0; row < 6; row++) {
      html += "<tr>";
      for (var col = 0; col < 7; col++) {
        var d = new Date(start.getFullYear(), start.getMonth(), start.getDate() +
                         row*7 + col);
        var key = iso(d);
        var cls = d.getMonth() === m ? "" : "off";
        if (key === iso(today)) cls += " today";
        html += "<td class='" + cls + "'><span class='dnum'>" + d.getDate() + "</span>";
        (eventsByDay[key] || []).forEach(function (e) {
          html += chip(e, "", false);
        });
        (tasksByDay[key] || []).forEach(function (t) {
          html += chip(t, "task", true);
        });
        html += "</td>";
      }
      html += "</tr>";
    }
    document.getElementById("grid").innerHTML = html + "</table>";
  }
  document.getElementById("prev").onclick = function () {
    m--; if (m < 0) { m = 11; y--; } render();
  };
  document.getElementById("next").onclick = function () {
    m++; if (m > 11) { m = 0; y++; } render();
  };
  document.getElementById("todaybtn").onclick = function () {
    y = today.getFullYear(); m = today.getMonth(); render();
  };
  render();

  // Open tasks: grouped by priority, overdue first within groups
  var host = document.getElementById("tasks");
  var prioOrder = ["high", "medium", "low", "no date"];
  var groups = {};
  data.tasks.forEach(function (t) {
    var g = t.due ? (t.priority || "medium") : "no date";
    (groups[g] = groups[g] || []).push(t);
  });
  var any = false;
  prioOrder.forEach(function (g) {
    var items = (groups[g] || []).sort(function (a, b) {
      return (a.due || "9999") < (b.due || "9999") ? -1 : 1;
    });
    if (!items.length) return;
    any = true;
    var h = "<p style='margin:10px 0 4px'><strong>" + esc(g) + "</strong></p><ol>";
    items.forEach(function (t) {
      var overdue = t.due && byDate(t.due) < today;
      h += "<li" + (overdue ? " class='overdue'" : "") + ">" + esc(t.task);
      if (overdue) h += "<span class='badge'>overdue</span>";
      var bits = [];
      if (t.owner) bits.push(t.owner);
      if (t.due) bits.push("due " + t.due);
      bits.push(t.manual ? "added by you" : "from: " + t.source);
      h += "<span class='meta'>" + esc(bits.join(" · ")) + "</span></li>";
    });
    h += "</ol>";
    host.insertAdjacentHTML("beforeend", h);
  });
  if (!any) host.innerHTML = "<p class='empty'>No open tasks. They appear here " +
    "automatically when a meeting is processed, or add your own with add.cmd.</p>";

  // Upcoming events: next 90 days
  var host2 = document.getElementById("events");
  var upcoming = data.events.filter(function (e) {
    if (!e.date) return false;
    var d = byDate(e.date);
    var diff = (d - today) / 86400000;
    return diff >= 0 && diff <= 90;
  }).sort(function (a, b) { return a.date < b.date ? -1 : 1; });
  if (upcoming.length) {
    var h2 = "<ol>";
    upcoming.forEach(function (e) {
      h2 += "<li><strong>" + esc(e.date) + "</strong> — " + esc(e.subject);
      var bits = [e.manual ? "added by you" : "from: " + e.source];
      if (e.notes) bits.push(e.notes);
      h2 += "<span class='meta'>" + esc(bits.join(" · ")) + "</span></li>";
    });
    host2.innerHTML = h2 + "</ol>";
  } else {
    host2.innerHTML = "<p class='empty'>Nothing scheduled in the next 90 days.</p>";
  }

  // Meetings processed (most recent first)
  var host3 = document.getElementById("meetings");
  var meets = (data.meetings || []).slice().sort(function (a, b) {
    return a.date > b.date ? -1 : 1;
  });
  if (meets.length) {
    var h3 = "<ol>";
    meets.forEach(function (mt) {
      h3 += "<li><strong>" + esc(mt.date) + "</strong> — " + esc(mt.title) +
            "<span class='meta'>output\\\\" + esc(mt.folder) + "</span></li>";
    });
    host3.innerHTML = h3 + "</ol>";
  } else {
    host3.innerHTML = "<p class='empty'>No meetings processed yet. Drag a recording " +
      "onto transcriber\\\\transcribe.cmd, or sync one into transcriber\\\\inbox while " +
      "the watcher is running — its tasks and events appear here on the next build.</p>";
  }
})();
</script>
</body>
</html>
""".replace("__PAYLOAD__", body)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build the My Work Calendar page.")
    ap.add_argument("--open", action="store_true", help="open the page when built")
    ap.add_argument("--check", action="store_true",
                    help="report what would be included without writing the page")
    args = ap.parse_args(argv)

    try:
        events, tasks, meetings = collect()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.check:
        print(f"{len(events)} event(s), {len(tasks)} task(s), {len(meetings)} meeting(s)")
        return 0
    generated_at = datetime.now(timezone.utc).isoformat()
    PAGE.write_text(build_page({
        "generated_at": generated_at, "events": events,
        "tasks": tasks, "meetings": meetings,
    }), encoding="utf-8")
    manual_n = sum(1 for e in events if e["manual"]) + sum(1 for t in tasks if t["manual"])
    print(f"Built {PAGE.name}: {len(events)} event(s), {len(tasks)} task(s) "
          f"({manual_n} added by you), {len(meetings)} meeting(s)")
    if args.open:
        webbrowser.open(PAGE.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
