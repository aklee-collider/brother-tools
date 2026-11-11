#!/usr/bin/env python3
"""Meeting transcript -> summary, priorities, events, communication insights.

Adapts the strict-JSON parse pattern from Allen's calendar intake: ONE call,
temperature 0, JSON-only reply, validated before anything is written.
Relative dates resolve against the meeting date, never against parse time.

v2: tags the meeting with an active project (projects.json) and analyzes
communication effectiveness — outcome, objections, and concrete persuasion
recommendations for the next conversation when the goal was not achieved.

Setup: DEEPSEEK_API_KEY in .env (platform.deepseek.com).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
MAX_EVENT_DAYS = 365
OUTCOMES = ("achieved", "partial", "not_achieved", "unclear")


def _clean_str_list(raw, cap: int = 6, maxlen: int = 400) -> list[str]:
    if not isinstance(raw, list):
        return []  # a bare string would iterate char-by-char — reject, don't coerce
    out = []
    for x in raw:
        s = str(x).strip()
        if s:
            out.append(s[:maxlen])
        if len(out) >= cap:
            break
    return out


def _validate(data: dict, meeting_day: str, project_keys: set[str]) -> dict:
    """Coerce the model's JSON into the known shape; drop unusable rows."""
    if not isinstance(data, dict):
        raise RuntimeError("parse response is not a JSON object")

    summary = str(data.get("summary") or "").strip()

    project = data.get("project")
    if not (isinstance(project, str) and project in project_keys):
        project = None

    priorities = []
    for p in data.get("priorities") or []:
        if not isinstance(p, dict):
            continue
        task = str(p.get("task") or "").strip()
        if not task:
            continue
        due = p.get("due")
        if not (isinstance(due, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", due)):
            due = None
        else:
            try:
                date.fromisoformat(due)
            except ValueError:
                due = None
        priority = str(p.get("priority") or "medium").lower()
        if priority not in ("high", "medium", "low"):
            priority = "medium"
        owner = p.get("owner")
        owner = str(owner).strip() if owner else None
        priorities.append({"task": task[:200], "owner": owner, "due": due,
                           "priority": priority})

    events = []
    for e in data.get("events") or []:
        if not isinstance(e, dict):
            continue
        d = e.get("date")
        subject = str(e.get("subject") or "").strip()
        if not subject or not (isinstance(d, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)):
            continue
        try:
            dd = date.fromisoformat(d)
        except ValueError:
            continue
        delta = (dd - date.fromisoformat(meeting_day)).days
        if delta < 0 or delta > MAX_EVENT_DAYS:
            continue  # past or implausibly far out — never silently calendar it
        events.append({"date": d, "subject": subject[:60],
                       "notes": str(e.get("notes") or "").strip()[:80]})

    ins_raw = data.get("insights")
    ins_raw = ins_raw if isinstance(ins_raw, dict) else {}
    outcome = str(ins_raw.get("outcome") or "unclear").lower()
    if outcome not in OUTCOMES:
        outcome = "unclear"
    insights = {
        "outcome": outcome,
        "objections": _clean_str_list(ins_raw.get("objections")),
        "recommendations": _clean_str_list(ins_raw.get("recommendations")),
    }

    return {"project": project, "summary": summary, "priorities": priorities,
            "events": events, "insights": insights}


def summarize(transcript: str, meeting_day: str | None = None,
              projects: list[dict] | None = None,
              project_key: str | None = None) -> dict:
    """One DeepSeek call -> validated {project, summary, priorities, events,
    insights}. projects: [{key, name, context}]; project_key forces the tag."""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set — copy .env.example to .env and paste your key"
        )
    meeting_day = meeting_day or date.today().isoformat()
    projects = projects or []
    project_keys = {p["key"] for p in projects}
    if projects:
        proj_lines = "\n".join(f"- {p['key']}: {p['name']} — {p['context']}"
                               for p in projects)
        project_rule = (
            "The reader's active projects:\n" + proj_lines + "\n"
            'Tag "project" with the best-matching project key, or null if none fit. '
        )
    else:
        project_rule = 'Set "project" to null. '
    system = (
        "You extract action items, calendar events, and communication insights "
        "from a meeting transcript. "
        f"The meeting date (TODAY for relative dates) is {meeting_day}. "
        + project_rule +
        "Return ONLY a strict JSON object with these keys: "
        "\"project\" (project key or null), "
        "\"summary\" (3-5 sentences covering what was discussed and decided), "
        "\"priorities\" (array of objects: \"task\" <=100 chars, \"owner\" "
        "(person's name or null), \"due\" (\"YYYY-MM-DD\" or null), \"priority\" "
        "(\"high\"|\"medium\"|\"low\")), "
        "\"events\" (array of objects: \"date\" \"YYYY-MM-DD\", \"subject\" <=60 "
        "chars, \"notes\" <=80 chars), "
        "\"insights\" (object: \"outcome\" (\"achieved\"|\"partial\"|\"not_achieved\""
        "|\"unclear\" — was the meeting's goal reached?), \"objections\" (array of "
        "concerns or resistance raised, close to how they were said), "
        "\"recommendations\" (array of concrete advice for the NEXT conversation "
        "to improve communication and persuasion — phrasing to use, evidence to "
        "bring, questions to ask, follow-up timing — especially when the outcome "
        "was not achieved, e.g. a prospect decided to move on; tailor to the "
        "project context when known)). "
        "Resolve relative dates ('next Friday', 'the 20th') against the meeting "
        "date; a bare month-name day means the NEXT future occurrence. "
        "Only include events with a clear date; undated tasks get due=null. "
        "Rank priorities by what the speakers emphasized as urgent or blocking. "
        "For purely informational meetings use outcome=\"unclear\" and keep "
        "recommendations minimal. "
        "No markdown fences, no commentary."
    )
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": transcript}],
        "temperature": 0,
        "max_tokens": 16384,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    msg = body["choices"][0]["message"]
    content = msg.get("content") or ""
    if not content.strip():
        content = msg.get("reasoning_content") or ""
    content = re.sub(r"```(?:json)?", "", content).strip()
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError("parse response contained no JSON object")
    data = _validate(json.loads(content[start:end + 1]), meeting_day, project_keys)
    if project_key:
        data["project"] = project_key
    return data
