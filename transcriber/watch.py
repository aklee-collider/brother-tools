#!/usr/bin/env python3
"""Watch inbox/ for new meeting recordings and process them automatically.

Phone workflow: record on the phone -> a sync app (OneDrive, Syncthing, Google
Drive) mirrors the recording into transcriber/inbox/ -> this watcher
transcribes + summarizes it -> results land in output/, the audio is archived
to inbox/processed/ (failures go to inbox/failed/ — re-drop to retry).

Run: double-click watch.cmd and leave the window open. Ctrl+C stops.
The meeting date is taken from the file's modified time (the recording date),
so "next Friday" resolves against when you actually met.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
INBOX = HERE / "inbox"
PROCESSED = INBOX / "processed"
FAILED = INBOX / "failed"
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac", ".mp4", ".m4b"}
POLL_SECS = 10
STABLE_POLLS = 2  # size+mtime unchanged across 2 polls (~20s) = upload finished
PROCESS_TIMEOUT = 7200  # long meetings chunk into many 29s API calls


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    try:
        (HERE / "output").mkdir(exist_ok=True)
        with open(HERE / "output" / "watch.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _dest(folder: Path, name: str) -> Path:
    dest = folder / name
    if dest.exists():
        p = Path(name)
        dest = folder / f"{p.stem}_{datetime.now():%Y%m%d-%H%M%S}{p.suffix}"
    return dest


def process_one(path: Path) -> bool:
    claimed = path.with_name(path.name + ".processing")
    try:
        os.rename(path, claimed)  # atomic claim: a second watcher won't see it
    except OSError as exc:
        log(f"SKIP {path.name}: still locked by the sync app ({exc})")
        return False
    meeting_day = datetime.fromtimestamp(claimed.stat().st_mtime).date().isoformat()
    out_dir = HERE / "output" / f"{path.stem}_{datetime.now():%Y%m%d-%H%M%S}"
    log(f"PROCESS {path.name} (meeting date {meeting_day})")
    try:
        r = subprocess.run(
            [sys.executable, str(HERE / "transcribe.py"), str(claimed),
             "--date", meeting_day, "--out", str(out_dir)],
            capture_output=True, text=True, timeout=PROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        r = None
    except OSError as exc:
        log(f"FAILED {path.name}: cannot launch transcribe.py ({exc})")
        os.rename(claimed, _dest(FAILED, path.name))
        return False
    if r is not None and r.returncode == 0:
        os.rename(claimed, _dest(PROCESSED, path.name))
        log(f"DONE {path.name} -> {out_dir}")
        return True
    err = r.stderr.strip()[-300:] if r is not None else f"timeout after {PROCESS_TIMEOUT}s"
    os.rename(claimed, _dest(FAILED, path.name))
    log(f"FAILED {path.name}: {err}")
    return False


def recover_stale() -> None:
    for stale in INBOX.glob("*.processing"):
        original = stale.name[: -len(".processing")]
        os.rename(stale, _dest(FAILED, original))
        log(f"RECOVERED stale claim from a crashed run -> failed/: {original}")


def scan_once(tracked: dict) -> None:
    for p in sorted(INBOX.iterdir()):
        try:
            if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
                continue
            st = p.stat()
        except OSError:
            continue  # vanished mid-sync — next poll
        sig = (st.st_size, st.st_mtime_ns)
        prev = tracked.get(p.name)
        count = prev[1] + 1 if prev and prev[0] == sig else 1
        tracked[p.name] = (sig, count)
        if count >= STABLE_POLLS:
            tracked.pop(p.name, None)
            process_one(p)
    for name in list(tracked):
        if not (INBOX / name).exists():
            tracked.pop(name)


def main() -> int:
    for d in (INBOX, PROCESSED, FAILED):
        d.mkdir(parents=True, exist_ok=True)
    recover_stale()
    log(f"Watching {INBOX} (poll {POLL_SECS}s). Sync your phone recordings here. Ctrl+C stops.")
    tracked: dict[str, tuple] = {}
    while True:
        try:
            scan_once(tracked)
            time.sleep(POLL_SECS)
        except KeyboardInterrupt:
            log("Stopped.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
