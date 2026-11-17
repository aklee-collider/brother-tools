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

**Easiest:** double-click `..\setup.cmd` in the main folder — it installs the
library and creates `.env` for you; `..\check.cmd` verifies everything.

Manual steps, if you prefer:

1. Install Python from <https://www.python.org/downloads/> — tick
   **"Add python.exe to PATH"**.
2. Install the one library: `python -m pip install -r requirements.txt`
   (or just run the main `setup.cmd`, which covers both tools).
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
  - Runs while the PC was off at 07:30 fire as soon as the PC wakes.
  - Every run logs to `output\news_log.txt` — if a morning looks stale,
    that file says what happened.

## How it stays honest

- The AI picks from **real fetched articles only** — every link on the page
  came from a feed, never invented. If an area has nothing notable, it says so.
- If the AI curation itself fails or comes back empty, the page falls back
  to raw headlines **with a visible banner saying so** — never a silently
  empty "briefing".
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
