# brother-tools

Two small tools, built to be low-maintenance:

| Tool | What it does | Start here |
|---|---|---|
| **[transcriber](transcriber/)** | meeting recording → transcript, summary, priority list, calendar events (.ics) | `transcriber/README.md` |
| **[rss](rss/)** | your feed list → one clean headline page | `rss/README.md` |

## Quick start

1. Install Python (<https://www.python.org/downloads/>, tick **"Add python.exe
   to PATH"**).
2. **RSS**: double-click `rss/refresh.cmd`. Done.
3. **Transcriber**: follow `transcriber/README.md` (ffmpeg + two API keys +
   `setup.cmd`), then drag a recording onto `transcriber/transcribe.cmd`.

## Ground rules

- **Keys live in `transcriber/.env` only.** That file is git-ignored — never
  commit it, never paste keys into chats or issues.
- Everything runs on your machine; the only network calls are the feeds you
  choose and the two transcription/summary APIs (see the transcriber README
  for cost + privacy notes).
- Generated files (`output/`, `index.html`, `headlines.json`) are git-ignored;
  the tools recreate them on every run.
