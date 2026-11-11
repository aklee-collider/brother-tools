#!/usr/bin/env python3
"""RSS headline reader: fetch feeds -> headlines.json + a self-contained page.

Stdlib only — no pip installs, no server, no Docker. Edit feeds.json to change
the source list, then double-click refresh.cmd (or run: python fetch_feeds.py).

A feed that fails shows its error on the page; it never takes the page down.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; BrotherToolsRSS/1.0)"
ATOM = "{http://www.w3.org/2005/Atom}"
SNIPPET_LEN = 220


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
        dt = parsedate_to_datetime(raw)  # RFC 822 (RSS pubDate)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))  # Atom
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read()


def parse_feed(xml_bytes: bytes, max_items: int) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    items: list[dict] = []
    if root.tag == "rss" or root.tag.endswith("rss"):
        nodes = root.findall("./channel/item")
    elif root.tag == f"{ATOM}feed":
        nodes = root.findall(f"{ATOM}entry")
    else:
        # some feeds serve rdf:RDF or odd wrappers — try both shapes
        nodes = root.findall("./channel/item") or root.findall(f".//{ATOM}entry")
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
        if not title:
            continue
        snippet = strip_html(desc)
        items.append({
            "title": html.unescape(title),
            "link": link,
            "published": published.isoformat() if published else None,
            "snippet": snippet[:SNIPPET_LEN] + ("…" if len(snippet) > SNIPPET_LEN else ""),
        })
    return items


def build_page(feeds: list[dict], generated_at: str) -> str:
    payload = json.dumps({"generated_at": generated_at, "feeds": feeds},
                         ensure_ascii=False)
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Headlines</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #f4f5f7; color: #1c1e21;
         font: 16px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  .wrap { max-width: 1000px; margin: 0 auto; padding: 24px 16px 48px; }
  header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px; margin-bottom: 20px; }
  h1 { font-size: 26px; margin: 0; }
  .stamp { color: #667; font-size: 13px; }
  .stale { display: none; background: #fff3cd; color: #7a5b00; border: 1px solid #e6d9a8;
           border-radius: 6px; padding: 2px 8px; font-size: 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
  .card { background: #fff; border: 1px solid #e3e5e8; border-radius: 10px; padding: 16px 18px; }
  .card h2 { font-size: 17px; margin: 0 0 4px; }
  .count { color: #889; font-size: 12px; margin-bottom: 10px; }
  .err { background: #fff3cd; color: #7a5b00; border-radius: 6px; padding: 8px 10px;
         font-size: 13px; margin: 8px 0; }
  ul { list-style: none; margin: 0; padding: 0; }
  li { padding: 8px 0; border-top: 1px solid #f0f1f3; }
  li:first-child { border-top: 0; }
  a { color: #14459c; text-decoration: none; font-weight: 600; }
  a:hover { text-decoration: underline; }
  .when { color: #889; font-size: 12px; margin-left: 6px; white-space: nowrap; }
  .snip { color: #556; font-size: 13px; margin-top: 2px; }
  footer { margin-top: 28px; color: #889; font-size: 13px; }
  code { background: #eceef1; border-radius: 4px; padding: 1px 5px; font-size: 12px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Headlines</h1>
    <span class="stamp" id="stamp"></span>
    <span class="stale" id="stale">STALE — run refresh.cmd</span>
  </header>
  <div class="grid" id="grid"></div>
  <footer>Edit <code>feeds.json</code> to change sources, then double-click <code>refresh.cmd</code>.</footer>
</div>
<script id="data" type="application/json">__PAYLOAD__</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById("data").textContent);
  var gen = new Date(data.generated_at);
  document.getElementById("stamp").textContent = "updated " + gen.toLocaleString();
  if (Date.now() - gen.getTime() > 24 * 3600 * 1000) {
    document.getElementById("stale").style.display = "inline-block";
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
  var grid = document.getElementById("grid");
  data.feeds.forEach(function (f) {
    var card = document.createElement("div");
    card.className = "card";
    var h = "<h2>" + esc(f.name) + "</h2>";
    if (f.error) {
      h += '<div class="err">Could not read this feed: ' + esc(f.error) + "</div>";
    } else {
      h += '<div class="count">' + f.items.length + " headlines</div><ul>";
      f.items.forEach(function (it) {
        var t = esc(it.title);
        h += "<li>" + (it.link
          ? '<a href="' + esc(it.link) + '" target="_blank" rel="noopener">' + t + "</a>"
          : t);
        var w = ago(it.published);
        if (w) h += '<span class="when">' + w + "</span>";
        if (it.snippet) h += '<div class="snip">' + esc(it.snippet) + "</div>";
        h += "</li>";
      });
      h += "</ul>";
    }
    card.innerHTML = h;
    grid.appendChild(card);
  });
})();
</script>
</body>
</html>
""".replace("__PAYLOAD__", payload.replace("</", "<\\/"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch RSS feeds and build the headline page.")
    ap.add_argument("--open", action="store_true", help="open the page in the browser when done")
    args = ap.parse_args()

    feeds_path = HERE / "feeds.json"
    try:
        sources = json.loads(feeds_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: cannot read feeds.json: {exc}", file=sys.stderr)
        return 1

    results = []
    for src in sources:
        name = src.get("name") or src.get("url", "?")
        max_items = int(src.get("max", 10))
        entry = {"name": name, "url": src.get("url", ""), "items": [], "error": None}
        try:
            entry["items"] = parse_feed(fetch(src["url"]), max_items)
            print(f"  OK   {name}: {len(entry['items'])} items")
        except Exception as exc:
            entry["error"] = str(exc)[:200]
            print(f"  FAIL {name}: {entry['error']}")
        results.append(entry)

    generated_at = datetime.now(timezone.utc).isoformat()
    (HERE / "headlines.json").write_text(
        json.dumps({"generated_at": generated_at, "feeds": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    page = HERE / "index.html"
    page.write_text(build_page(results, generated_at), encoding="utf-8")
    ok = sum(1 for r in results if not r["error"])
    print(f"Done: {ok}/{len(results)} feeds -> {page}")
    if args.open:
        webbrowser.open(page.as_uri())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
