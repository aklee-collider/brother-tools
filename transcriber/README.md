# Meeting Transcriber

Record a meeting on your phone → it lands in the harness → you get a
transcript, **outstanding tasks**, calendar events, and **communication
insights** (what objections came up, and how to handle the next conversation
better — especially when a prospect moved on).

Works for all your career projects: **Westcliff** and **2Nimbus** are set up
already; add more in `projects.json` anytime.

## One-time setup (Windows)

**Easiest:** double-click `..\setup.cmd` in the main folder — it installs the
libraries, creates `.env` for you, and finishes with a full check of
everything below. `..\check.cmd` re-runs the check any time (no network, no
cost).

Manual steps, if you prefer:

1. **Python** — install from <https://www.python.org/downloads/>.
   ⚠ On the first installer screen, tick **"Add python.exe to PATH"**.
2. **ffmpeg** (splits long recordings into API-sized pieces) — open PowerShell
   and run:
   ```
   winget install --id Gyan.FFmpeg -e
   ```
   No winget? Download a release from <https://www.gyan.dev/ffmpeg/builds/>,
   and put `ffmpeg.exe` + `ffprobe.exe` into a new `ffmpeg` folder inside this
   `transcriber` folder. Either way works.
3. **Libraries** — double-click `setup.cmd` (in the main folder), or:
   `python -m pip install -r requirements.txt`.
4. **API keys** — two paid APIs (both cheap, pay-as-you-go):
   - **Zhipu BigModel** (transcription): sign up at <https://open.bigmodel.cn>,
     create an API key, top up a small amount (¥10 covers ~2.5 hours of audio).
   - **DeepSeek** (summary + tasks + insights): sign up at
     <https://platform.deepseek.com>, create an API key, top up a few dollars.
   - Copy `.env.example` to `.env` (same folder) and paste both keys in.
     **Never share or commit `.env` — it's your wallet.**

## The seamless phone workflow

1. On your phone, record the meeting with any recorder app (Voice Memos,
   Easy Voice Recorder, etc.).
2. Get the recording into this folder's **`inbox\`** automatically — pick one:
   - **OneDrive** (simplest if you already use it): save/share the recording
     into your OneDrive, and on the PC move the OneDrive folder location — or
     just make `inbox` a folder inside OneDrive that syncs down.
   - **Syncthing** (most robust, free, no cloud): install Syncthing on the
     phone and PC, pair them, and sync your phone's recordings folder to
     `transcriber\inbox`.
   - **Google Drive / Dropbox**: same idea — sync a recordings folder down to
     `inbox`.
3. On the PC, double-click **`watch.cmd`** and leave the window open. Every
   new recording that finishes syncing is transcribed and summarized
   automatically — results appear in `output\`, the audio is archived to
   `inbox\processed\`.
   - **No window wanted:** double-click `install_watch.cmd` once — the
     watcher then starts automatically at logon, no window, activity logged
     to `output\watch.log`. Remove with
     `schtasks /delete /tn "BrotherTranscribeWatch" /f`.
   - Only one watcher can run at a time — starting a second copy (window or
     scheduled) exits with a clear message instead of fighting over files.

The meeting date is read from the file's modified time (the recording date),
so "next Friday" resolves against when you actually met, not when the file
synced. Each summary states the date it used and how to correct it.

**Manual still works:** drag any audio file onto `transcribe.cmd`, or:

```
python transcribe.py meeting.m4a
python transcribe.py meeting.m4a --project westcliff --date 2026-11-10
python transcribe.py meeting.m4a --no-summary        # transcript only
```

## What you get (in `output\<name>_<timestamp>\`)

| File | What it is |
|---|---|
| `transcript.txt` | everything that was said |
| `asr_meta.json` | transcription facts (date, length, cost) — used by `--resume` |
| `summary.md` | overview, outstanding tasks, events, **communication insights** |
| `tasks.md` | checkbox list of outstanding tasks, high / medium / low |
| `events.ics` | dated events — double-click to import into your calendar |

The **communication insights** section tells you whether the meeting's goal
was achieved, lists the objections that were raised, and gives concrete
recommendations for the next conversation — phrasing, evidence to bring,
questions to ask, follow-up timing — tailored to the project.

## Projects

`projects.json` holds your active projects (key, name, context). The AI reads
the context to tag each meeting and tailor its advice. Add a project by
copying an entry and editing it; force a tag with `--project <key>`.

## Fixing a meeting without paying again

The expensive part is transcription; the summary step is a few cents. When a
meeting went wrong in a fixable way, resume from the saved transcript — the
audio is **never** re-sent:

```
python transcribe.py --resume output/meeting_20261117-120000              # summary failed earlier
python transcribe.py --resume output/meeting_20261117-120000 --date 2026-11-15   # date was wrong
```

`summary.md`, `tasks.md`, and `events.ics` are rewritten in place; the
corrected date also lands in the calendar file.

## Cost & privacy

- Transcription ≈ **¥0.06/minute** (~1¢ CAD/min); a 1-hour meeting ≈ ¥3.6.
  Long meetings take a while (the API processes 29-second chunks in order) —
  the watcher runs in the background, so just let it work.
- The summary/insights step costs a few cents per meeting on DeepSeek.
- Every run appends its estimate to `output/cost_YYYY-MM-DD.log`.
- **Audio is uploaded to Zhipu's servers (China); the transcript goes to
  DeepSeek.** Don't run confidential recordings through it unless that's
  acceptable to you. Recordings in `inbox/` stay on your machine and are
  git-ignored.

## Troubleshooting

- `ZHIPU_API_KEY not set` → you skipped step 4, or named the file `.env.txt`
  (Windows hides extensions — check).
- `ASR HTTP 401` → key wrong.
- `ASR HTTP 429` (code 1113, "余额不足") → wallet empty — top up at
  open.bigmodel.cn, then re-run.
- Long file fails with a 30-second error → ffmpeg isn't installed (step 2).
- `incomplete conversion` / `ffmpeg failed` → the recording did not convert
  fully (corrupt file, or the PC slept mid-copy). The tool refuses rather
  than give you a silently truncated transcript — check the file, re-drop it.
- `WARNING: N of M audio segments returned no text` → part of the meeting
  may be missing from the transcript (usually a bad patch of audio). The
  result is still saved; listen to the original around the gap if in doubt.
- Transcript saved but summary failed → your transcript is safe in
  `output\`; fix the DeepSeek key and resume with
  `python transcribe.py --resume output\<folder>` (no transcription cost).
- `A watcher is already running` → exactly that. One watcher does all the
  work; if that copy is actually gone (crash, reboot), the lock releases by
  itself — just start `watch.cmd` again. No file needs deleting.
- A recording is stuck in `inbox\` as `<name>.processing` → a transcription
  from an earlier crashed watcher may still be running for it; the watcher
  checks every poll and moves it to `inbox\failed\` for a re-drop once that
  transcription is really finished. Nothing for you to do by hand.
- Watcher put a file in `inbox\failed\` → the log (`output\watch.log`) says
  why; fix it and drop the file back into `inbox\` to retry.
