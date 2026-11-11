#!/usr/bin/env python3
"""Meeting transcript -> summary, priorities, calendar events (DeepSeek).

Adapts the strict-JSON parse pattern from Allen's calendar intake: ONE call,
temperature 0, JSON-only reply, validated before anything is written.
Relative dates resolve against the meeting date, never against parse time.

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


def _validate(data: dict, meeting_day: str) -> dict:
    """Coerce the model's JSON into the known shape; drop unusable rows."""
    if not isinstance(data, dict):
        raise RuntimeError("parse response is not a JSON object")

    summary = str(data.get("summary") or "").strip()

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

    return {"summary": summary, "priorities": priorities, "events": events}


def summarize(transcript: str, meeting_day: str | None = None) -> dict:
    """One DeepSeek call -> {summary, priorities[], events[]}, validated."""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY not set — copy .env.example to .env and paste your key"
        )
    meeting_day = meeting_day or date.today().isoformat()
    system = (
        "You extract action items and calendar events from a meeting transcript. "
        f"The meeting date (TODAY for relative dates) is {meeting_day}. "
        "Return ONLY a strict JSON object with three keys: "
        "\"summary\" (3-5 sentences covering what was discussed and decided), "
        "\"priorities\" (array of objects: \"task\" <=100 chars, \"owner\" "
        "(person's name or null), \"due\" (\"YYYY-MM-DD\" or null), \"priority\" "
        "(\"high\"|\"medium\"|\"low\")), "
        "\"events\" (array of objects: \"date\" \"YYYY-MM-DD\", \"subject\" <=60 "
        "chars, \"notes\" <=80 chars). "
        "Resolve relative dates ('next Friday', 'the 20th') against the meeting "
        "date; a bare month-name day means the NEXT future occurrence. "
        "Only include events with a clear date; undated tasks get due=null. "
        "Rank by what the speakers emphasized as urgent or blocking. "
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
    return _validate(json.loads(content[start:end + 1]), meeting_day)
