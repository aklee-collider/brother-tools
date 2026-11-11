# Headlines — lightweight RSS reader

One page with the latest headlines from your sources. No account, no server,
no Docker, no pip installs — just Python.

## Setup (Windows)

1. Install Python from <https://www.python.org/downloads/> — tick
   **"Add python.exe to PATH"** on the first installer screen.
2. That's it.

## Use it

Double-click **`refresh.cmd`**. It fetches every feed, rebuilds `index.html`,
and opens it in your browser. Re-run whenever you want fresh headlines.

A feed that fails shows a yellow note on the page — the rest still work.
If the page is more than a day old it shows a **STALE** badge so you never
mistake old news for new.

## Change your sources

Edit **`feeds.json`** — one line per feed:

```json
{"name": "Hacker News", "url": "https://news.ycombinator.com/rss", "max": 10}
```

- `name` — anything you like (shown on the page)
- `url` — the feed's RSS/Atom address (look for the RSS icon on the site,
  or try `<site>/feed/` or `<site>/rss`)
- `max` — how many headlines to show

Both RSS and Atom feeds work. Add as many as you want.

## Optional: auto-refresh every morning

Open PowerShell and run (adjust the path to where you put this folder):

```
schtasks /create /tn "HeadlinesRefresh" /tr "python C:\path\to\rss\fetch_feeds.py" /sc daily /st 07:30
```

Then just open `index.html` whenever — it's always the latest fetch.
