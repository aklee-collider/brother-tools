#!/usr/bin/env python3
"""Process-level regression tests for the round-2 (Codex r2) watch.py fixes.

Each test drives the REAL watch.py in a throwaway fixture directory, with a
stub transcribe.py standing in for the paid API — the watcher's own
orchestration (single-instance lock, claim safety, status surfacing) is the
unit under test, exercised through the same process mechanics Windows really
uses (a child can outlive its parent; the OS releases byte locks on death).

Every claim here is stated as the defect it would have let escape:

- TestOnlyOneWatcherWins ......... r2 finding 2: two simultaneous starters
  both took over a stale lock and both ran recovery.
- TestReplacementAfterCrash ...... the crash-release contract that replaces
  r1's stale-lock takeover (existing-contract control: kept true).
- TestCrashDuringTranscription ... r2 condition 2: lock alone cannot prove a
  .processing recording safe to move — the child can outlive the watcher.
- TestWarningDurability .......... r2 finding 3 / condition 3: empty-segment
  warnings and metadata diagnostics must reach watch.log.
- TestOwnershipHandoff ........... r3 finding 1: ownership is published by
  the child BEFORE it uses the recording — pre-publication claims are
  recoverable even with a live child process; a child that cannot publish
  refuses to start.

Set BROTHER_WATCH_SOURCE=main to build fixtures from the pre-fix baseline
(red-on-baseline runs); the default is the working tree. No network, no API
cost. Run:  python tests\\test_watch_r2.py   (~5 min: real poll cycles)
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = os.environ.get("BROTHER_WATCH_SOURCE", "WORKING")
POLL_SECS = 10      # watch.py's production poll period
STABLE_POLLS = 2    # production stability window: ~20 s before a file is claimed

STUB_TRANSCRIBE = r'''\
import json, os, re, sys, time
from pathlib import Path


def spec_for(audio: Path) -> dict:
    """Per-file behavior spec: <original name>.stubspec next to the claim.

    The watcher passes a UNIQUE claim path (original + attempt tag +
    .processing, r5) — strip both suffixes to find the spec the test
    dropped next to the original recording.
    """
    name = audio.name
    if name.endswith(".processing"):
        name = name[: -len(".processing")]
    name = re.sub(r"\.\d{8}-\d{6}-\d{6}$", "", name)
    spec = {"META": "ok", "EMPTY": "0", "SEGMENTS": "5", "SLEEP": "0",
            "PUBLISH_DELAY": "0", "PUBLISH_FAIL": "0"}
    p = audio.parent / (name + ".stubspec")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                spec[k.strip().upper()] = v.strip()
    return spec


def main() -> int:
    argv = sys.argv[1:]
    audio = Path(argv[0])
    out = Path(argv[argv.index("--out") + 1])
    spec = spec_for(audio)
    # Mirror transcribe.py's EXACT order — interpreter startup -> marker
    # lock -> exists-check -> paid call. A delayed child whose claim was
    # recovered while it slept must therefore refuse BEFORE logging a call.
    time.sleep(float(spec["PUBLISH_DELAY"]))
    if audio.name.endswith(".processing"):
        marker = audio.with_name(audio.name + ".pid")
        if spec["PUBLISH_FAIL"] == "1":
            print("ERROR: cannot open the processing marker", flush=True)
            return 3
        mfd = os.open(marker, os.O_RDWR | os.O_CREAT)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(mfd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.lockf(mfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.ftruncate(mfd, 0)
        os.write(mfd, str(os.getpid()).encode())
    if not audio.exists():
        print(f"ERROR: file not found: {audio}", flush=True)
        return 1
    out.mkdir(parents=True, exist_ok=True)
    # The marker is byte-locked (unreadable from outside by design) —
    # publish our pid in the out dir so the test can drain us later.
    if audio.name.endswith(".processing"):
        (out / "stub.pid").write_text(str(os.getpid()), encoding="utf-8")
    call_log = os.environ.get("WATCH_CALL_LOG")
    if call_log:
        with open(call_log, "a", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\t{audio}\n")
    time.sleep(float(spec["SLEEP"]))
    (out / "transcript.txt").write_text("stub transcript for " + audio.name,
                                        encoding="utf-8")
    mode = spec["META"]
    if mode == "ok":
        (out / "asr_meta.json").write_text(json.dumps({
            "title": audio.stem, "meeting_day": "2026-11-18",
            "segments": int(spec["SEGMENTS"]),
            "segments_empty": int(spec["EMPTY"])}), encoding="utf-8")
    elif mode == "malformed":
        (out / "asr_meta.json").write_text("{not json", encoding="utf-8")
    # mode "missing": deliberately write no asr_meta.json at all
    print("stub transcribe finished", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


class TestStubIsCompilable(unittest.TestCase):
    """The stub is a program-inside-a-string: an escape slip in the outer
    literal produces a generated stub that dies at import — every processing
    test then fails opaquely. Keep the artifact compilable by construction."""

    def test_stub_source_compiles(self):
        compile(STUB_TRANSCRIBE, "stub_transcribe.py", "exec")


def watch_py_source() -> str:
    if SOURCE == "WORKING":
        return (REPO / "transcriber" / "watch.py").read_text(encoding="utf-8")
    blob = subprocess.run(["git", "-C", str(REPO), "show",
                           f"{SOURCE}:transcriber/watch.py"],
                          capture_output=True, text=True, check=True)
    return blob.stdout


def build_fixture(tmp: Path) -> Path:
    fx = tmp / "fx"
    (fx / "inbox").mkdir(parents=True)
    (fx / "watch.py").write_text(watch_py_source(), encoding="utf-8")
    (fx / "transcribe.py").write_text(STUB_TRANSCRIBE, encoding="utf-8")
    return fx


def read_log(fx: Path) -> str:
    f = fx / "output" / "watch.log"
    return f.read_text(encoding="utf-8") if f.exists() else ""


def wait_until(pred, timeout: float, desc: str, poll: float = 0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(poll)
    raise AssertionError(f"timed out after {timeout}s waiting for: {desc}")


def done_lines(log: str, name: str):
    return [ln for ln in log.splitlines() if f"DONE {name}" in ln]


class WatcherTestCase(unittest.TestCase):
    """Shared fixture + process-drain discipline (never leak live watchers)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.fx = build_fixture(Path(self.tmp.name))
        self.procs: list[subprocess.Popen] = []
        self.child_pids: list[int] = []  # orphan-prone stubs recorded for draining

    def tearDown(self):
        for pid in self.child_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        for p in self.procs:
            if p.poll() is None:
                p.kill()
        for p in self.procs:
            try:
                p.communicate(timeout=10)
            except Exception:
                pass
        self.tmp.cleanup()

    def run_watcher(self, env_extra: dict | None = None) -> subprocess.Popen:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        if env_extra:
            env.update(env_extra)
        p = subprocess.Popen([sys.executable, "watch.py"], cwd=str(self.fx),
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace", env=env)
        self.procs.append(p)
        return p

    @staticmethod
    def stop(p: subprocess.Popen) -> str:
        if p.poll() is None:
            p.kill()
        out, _ = p.communicate(timeout=10)
        return out or ""


# ------------------------------------------------- r2 finding 2 (lock race)

class TestOnlyOneWatcherWins(WatcherTestCase):
    def test_only_one_watcher_acquires_and_reaches_recovery(self):
        # The exact r2 scenario: a CRASHED watcher left both a stale lock file
        # and a stale claim behind; two watchers start at the same instant.
        # Exactly one may acquire the lock; only THAT one may run recovery.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (self.fx / "watch.lock").write_text(
            json.dumps({"pid": dead.pid, "started": "2026-11-18T00:00:00"}),
            encoding="utf-8")
        (self.fx / "inbox" / "old.m4a.processing").write_text("x", encoding="utf-8")
        a = self.run_watcher()
        b = self.run_watcher()
        wait_until(lambda: "Watching" in read_log(self.fx)
                   or (a.poll() is not None and b.poll() is not None),
                   30, "one watcher to acquire and print its banner")
        time.sleep(2)  # let the loser exit and flush
        out_a = self.stop(a)
        out_b = self.stop(b)
        winners = [o for o in (out_a, out_b) if "Watching" in o]
        losers = [o for o in (out_a, out_b) if "already running" in o]
        self.assertEqual(len(winners), 1,
                         f"exactly one watcher may start; got {len(winners)}")
        self.assertEqual(len(losers), 1, "the other must refuse in plain language")
        recovered = out_a.count("RECOVERED") + out_b.count("RECOVERED")
        self.assertEqual(recovered, 1,
                         "only the lock holder may sweep the stale claim")
        failed = list((self.fx / "inbox" / "failed").glob("old.m4a*"))
        self.assertEqual(len(failed), 1, "the claim moves exactly once")
        self.assertEqual(list((self.fx / "inbox").glob("*.processing")), [])


class TestReplacementAfterCrash(WatcherTestCase):
    def test_replacement_acquires_after_holder_killed(self):
        # Existing-contract control (true before r2 and must stay true): a
        # crashed watcher's lock never blocks the next watcher. Under r1 this
        # needed stale-PID takeover; now the OS releases the byte itself.
        a = self.run_watcher()
        wait_until(lambda: "Watching" in read_log(self.fx), 15, "first watcher up")
        a.kill()
        a.communicate(timeout=10)
        b = self.run_watcher()
        wait_until(lambda: read_log(self.fx).count("Watching") >= 2, 15,
                   "replacement acquires after the holder's death")
        out_b = self.stop(b)
        self.assertNotIn("already running", out_b,
                         "the replacement must not be mistaken for a duplicate")


# --------------------------------- r2 condition 2 (child outlives watcher)

class TestCrashDuringTranscription(WatcherTestCase):
    def test_claim_survives_replacement_while_child_alive(self):
        inbox = self.fx / "inbox"
        audio = inbox / "meeting.m4a"
        audio.write_bytes(b"0" * 100)
        (inbox / "meeting.m4a.stubspec").write_text("SLEEP=300\n",
                                                    encoding="utf-8")
        a = self.run_watcher()
        wait_until(lambda: list(inbox.glob("*.processing")),
                   STABLE_POLLS * POLL_SECS + 30,
                   "watcher to claim the recording")
        claim = list(inbox.glob("*.processing"))[0]  # unique-tagged claim path
        pid_file = Path(str(claim) + ".pid")
        wait_until(lambda: pid_file.exists(),
                   10, "child to publish its ownership beacon")

        def _stub_pid_file():
            files = list(self.fx.glob("output/*/stub.pid"))
            return files[0] if files else None

        wait_until(lambda: _stub_pid_file() is not None,
                   10, "stub pid sidecar to appear")
        child_pid = int(_stub_pid_file().read_text(encoding="utf-8"))
        self.child_pids.append(child_pid)  # drained in tearDown even on failure
        a.kill()  # watcher crashes MID-TRANSCRIPTION; the stub child outlives it
        a.communicate(timeout=10)
        c = self.run_watcher()  # replacement watcher
        wait_until(lambda: "still being transcribed" in read_log(self.fx), 30,
                   "replacement to name the live child instead of moving the claim")
        time.sleep(2 * POLL_SECS + 5)  # several more polls: still protected
        self.assertTrue(claim.exists(),
                        "a live transcription's claim must not be moved")
        self.assertTrue(pid_file.exists(), "the beacon must survive as long as the child")
        self.assertEqual(list((self.fx / "inbox" / "failed").iterdir()), [])
        # drain the orphan child — now the replacement may recover the claim
        os.kill(child_pid, signal.SIGTERM)
        wait_until(lambda: (self.fx / "inbox" / "failed" / "meeting.m4a").exists(),
                   4 * POLL_SECS + 20, "claim recovered once the child is really gone")
        self.assertFalse(claim.exists())
        self.assertFalse(pid_file.exists(), "the beacon goes with the claim")
        self.assertIn("RECOVERED", read_log(self.fx))
        self.stop(c)


# ----------------------- r3 finding 1 (ownership published before first use)

class TestOwnershipHandoff(WatcherTestCase):
    """Review r3 finding 1: the child publishes its PID BEFORE touching the
    recording. A claim with no marker therefore has no owner yet — recovery
    may move it even though a child process exists (it has not opened the
    audio). RED on d64648c: the watcher wrote the beacon itself right after
    spawn, so the same scenario froze the claim under a pre-publication
    child that could then lose its recording mid-flight."""

    def test_claim_recoverable_before_child_publishes(self):
        inbox = self.fx / "inbox"
        audio = inbox / "meeting.m4a"
        audio.write_bytes(b"0" * 100)
        (inbox / "meeting.m4a.stubspec").write_text(
            "PUBLISH_DELAY=60\n", encoding="utf-8")
        a = self.run_watcher()
        wait_until(lambda: list(inbox.glob("*.processing")),
                   STABLE_POLLS * POLL_SECS + 30,
                   "watcher to claim the recording")
        claim = list(inbox.glob("*.processing"))[0]  # unique-tagged claim path
        pid_file = Path(str(claim) + ".pid")
        time.sleep(2)  # the child is spawned right after the claim
        self.assertFalse(pid_file.exists(),
                         "pre-publication: the child has not claimed ownership yet")
        time.sleep(2)  # the child is spawned right after the claim
        self.assertFalse(pid_file.exists(),
                         "pre-publication: the child has not claimed ownership yet")
        a.kill()  # watcher dies in the launch window; the child is still starting
        a.communicate(timeout=10)
        c = self.run_watcher()
        wait_until(lambda: (self.fx / "inbox" / "failed" / "meeting.m4a").exists(),
                   3 * POLL_SECS + 20,
                   "unowned claim recovered even though the child process exists")
        self.assertFalse(pid_file.exists(),
                         "a pre-publication child leaves no marker behind")
        self.assertEqual(list((self.fx / "inbox").glob("*.processing")), [])
        self.stop(c)  # the orphaned stub self-exits when its delay elapses

    def test_refused_publication_fails_the_job(self):
        # The failed-write leg of the r3 requirement: a child that cannot
        # publish ownership refuses to start, and the watcher files the
        # recording into failed/ with no marker left behind.
        audio = self.fx / "inbox" / "meeting.m4a"
        audio.write_bytes(b"0" * 100)
        (self.fx / "inbox" / "meeting.m4a.stubspec").write_text(
            "PUBLISH_FAIL=1\n", encoding="utf-8")
        a = self.run_watcher()
        wait_until(lambda: (self.fx / "inbox" / "failed" / "meeting.m4a").exists(),
                   STABLE_POLLS * POLL_SECS + 40,
                   "a child that cannot publish ownership fails loudly into failed/")
        log = read_log(self.fx)
        self.assertIn("FAILED meeting.m4a", log)
        self.assertNotIn("DONE meeting.m4a", log)
        self.assertFalse((self.fx / "inbox" / "meeting.m4a.processing.pid").exists(),
                         "a refusing child leaves no marker behind")
        self.stop(a)


# ------------------------------------ r5 finding (unique claim per attempt)
class TestUniqueClaimPerAttempt(WatcherTestCase):
    """Review r5 finding: recovery moved recording A aside; recording B then
    arrived with the SAME filename. The delayed child for A must make ZERO
    transcription calls (its unique claim path stays gone) and B's child
    exactly one.

    Red-on-baseline derivation: on dd98d4e the claim path is SHARED, and B's
    child is delayed past A's wake-up — so when A wakes, the shared path
    holds B's claimed bytes and B's child has not locked the marker yet.
    The r4 marker lock cannot cover that window (nobody holds it): A's
    exists-check passes on B's bytes and A calls too -> 2 calls -> red.
    Unique claim paths close it: A's argv path stays gone -> refuse -> 0."""

    def test_recovered_a_then_b_same_name_old_child_zero_new_one_call(self):
        call_log = self.fx / "calls.log"
        inbox = self.fx / "inbox"

        # Recording A: its child is delayed (simulating interpreter startup).
        (inbox / "meeting.m4a").write_bytes(b"A" * 100)
        (inbox / "meeting.m4a.stubspec").write_text("PUBLISH_DELAY=60\n",
                                                    encoding="utf-8")
        a = self.run_watcher({"WATCH_CALL_LOG": str(call_log)})
        wait_until(lambda: list(inbox.glob("*.processing")),
                   STABLE_POLLS * POLL_SECS + 30,
                   "watcher to claim recording A")
        a.kill()  # watcher crashes; delayed child A outlives it, un-started
        a.communicate(timeout=10)

        c = self.run_watcher({"WATCH_CALL_LOG": str(call_log)})
        wait_until(lambda: (inbox / "failed" / "meeting.m4a").exists(),
                   3 * POLL_SECS + 20, "recovery to move A's claim aside")

        # Recording B: same filename; its child is delayed PAST A's wake-up,
        # so at t=60 the shared path holds B and nobody holds the marker.
        (inbox / "meeting.m4a").write_bytes(b"B" * 100)
        (inbox / "meeting.m4a.stubspec").write_text("PUBLISH_DELAY=90\n",
                                                    encoding="utf-8")
        wait_until(lambda: (inbox / "processed" / "meeting.m4a").exists(),
                   2 * (STABLE_POLLS * POLL_SECS) + 120,
                   "B processed end to end (its child sleeps 90s first)")

        calls = (call_log.read_text(encoding="utf-8").splitlines()
                 if call_log.exists() else [])
        self.assertEqual(len(calls), 1,
                         f"exactly one transcription call (B's); got: {calls}")
        self.assertIn("meeting.m4a", calls[0])
        self.assertEqual(list(inbox.glob("*.processing")), [],
                         "A's unique claim path must stay gone")
        self.stop(c)


# ------------------------------- r2 finding 3 / condition 3 (durable status)

class TestWarningDurability(WatcherTestCase):
    def _drop(self, stem: str, spec: str):
        (self.fx / "inbox" / f"{stem}.m4a").write_bytes(b"0" * 100)
        (self.fx / "inbox" / f"{stem}.m4a.stubspec").write_text(spec, encoding="utf-8")

    def _wait_processed(self, stem: str):
        wait_until(lambda: (self.fx / "inbox" / "processed" / f"{stem}.m4a").exists(),
                   STABLE_POLLS * POLL_SECS + 40, f"{stem} archived to processed")

    def test_positive_count_warns_and_archives(self):
        self._drop("m1", "EMPTY=2\nSEGMENTS=5\n")
        w = self.run_watcher()
        self._wait_processed("m1")
        lines = done_lines(read_log(self.fx), "m1.m4a")
        self.assertEqual(len(lines), 1)
        self.assertIn("WARNING: 2 of 5", lines[0], "the warning must reach watch.log")
        self.assertIn("missing parts", lines[0])
        out = self.stop(w)
        self.assertIn("WARNING: 2 of 5", out, "and on the watcher's own console")

    def test_zero_count_is_a_plain_done(self):
        self._drop("m2", "EMPTY=0\n")
        w = self.run_watcher()
        self._wait_processed("m2")
        lines = done_lines(read_log(self.fx), "m2.m4a")
        self.assertEqual(len(lines), 1)
        self.assertNotIn("WARNING", lines[0], "a complete transcript needs no warning")
        self.stop(w)

    def test_missing_and_malformed_meta_diagnose_and_watcher_keeps_running(self):
        self._drop("m3", "META=malformed\n")
        self._drop("m4", "META=missing\n")
        w = self.run_watcher()
        self._wait_processed("m3")
        lines3 = done_lines(read_log(self.fx), "m3.m4a")
        self.assertEqual(len(lines3), 1)
        self.assertIn("asr_meta.json", lines3[0], "the diagnostic must name the gap")
        self.assertIn("cannot verify", lines3[0])
        self._wait_processed("m4")  # m4 processed AFTER m3's weirdness = alive
        lines4 = done_lines(read_log(self.fx), "m4.m4a")
        self.assertEqual(len(lines4), 1)
        self.assertIn("not found", lines4[0])
        self.assertIn("cannot verify", lines4[0])
        self.stop(w)


if __name__ == "__main__":
    unittest.main(verbosity=2)
