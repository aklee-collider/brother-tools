# Daily Briefing — categorized news feed

One page, four areas, every day — each area gets a short AI summary and
clickable links to the actual articles:

1. **Canadian Higher Education Policy** — federal/provincial announcements,
   immigration & study permits, private-college discourse, funding
2. **World & Canada Events** — notable developments
3. **Professional Development & Tech Talent (North America)**
4. **Dental Industry → Canadian Dental Software** — advancements that impact
   dental specialist software in Canada

## Setup (Windows)

1. Install Python from <https://www.python.org/downloads/> — tick
   **"Add python.exe to PATH"**.
2. Install the one library: open a terminal in this folder and run
   `python -m pip install python-dotenv`
   (or double-click `..\transcriber\setup.cmd` if you already set up the
   transcriber — same library).
3. **AI summaries (recommended):** copy `.env.example` to `.env` and paste
   your DeepSeek key — the same one the transcriber uses
   (<https://platform.deepseek.com>). Without a key the page still builds,
   but shows raw headlines with a visible note instead of summaries.

## Use it

- **Double-click `refresh.cmd`** — fetches ~18 feeds, curates, opens the page.
- **Daily automation:** double-click `install_daily.cmd` once. It registers a
  Windows task that rebuilds the briefing every day at **07:30** (no popup —
  just open `index.html` whenever). Remove with:
  `schtasks /delete /tn "BrotherNewsDaily" /f`

## How it stays honest

- The AI picks from **real fetched articles only** — every link on the page
  came from a feed, never invented. If an area has nothing notable, it says so.
- A feed that fails is listed at the bottom of the page; the rest still work.
- A page older than ~a day shows a **STALE** badge.
- Only articles from the last 72 hours are considered (survives weekends).

## Change areas or sources

Edit **`categories.json`**: each area has a `name`, a `focus` sentence (this
is what the AI uses to judge relevance — make it specific), and a `feeds`
list (`name`, `url`, `max`). Any RSS/Atom feed works; Google News search
feeds are great for niche topics:

```
https://news.google.com/rss/search?q=YOUR+TOPIC&hl=en-CA&gl=CA&ceid=CA:en
```

## Cost

One DeepSeek call per refresh ≈ a few cents. Feeds are free. At daily
cadence that's roughly $1–2/month.
