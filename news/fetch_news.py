#!/usr/bin/env python3
"""Daily categorized news briefing.

Fetches the feed pool in categories.json, then (if DEEPSEEK_API_KEY is set)
asks DeepSeek to pick the most notable items per category and write a short
summary of each area. Renders a self-contained index.html with clickable
article links. Stdlib + python-dotenv only.

Integrity contract: the LLM selects by candidate id only — titles and URLs
always come from the fetched candidates, never from the model. A category
with nothing relevant says so instead of inventing news. Without a key (or
if the API fails) the page degrades to raw categorized headlines with a
visible note. A feed that fails is listed in the footer; it never takes the
page down.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; BrotherToolsNews/1.0)"
ATOM = "{http://www.w3.org/2005/Atom}"
SNIPPET_LEN = 200
FRESH_HOURS = 72
MAX_PICK = 5
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(text: str) -> str:
    s = _Stripper()
    try:
        s.feed(text or "")
        s.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", " ".join(s.parts)).strip()


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read()


def parse_feed(xml_bytes: bytes, max_items: int, is_google_news: bool) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    if root.tag == "rss" or root.tag.endswith("rss"):
        nodes = root.findall("./channel/item")
    elif root.tag == f"{ATOM}feed":
        nodes = root.findall(f"{ATOM}entry")
    else:
        nodes = root.findall("./channel/item") or root.findall(f".//{ATOM}entry")
    items = []
    for node in nodes[:max_items]:
        if node.tag == "item":
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            published = parse_date(node.findtext("pubDate"))
            desc = node.findtext("description") or ""
        else:
            title = (node.findtext(f"{ATOM}title") or "").strip()
            link = ""
            for ln in node.findall(f"{ATOM}link"):
                if ln.get("rel") in (None, "alternate") and ln.get("href"):
                    link = ln.get("href")
                    break
            published = parse_date(
                node.findtext(f"{ATOM}published") or node.findtext(f"{ATOM}updated")
            )
            desc = node.findtext(f"{ATOM}summary") or node.findtext(f"{ATOM}content") or ""
        title = html.unescape(title)
        if not title:
            continue
        source = None
        if is_google_news:
            # Google News titles end with " - Source Name"; its description is
            # a related-coverage list, not an article summary — skip it.
            if " - " in title:
                title, source = title.rsplit(" - ", 1)
            desc = ""
        snippet = strip_html(desc)
        items.append({
            "title": title.strip(),
            "link": link,
            "source_override": source,
            "published": published.isoformat() if published else None,
            "snippet": snippet[:SNIPPET_LEN] + ("…" if len(snippet) > SNIPPET_LEN else ""),
        })
    return items


def _link_identity(url: str) -> str:
    """Dedupe identity for a link: the full URL minus fragment, trailing
    path slash, and URL case. Query params are KEPT — distinct articles
    often differ only there, and stripping them once collapsed separate
    stories into one (the survivor's link won, the other was hidden)."""
    u = (url or "").strip()
    if not u:
        return ""
    u = u.split("#", 1)[0]
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*://)([^/?#]*)(.*)$", u)
    if not m:
        return u.rstrip("/")
    scheme, host, rest = m.group(1).lower(), m.group(2).lower(), m.group(3)
    if "?" in rest:
        path, query = rest.split("?", 1)
        rest = path.rstrip("/") + "?" + query
    else:
        rest = rest.rstrip("/")
    return scheme + host + rest


def collect(categories: list[dict]) -> tuple[list[dict], list[str]]:
    """Fetch every feed in parallel; return (candidates, feed_errors)."""
    jobs = []
    for cat in categories:
        for feed in cat.get("feeds", []):
            jobs.append((cat["key"], feed))
    candidates: list[dict] = []
    errors: list[str] = []

    def run(job):
        cat_key, feed = job
        try:
            items = parse_feed(fetch(feed["url"]), int(feed.get("max", 8)),
                               "news.google.com" in feed["url"])
            return (cat_key, feed["name"], items, None)
        except Exception as exc:
            return (cat_key, feed["name"], [], f"{feed['name']}: {str(exc)[:120]}")

    with ThreadPoolExecutor(max_workers=8) as ex:
        for cat_key, feed_name, items, err in ex.map(run, jobs):
            if err:
                errors.append(err)
                print(f"  FAIL {err}")
                continue
            print(f"  OK   {feed_name}: {len(items)} items")
            for it in items:
                candidates.append({**it, "cat_hint": cat_key,
                                   "source": it["source_override"] or feed_name})

    now = datetime.now(timezone.utc)
    seen_links, seen_titles = set(), set()
    fresh = []
    for c in candidates:
        if c["published"]:
            age_h = (now - datetime.fromisoformat(c["published"])).total_seconds() / 3600
            if age_h > FRESH_HOURS:
                continue
            c["age_h"] = round(age_h)
        else:
            c["age_h"] = None
        link_key = _link_identity(c["link"])
        title_key = re.sub(r"\W+", "", c["title"].lower())
        if (link_key and link_key in seen_links) or title_key in seen_titles:
            continue
        seen_links.add(link_key)
        seen_titles.add(title_key)
        fresh.append(c)
    for i, c in enumerate(fresh, 1):
        c["id"] = i
    return fresh, errors


def _age_label(age_h) -> str:
    if age_h is None:
        return "undated"
    if age_h < 24:
        return f"{age_h}h ago"
    return f"{age_h // 24}d ago"


def curate(candidates: list[dict], categories: list[dict], today: str) -> dict:
    """One DeepSeek call -> {key: {summary, items:[{id, takeaway}]}}.
    Selection is by id only; URLs/titles are mapped back from candidates."""
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    cat_lines = "\n".join(f"- {c['key']} — {c['focus']}" for c in categories)
    cand_lines = []
    for c in candidates:
        line = f"[{c['id']}] ({c['source']}, {_age_label(c['age_h'])}) {c['title']}"
        if c["snippet"]:
            line += f" — {c['snippet'][:160]}"
        cand_lines.append(line)
    system = (
        "You are a daily news briefing curator for a reader in Canada. "
        f"TODAY is {today}. Categories (key — focus):\n{cat_lines}\n"
        f"For each category select up to {MAX_PICK} of the most notable RELEVANT "
        "candidates, ranked most important first. Write a 2-4 sentence summary "
        "of what is happening in that area today, and a one-line takeaway for "
        "each selected item.\n"
        "RULES: Use ONLY the provided candidate ids — never invent items, facts, "
        "or URLs. Place each item in at most ONE category (the best fit). If no "
        "candidates fit a category, return an empty items list and a summary "
        "saying nothing notable today. Prefer concrete announcements and fresh "
        "reporting over opinion pieces.\n"
        'Return ONLY strict JSON: {"categories":[{"key":"...","summary":"...",'
        '"items":[{"id":123,"takeaway":"..."}]}]}'
    )
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": "CANDIDATES:\n" + "\n".join(cand_lines)}],
        "temperature": 0,
        "max_tokens": 16384,
    }
    req = urllib.request.Request(
        DEEPSEEK_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    msg = body["choices"][0]["message"]
    content = msg.get("content") or ""
    if not content.strip():
        content = msg.get("reasoning_content") or ""
    content = re.sub(r"```(?:json)?", "", content).strip()
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError("curation response contained no JSON object")
    data = json.loads(content[start:end + 1])

    by_id = {c["id"]: c for c in candidates}
    valid_keys = {c["key"] for c in categories}
    used_ids: set[int] = set()
    out: dict[str, dict] = {}
    for entry in data.get("categories") or []:
        if not isinstance(entry, dict) or entry.get("key") not in valid_keys:
            continue
        items = []
        for it in entry.get("items") or []:
            if not isinstance(it, dict):
                continue
            cid = it.get("id")
            if not isinstance(cid, int) or cid not in by_id or cid in used_ids:
                continue  # hallucinated or duplicate id — drop, never trust
            used_ids.add(cid)
            c = by_id[cid]
            items.append({
                "title": c["title"], "link": c["link"], "source": c["source"],
                "published": c["published"], "age_h": c["age_h"],
                "takeaway": str(it.get("takeaway") or "").strip()[:300],
            })
            if len(items) >= MAX_PICK:
                break
        out[entry["key"]] = {
            "summary": str(entry.get("summary") or "").strip()[:1000],
            "items": items,
        }
    picks = sum(len(v["items"]) for v in out.values())
    if picks == 0:
        # An empty-but-parseable reply used to publish as a "successful"
        # briefing with zero picks. Fail instead — main() falls back to raw
        # headlines with a visible note.
        raise RuntimeError(
            "curation returned no usable selections (empty or all-invalid "
            "categories) — refusing to present this as a curated briefing"
        )
    return out


def build_page(categories: list[dict], digest: dict, generated_at: str,
               mode: str, errors: list[str], curation_note: str = "") -> str:
    sections = []
    for cat in categories:
        d = digest.get(cat["key"], {"summary": "", "items": []})
        sections.append({"key": cat["key"], "name": cat["name"],
                         "summary": d["summary"], "items": d["items"]})
    payload = json.dumps({"generated_at": generated_at, "mode": mode,
                          "sections": sections, "errors": errors,
                          "curation_note": curation_note}, ensure_ascii=False)
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daily Briefing</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #f4f5f7; color: #1c1e21;
         font: 16px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .wrap { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px; margin-bottom: 8px; }
  h1 { font-size: 26px; margin: 0; }
  .stamp { color: #667; font-size: 13px; }
  .stale { display: none; background: #fff3cd; color: #7a5b00; border: 1px solid #e6d9a8;
           border-radius: 6px; padding: 2px 8px; font-size: 12px; }
  .banner { background: #fff3cd; color: #7a5b00; border: 1px solid #e6d9a8;
            border-radius: 8px; padding: 8px 12px; font-size: 13px; margin: 10px 0; }
  .card { background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
          padding: 18px 22px; margin-top: 16px; }
  .card h2 { font-size: 18px; margin: 0 0 8px; }
  .summary { color: #334; margin: 0 0 12px; }
  .empty { color: #889; font-style: italic; }
  ol { margin: 0; padding-left: 22px; }
  li { padding: 7px 0; border-top: 1px solid #f0f1f3; }
  li:first-child { border-top: 0; }
  a { color: #14459c; text-decoration: none; font-weight: 600; }
  a:hover { text-decoration: underline; }
  .meta { color: #889; font-size: 12px; margin-left: 6px; white-space: nowrap; }
  .takeaway { color: #556; font-size: 13px; margin-top: 2px; }
  footer { margin-top: 28px; color: #889; font-size: 13px; }
  code { background: #eceef1; border-radius: 4px; padding: 1px 5px; font-size: 12px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Daily Briefing</h1>
    <span class="stamp" id="stamp"></span>
    <span class="stale" id="stale">STALE — run refresh.cmd</span>
  </header>
  <div id="banner"></div>
  <div id="sections"></div>
  <footer id="footer"></footer>
</div>
<script id="data" type="application/json">__PAYLOAD__</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById("data").textContent);
  var gen = new Date(data.generated_at);
  document.getElementById("stamp").textContent = "updated " + gen.toLocaleString();
  if (Date.now() - gen.getTime() > 26 * 3600 * 1000) {
    document.getElementById("stale").style.display = "inline-block";
  }
  if (data.mode === "raw") {
    var msg = data.curation_note ||
      "AI summaries unavailable (set DEEPSEEK_API_KEY in news/.env) — showing raw headlines.";
    document.getElementById("banner").innerHTML = '<div class="banner">' + esc(msg) + "</div>";
  }
  function ago(iso) {
    if (!iso) return "";
    var mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 60) return mins + "m";
    if (mins < 1440) return Math.round(mins / 60) + "h";
    return Math.round(mins / 1440) + "d";
  }
  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }
  var host = document.getElementById("sections");
  data.sections.forEach(function (s) {
    var card = document.createElement("div");
    card.className = "card";
    var h = "<h2>" + esc(s.name) + "</h2>";
    if (s.summary) h += '<p class="summary">' + esc(s.summary) + "</p>";
    if (!s.items.length) {
      h += '<p class="empty">Nothing notable in this area today.</p>';
    } else {
      h += "<ol>";
      s.items.forEach(function (it) {
        h += "<li>" + (it.link
          ? '<a href="' + esc(it.link) + '" target="_blank" rel="noopener">' + esc(it.title) + "</a>"
          : esc(it.title));
        var w = ago(it.published);
        h += '<span class="meta">' + esc(it.source) + (w ? " · " + w : "") + "</span>";
        if (it.takeaway) h += '<div class="takeaway">' + esc(it.takeaway) + "</div>";
        h += "</li>";
      });
      h += "</ol>";
    }
    card.innerHTML = h;
    host.appendChild(card);
  });
  var f = 'Edit <code>categories.json</code> to change areas and sources, then run <code>refresh.cmd</code>.';
  if (data.errors && data.errors.length) {
    f += "<br>Feeds unreachable this run: " + data.errors.map(esc).join("; ");
  }
  document.getElementById("footer").innerHTML = f;
})();
</script>
</body>
</html>
""".replace("__PAYLOAD__", payload.replace("</", "<\\/"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the daily categorized news briefing.")
    ap.add_argument("--open", action="store_true", help="open the page in the browser when done")
    args = ap.parse_args()

    try:
        categories = json.loads((HERE / "categories.json").read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: cannot read categories.json: {exc}", file=sys.stderr)
        return 1

    print("Fetching feeds ...")
    candidates, errors = collect(categories)
    print(f"{len(candidates)} fresh candidates (<= {FRESH_HOURS}h)")
    if not candidates:
        print("ERROR: no candidates from any feed — page not rebuilt.", file=sys.stderr)
        return 1

    mode = "raw"
    digest: dict = {}
    curation_note = ""
    if os.getenv("DEEPSEEK_API_KEY"):
        print("Curating with DeepSeek ...")
        try:
            digest = curate(candidates, categories, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
            mode = "curated"
            for cat in categories:
                d = digest.get(cat["key"], {"items": []})
                print(f"  {cat['key']}: {len(d['items'])} picks")
        except Exception as exc:
            curation_note = f"AI curation failed this run ({exc}) — showing raw headlines."
            print(f"WARNING: {curation_note}", file=sys.stderr)
    else:
        print("No DEEPSEEK_API_KEY — raw headline mode (set news/.env for AI summaries).")

    if mode == "raw":
        digest = {}
        for cat in categories:
            items = [c for c in candidates if c["cat_hint"] == cat["key"]][:8]
            digest[cat["key"]] = {"summary": "", "items": [
                {"title": c["title"], "link": c["link"], "source": c["source"],
                 "published": c["published"], "age_h": c["age_h"], "takeaway": ""}
                for c in items]}

    generated_at = datetime.now(timezone.utc).isoformat()
    (HERE / "digest.json").write_text(json.dumps(
        {"generated_at": generated_at, "mode": mode, "digest": digest,
         "errors": errors, "curation_note": curation_note},
        ensure_ascii=False, indent=2), encoding="utf-8")
    page = HERE / "index.html"
    page.write_text(build_page(categories, digest, generated_at, mode, errors,
                               curation_note), encoding="utf-8")
    print(f"Done ({mode}): {page}")
    if args.open:
        webbrowser.open(page.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
