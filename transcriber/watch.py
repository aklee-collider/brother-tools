#!/usr/bin/env python3
"""Watch inbox/ for new meeting recordings and process them automatically.

Phone workflow: record on the phone -> a sync app (OneDrive, Syncthing, Google
Drive) mirrors the recording into transcriber/inbox/ -> this watcher
transcribes + summarizes it -> results land in output/, the audio is archived
to inbox/processed/ (failures go to inbox/failed/ — re-drop to retry).

Run: double-click watch.cmd and leave the window open. Ctrl+C stops.
The meeting date is taken from the file's modified time (the recording date),
so "next Friday" resolves against when you actually met.

Two safety mechanisms, both OS-level so they survive crashes:

- Single instance: the watcher holds an exclusive byte lock on watch.lock for
  its whole lifetime. A second copy cannot take that byte — the OS decides
  atomically, so even two copies started at the same instant cannot both win.
  When the watcher exits or crashes, the OS releases the byte by itself:
  starting a new watcher after a crash needs no cleanup and no lock-file
  deletion.
- Claim safety: while a recording is being processed it is renamed to
  <name>.processing, and the transcribe.py child's PID is written next to it
  (<name>.processing.pid). If the watcher itself crashes but the child keeps
  running (Windows lets a child outlive its parent), a replacement watcher
  sees the live PID and leaves that recording alone until the child is done.
"""

from __future__ import annotations

import json
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
LOCK_FILE = HERE / "watch.lock"
AUDIO_EXTS = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".flac", ".mp4", ".m4b"}
POLL_SECS = 10
STABLE_POLLS = 2  # size+mtime unchanged across 2 polls (~20s) = upload finished
PROCESS_TIMEOUT = 7200  # long meetings chunk into many 29s API calls

_LOCK_STREAM = None  # held open for the process lifetime = holds the byte lock
_ANNOUNCED_ALIVE: set[str] = set()  # claims already logged as "child still running"


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


# --------------------------------------------------------------- pid beacon

def _pid_file(claimed: Path) -> Path:
    return claimed.with_name(claimed.name + ".pid")


def _write_pid_file(pid_file: Path, pid: int) -> None:
    try:
        pid_file.write_text(str(pid), encoding="utf-8")
    except OSError:
        pass  # worst case the claim looks ownerless — the sweep still retries safely


def _read_pid_file(pid_file: Path) -> int | None:
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _clear_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """Is a process with this PID running right now?"""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(h)


# -------------------------------------------------------------------- lock

def acquire_lock() -> bool:
    """Allow only one watcher — decided by the OS, not by file contents.

    A second watcher is the one thing claim recovery cannot survive: its
    sweep would move a recording the first watcher is actively processing
    into failed/. So while the byte lock on watch.lock is held by a live
    watcher, a new copy refuses to start. The lock is taken atomically by
    the OS: two copies started at the same instant cannot both win, and the
    previous watcher's leftover file (crash, reboot) never blocks anyone —
    its pid/started note is kept only so the refusal can name the holder.
    """
    global _LOCK_STREAM
    try:
        fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT)
    except OSError as exc:
        print(f"Cannot open {LOCK_FILE.name}: {exc}", flush=True)
        return False
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # byte 0: file position is 0 here
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        note = _lock_note()
        holder = f" (PID {note['pid']}, started {note['started']})" if note else ""
        print(
            f"A watcher is already running{holder}.\n"
            "This second copy will NOT start — two watchers would fight over\n"
            "the same recordings and could file one into failed\\ by mistake.\n"
            "If that copy is actually gone (crash, reboot), the lock releases\n"
            "by itself — start watch.cmd again in a moment.",
            flush=True,
        )
        return False
    _LOCK_STREAM = os.fdopen(fd, "w", encoding="utf-8")
    _LOCK_STREAM.write(json.dumps({"pid": os.getpid(),
                                   "started": datetime.now().isoformat(timespec="seconds")}))
    _LOCK_STREAM.flush()
    return True


def _lock_note() -> dict:
    """Best-effort read of the current holder's note (for the refusal message)."""
    try:
        note = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        return note if isinstance(note, dict) else {}
    except (OSError, ValueError):
        return {}


def release_lock() -> None:
    global _LOCK_STREAM
    if _LOCK_STREAM is None:
        return
    f, _LOCK_STREAM = _LOCK_STREAM, None
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)  # the lock was taken at position 0
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        f.close()
    except OSError:
        pass
    try:
        LOCK_FILE.unlink()  # clean exit tidies up; fails harmlessly if a rival
    except OSError:         # is mid-acquire with the file open — the byte lock,
        pass                # not the file, is what decides who runs


# ------------------------------------------------------------------ claims

def _completeness_note(out_dir: Path) -> str:
    """Human-readable completeness note from asr_meta.json (best effort).

    transcribe.py counts billed-but-empty segments in segments_empty. A
    positive count means parts of the recording came back with no text, so
    the transcript may be incomplete — that must reach watch.log, not die in
    the child's captured output. Anything unreadable produces a clear
    diagnostic instead of silence; it never fails the DONE itself.
    """
    try:
        meta = json.loads((out_dir / "asr_meta.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "  (asr_meta.json not found — cannot verify segment completeness)"
    except (OSError, ValueError) as exc:
        return f"  (asr_meta.json unreadable: {exc} — cannot verify segment completeness)"
    if not isinstance(meta, dict):
        return "  (asr_meta.json has an unexpected shape — cannot verify segment completeness)"
    try:
        empty = int(meta.get("segments_empty") or 0)
        total = int(meta.get("segments") or 0)
    except (TypeError, ValueError):
        return "  (asr_meta.json has non-numeric segment counts — cannot verify segment completeness)"
    if empty > 0:
        return (f"  WARNING: {empty} of {total} audio segment(s) transcribed empty —"
                f" transcript may be missing parts; review {out_dir.name}")
    return ""


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
        proc = subprocess.Popen(
            [sys.executable, str(HERE / "transcribe.py"), str(claimed),
             "--date", meeting_day, "--date-assumed", "--out", str(out_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as exc:
        log(f"FAILED {path.name}: cannot launch transcribe.py ({exc})")
        os.rename(claimed, _dest(FAILED, path.name))
        return False
    # Crash beacon: if THIS watcher dies while the child keeps running, the
    # pid file is what stops a replacement watcher from moving the claim.
    pid_file = _pid_file(claimed)
    _write_pid_file(pid_file, proc.pid)
    timed_out = False
    try:
        out, err = proc.communicate(timeout=PROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        proc.communicate()
    _clear_pid_file(pid_file)  # child is done (or killed) — beacon served
    if not timed_out and proc.returncode == 0:
        os.rename(claimed, _dest(PROCESSED, path.name))
        log(f"DONE {path.name} -> {out_dir}{_completeness_note(out_dir)}")
        return True
    if timed_out:
        err = f"timeout after {PROCESS_TIMEOUT}s"
    os.rename(claimed, _dest(FAILED, path.name))
    log(f"FAILED {path.name}: {(err or '').strip()[-300:]}")
    return False


def sweep_claims() -> None:
    """Move finished/detached .processing claims back for a retry.

    Runs at startup AND on every poll, always under the single-instance
    lock. A claim whose transcribe child is STILL RUNNING (its watcher
    crashed, the child outlived it) is left alone — moving the file under a
    live transcription would break it. It is re-checked every poll, so once
    that child exits, the claim lands in failed/ for an easy re-drop.
    """
    for stale in INBOX.glob("*.processing"):
        pid_file = _pid_file(stale)
        owner = _read_pid_file(pid_file)
        if owner is not None and _pid_alive(owner):
            if stale.name not in _ANNOUNCED_ALIVE:
                _ANNOUNCED_ALIVE.add(stale.name)
                log(f"CLAIM {stale.name}: still being transcribed by PID {owner} "
                    "(its watcher crashed) — leaving it until that finishes.")
            continue
        original = stale.name[: -len(".processing")]
        try:
            os.rename(stale, _dest(FAILED, original))
        except OSError as exc:
            log(f"SKIP {stale.name}: cannot move it right now ({exc}) — will retry.")
            continue
        _ANNOUNCED_ALIVE.discard(stale.name)
        _clear_pid_file(pid_file)
        owner_txt = f"PID {owner}" if owner is not None else "no pid marker"
        log(f"RECOVERED claim (owner {owner_txt}) -> failed/: {original}")


# ------------------------------------------------------------------- loop

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
    if not acquire_lock():
        return 1
    try:
        for d in (INBOX, PROCESSED, FAILED):
            d.mkdir(parents=True, exist_ok=True)
        sweep_claims()  # safe only because the lock guarantees we are alone
        log(f"Watching {INBOX} (poll {POLL_SECS}s). Sync your phone recordings here. Ctrl+C stops.")
        tracked: dict[str, tuple] = {}
        while True:
            try:
                sweep_claims()  # an orphaned claim is retried once its child is gone
                scan_once(tracked)
                time.sleep(POLL_SECS)
            except KeyboardInterrupt:
                log("Stopped.")
                return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
