# brother-tools

Two small tools, built to be low-maintenance:

| Tool | What it does | Start here |
|---|---|---|
| **[transcriber](transcriber/)** | phone recording → transcript, outstanding tasks, calendar events (.ics), communication insights — per project (Westcliff, 2Nimbus, …) | `transcriber/README.md` |
| **[news](news/)** | daily briefing: Canadian higher-ed policy · world & Canada events · NA tech talent & professional development · dental industry → Canadian dental software | `news/README.md` |

## Quick start

1. **Double-click `setup.cmd`** — it installs Python libraries, creates the
   `.env` key files, and then checks everything. (Python itself must be
   installed first: <https://www.python.org/downloads/>, tick **"Add
   python.exe to PATH"** — `setup.cmd` tells you if it's missing.)
2. Paste your API keys into the `.env` files as instructed
   (`transcriber/.env` needs two, `news/.env` reuses the DeepSeek one).
3. Run anything ffmpeg-related asks for: open PowerShell once and run
   `winget install --id Gyan.FFmpeg -e` (only needed for recordings longer
   than 30 seconds).
4. **Check any time:** double-click `check.cmd` — no network, no cost, every
   gap explained in plain language.

## Background automation (optional, one double-click each)

- `news\install_daily.cmd` — daily 07:30 briefing; missed runs (PC was off)
  fire when the PC wakes; output lands in `news\output\news_log.txt`.
- `transcriber\install_watch.cmd` — the recording watcher starts at logon
  with no window; activity lands in `transcriber\output\watch.log`.

## Something failed?

- A meeting summary failed (no money lost): resume from the saved transcript
  with `python transcriber\transcribe.py --resume output\<folder>` — see the
  transcriber README.
- The meeting date came out wrong: same command plus `--date YYYY-MM-DD`.
- Anything else: `check.cmd` first, then the troubleshooting sections in each
  tool's README.

## Ground rules

- **Keys live in `.env` files only** (`transcriber/.env`, `news/.env`). They
  are git-ignored — never commit them, never paste keys into chats or issues.
- Everything runs on your machine; the only network calls are the news feeds
  you choose and the transcription/summary APIs (cost + privacy notes in the
  transcriber README).
- Generated files (`output/`, `inbox/`, `index.html`, `digest.json`) are
  git-ignored; the tools recreate them on every run.
