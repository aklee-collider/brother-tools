#!/usr/bin/env python3
"""Run the daily briefing with a persistent log — used by the scheduled task.

fetch_news.py prints to the console, which is invisible when Windows runs
it unattended at 07:30. This wrapper captures everything into
news/output/news_log.txt (one .old generation, ~512 KB cap) and records a
crash with its full traceback, so a silent failure becomes a readable one.

    python run_daily.py               # run the briefing, log to output/news_log.txt
    python run_daily.py --self-test   # prove the log mechanics, no network calls
"""

from __future__ import annotations

import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_DIR = HERE / "output"
LOG_FILE = LOG_DIR / "news_log.txt"
LOG_MAX_BYTES = 512 * 1024


class _Tee:
    """Write to several streams at once; tolerate closed/None console streams."""

    def __init__(self, *streams) -> None:
        self.streams = [s for s in streams if s is not None]

    def write(self, s: str) -> int:
        for st in self.streams:
            try:
                st.write(s)
            except OSError:
                pass
        return len(s)

    def flush(self) -> None:
        for st in self.streams:
            try:
                st.flush()
            except OSError:
                pass


def _rotate_if_big() -> None:
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX_BYTES:
            old = LOG_FILE.with_suffix(".old.txt")
            if old.exists():
                old.unlink()
            LOG_FILE.rename(old)
    except OSError:
        pass  # logging must never take the run down


def _log(line: str, f) -> None:
    f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")


def main(argv: list[str]) -> int:
    self_test = "--self-test" in argv
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _rotate_if_big()
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        sys.stdout = sys.stderr = _Tee(sys.__stdout__, sys.__stderr__, f)
        _log(f"--- run start ({'self-test' if self_test else 'daily briefing'}) ---", f)
        if self_test:
            print("self-test: log line visible, rotation path checked")
            _log("self-test OK", f)
            sys.stdout = sys.__stdout__
            sys.stderr = sys.__stderr__
            print(f"self-test OK — see {LOG_FILE}")
            return 0
        try:
            import fetch_news
            rc = fetch_news.main([])
        except SystemExit as exc:  # argparse and friends raise SystemExit
            rc = int(exc.code or 0)
        except BaseException:
            _log("run FAILED with an exception — traceback follows", f)
            traceback.print_exc()
            rc = 1
        _log(f"--- run end, exit {rc} ---", f)
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
