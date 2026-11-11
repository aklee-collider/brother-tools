# brother-tools

Two small tools, built to be low-maintenance:

| Tool | What it does | Start here |
|---|---|---|
| **[transcriber](transcriber/)** | phone recording → transcript, outstanding tasks, calendar events (.ics), communication insights — per project (Westcliff, 2Nimbus, …) | `transcriber/README.md` |
| **[news](news/)** | daily briefing: Canadian higher-ed policy · world & Canada events · NA tech talent & professional development · dental industry → Canadian dental software | `news/README.md` |

## Quick start

1. Install Python (<https://www.python.org/downloads/>, tick **"Add python.exe
   to PATH"**).
2. **News**: put your DeepSeek key in `news/.env` (optional but recommended),
   double-click `news/refresh.cmd`. For a daily 07:30 briefing, run
   `news/install_daily.cmd` once.
3. **Transcriber**: follow `transcriber/README.md` (ffmpeg + two API keys +
   `setup.cmd`). Drag a recording onto `transcriber/transcribe.cmd`, or set
   up the phone → `inbox` sync and leave `watch.cmd` running.

## Ground rules

- **Keys live in `.env` files only** (`transcriber/.env`, `news/.env`). They
  are git-ignored — never commit them, never paste keys into chats or issues.
- Everything runs on your machine; the only network calls are the news feeds
  you choose and the transcription/summary APIs (cost + privacy notes in the
  transcriber README).
- Generated files (`output/`, `inbox/`, `index.html`, `digest.json`) are
  git-ignored; the tools recreate them on every run.
