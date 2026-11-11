# Meeting Transcriber

Drop in a meeting recording → get a transcript, a summary, a priority task
list, and calendar events you can import into Google / Apple / Outlook.

## One-time setup (Windows)

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
3. **Libraries** — double-click `setup.cmd`.
4. **API keys** — this tool uses two paid APIs (both cheap, pay-as-you-go):
   - **Zhipu BigModel** (transcription): sign up at <https://open.bigmodel.cn>,
     create an API key, top up a small amount (¥10 covers ~2.5 hours of audio).
   - **DeepSeek** (summary + priorities): sign up at
     <https://platform.deepseek.com>, create an API key, top up a few dollars.
   - Copy `.env.example` to `.env` (same folder) and paste both keys in.
     **Never share or commit `.env` — it's your wallet.**

## Use it

- **Drag** an audio file (`.m4a`, `.mp3`, `.wav`, `.ogg`, phone voice notes,
  Zoom/Teams recordings) **onto `transcribe.cmd`**.
- Or from a terminal:
  ```
  python transcribe.py meeting.m4a
  python transcribe.py meeting.m4a --date 2026-11-10   # meeting happened earlier
  python transcribe.py meeting.m4a --no-summary        # transcript only
  ```

Results land in `output/<name>_<timestamp>/`:

| File | What it is |
|---|---|
| `transcript.txt` | everything that was said |
| `summary.md` | 3–5 sentence overview + priorities + events |
| `tasks.md` | checkbox list, grouped high / medium / low |
| `events.ics` | dated events — double-click to import into your calendar |

`--date` matters: "next Friday" in the recording is resolved against the
meeting date, not the day you run the tool.

## Cost & privacy

- Transcription ≈ **¥0.06/minute** (~1¢ CAD/min); a 1-hour meeting ≈ ¥3.6.
  Every run appends its estimate to `output/cost_YYYY-MM-DD.log`.
- The summary step costs a few cents per meeting on DeepSeek.
- **Audio is uploaded to Zhipu's servers (China); the transcript goes to
  DeepSeek.** Don't run confidential recordings through it unless that's
  acceptable to you.

## Troubleshooting

- `ZHIPU_API_KEY not set` → you skipped step 4, or named the file `.env.txt`
  (Windows hides extensions — check).
- `ASR HTTP 401` → key wrong.
- `ASR HTTP 429` (code 1113, "余额不足") → wallet empty — top up at
  open.bigmodel.cn, then re-run.
- Long file fails with a 30-second error → ffmpeg isn't installed (step 2).
- Transcript saved but summary failed → your transcript is safe in `output/`;
  fix the DeepSeek key and re-run.
