#!/usr/bin/env python3
"""Offline test suite for brother-tools — no network, no API cost.

Every defect from the 2026-11-17 handoff-readiness review (Codex r1,
CHANGES-REQUIRED) has a negative control here: the test first shows the
BASELINE behavior (the exact old expression) failing the user, then proves
the fixed code handles the same injection. Run:

    python tests\\test_offline.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "transcriber"))
sys.path.insert(0, str(ROOT / "news"))

import asr_lib  # noqa: E402
import watch  # noqa: E402
import transcribe  # noqa: E402
import fetch_news  # noqa: E402
import check_installation  # noqa: E402


def make_wav(path: Path, seconds: float, rate: int = 8000) -> float:
    """Write a real wav file; return its exact duration."""
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return frames / rate


# ---------------------------------------------------------------- HIGH-1
class TestConversionIntegrity(unittest.TestCase):
    """Review finding 1: a failed ffmpeg conversion was accepted as a
    successful, silently incomplete transcript (asr_lib.py:143)."""

    def test_neg_ffmpeg_nonzero_exit_is_loud(self):
        # Baseline: `subprocess.run(...)` with the returncode ignored —
        # a failing encode scrolled on. Inject: rc=1 + one partial segment.
        with tempfile.TemporaryDirectory() as td:
            seg = Path(td) / "seg_000.wav"
            make_wav(seg, 5)
            fake = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"boom")
            with mock.patch.object(asr_lib.subprocess, "run", return_value=fake):
                with self.assertRaises(RuntimeError) as ctx:
                    asr_lib._run_ffmpeg(["ffmpeg", "-y"], timeout=10, what="unit test")
            self.assertIn("exit 1", str(ctx.exception))

    def test_neg_partial_conversion_rejected(self):
        # Inject: ffmpeg produced 2 x 5s segments of a 60s recording.
        with tempfile.TemporaryDirectory() as td:
            segs = [Path(td) / f"seg_{i:03d}.wav" for i in range(2)]
            for s in segs:
                make_wav(s, 5)
            with self.assertRaises(RuntimeError) as ctx:
                asr_lib._verify_conversion(segs, source_duration=60.0)
            self.assertIn("incomplete conversion", str(ctx.exception))
            self.assertIn("cover", str(ctx.exception))

    def test_neg_segment_not_decodable_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "seg_000.wav"
            make_wav(good, 5)
            bad = Path(td) / "seg_001.wav"
            bad.write_bytes(b"\x00" * 1024)  # right size, but not a wav — truncated write
            with self.assertRaises(RuntimeError) as ctx:
                asr_lib._verify_conversion([good, bad], source_duration=10.0)
            self.assertIn("readable wav", str(ctx.exception))

    def test_pos_full_conversion_passes(self):
        # Edge inside the boundary: 60s source, segments cover 59.5s —
        # within the 5% + 1s cut-boundary tolerance, must NOT raise.
        with tempfile.TemporaryDirectory() as td:
            segs = [Path(td) / f"seg_{i:03d}.wav" for i in range(2)]
            make_wav(segs[0], 29.0)
            make_wav(segs[1], 30.5)  # total 59.5s of a 60.0s source
            asr_lib._verify_conversion(segs, source_duration=60.0)  # no raise

    def test_neg_zero_segments_rejected(self):
        with self.assertRaises(RuntimeError) as ctx:
            asr_lib._verify_conversion([], source_duration=30.0)
        self.assertIn("no audio segments", str(ctx.exception))

    def test_transcribe_surfaces_empty_segment_texts(self):
        # A segment that billed but returned no text used to vanish from the
        # transcript silently; now it is counted and reported.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "meet.wav"
            make_wav(src, 10)
            calls = {"n": 0}

            def fake_asr_segment(seg_path, model, key):
                calls["n"] += 1
                return "" if calls["n"] == 1 else "hello world"

            with mock.patch.object(asr_lib, "asr_segment", fake_asr_segment):
                result = asr_lib.transcribe(src, api_key="test-key")
            self.assertEqual(calls["n"], 1)
            self.assertEqual(result["segments_empty"], 1)
            self.assertEqual(result["transcript"], "")

    def test_transcribe_rejects_failing_conversion_end_to_end(self):
        # Full-path injection: ffmpeg exits 0 but writes only 1 segment for a
        # 90s recording (the "missing tail" defect class). Must raise, never
        # transcribe a fraction of the meeting as if it were whole.
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "long.wav"
            make_wav(src, 90)

            def fake_ffmpeg(cmd, capture_output, timeout):
                tdir = Path(cmd[-1]).parent
                make_wav(tdir / "seg_000.wav", 29)
                return subprocess.CompletedProcess(args=[], returncode=0,
                                                   stdout=b"", stderr=b"")

            with mock.patch.object(asr_lib, "_which_ff", return_value="C:/fake/ffmpeg.exe"), \
                 mock.patch.object(asr_lib.subprocess, "run", side_effect=fake_ffmpeg):
                with self.assertRaises(RuntimeError) as ctx:
                    asr_lib.transcribe(src, api_key="test-key")
            self.assertIn("incomplete conversion", str(ctx.exception))


# ---------------------------------------------------------------- HIGH-2 (r2)
class TestWatcherSingleInstance(unittest.TestCase):
    """Review r2 finding 2: two simultaneous starters could both take over a
    stale lock (both read it as dead, then the loser's delete removed the
    winner's FRESH lock). The lock is now an OS byte lock held for the
    process lifetime: the OS decides atomically who holds it and releases it
    by itself on exit or crash. These tests hold REAL byte locks from real
    processes; lock-file CONTENTS are never load-bearing anymore."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.old_lock = watch.LOCK_FILE
        watch.LOCK_FILE = self.dir / "watch.lock"

    def tearDown(self):
        watch.release_lock()
        watch.LOCK_FILE = self.old_lock
        self.tmp.cleanup()

    def _holder(self):
        """A real second process holding the watch.lock byte for 60 s."""
        return subprocess.Popen([sys.executable, "-c",
            "import os, sys, time\n"
            "f = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
            "if os.name == 'nt':\n"
            "    import msvcrt; msvcrt.locking(f, msvcrt.LK_NBLCK, 1)\n"
            "else:\n"
            "    import fcntl; fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "print('HELD', flush=True)\n"
            "time.sleep(60)\n", str(watch.LOCK_FILE)],
            stdout=subprocess.PIPE, text=True)

    def _try_acquire_until_refused(self) -> bool:
        """acquire_lock() must fail once the holder really holds the byte.

        The holder needs a moment to start; an early success just means the
        byte was still free — release and retry."""
        import time as _time
        deadline = _time.time() + 10
        while _time.time() < deadline:
            with mock.patch.object(watch, "log"):
                if not watch.acquire_lock():
                    return True
            watch.release_lock()
            _time.sleep(0.1)
        return False

    def test_neg_second_watcher_refused_while_byte_held(self):
        holder = self._holder()
        try:
            self.assertEqual(holder.stdout.readline().strip(), "HELD")
            self.assertTrue(self._try_acquire_until_refused(),
                            "a second watcher must refuse while the byte is held")
            self.assertTrue(watch.LOCK_FILE.exists(), "the lock must survive the refusal")
        finally:
            holder.kill()
            holder.wait()
        # crash-release: the OS frees the byte the moment the holder dies —
        # this is what replaces r1's stale-lock takeover (and its race).
        with mock.patch.object(watch, "log"):
            self.assertTrue(watch.acquire_lock(),
                            "a replacement must acquire after the holder exits")
        watch.release_lock()
        # r3 plan conformance: the lock FILE stays in place — deleting it is
        # racy on Unix (a replacement could lock the old file while a third
        # process creates and locks a fresh one). Release = close the handle;
        # the file is only a note for the refusal message.
        self.assertTrue(watch.LOCK_FILE.exists(),
                        "release must close the handle, not delete the lock file")

    def test_pos_leftover_lock_file_never_blocks(self):
        # Edge: a leftover file with a DEAD pid note (crashed watcher). Under
        # r1 this needed PID-liveness "takeover" — the code that raced. Now
        # the file is just a note; a free byte means free to start.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        watch.LOCK_FILE.write_text(
            json.dumps({"pid": dead.pid, "started": "2026-11-18T00:00:00"}), encoding="utf-8")
        with mock.patch.object(watch, "log"):
            self.assertTrue(watch.acquire_lock())
        watch.release_lock()

    def test_pos_corrupt_lock_file_never_blocks(self):
        # Birth shape: a half-written lock from a crash mid-write.
        watch.LOCK_FILE.write_text("{broken json", encoding="utf-8")
        with mock.patch.object(watch, "log"):
            self.assertTrue(watch.acquire_lock())
        watch.release_lock()

    def test_neg_sweep_documented_as_lock_guarded(self):
        # The defect mechanism, kept explicit: the claim sweep must stay
        # unreachable while a lock is held — main() wires acquire before it.
        import inspect
        src = inspect.getsource(watch.main)
        self.assertLess(src.index("acquire_lock"), src.index("sweep_claims"),
                        "sweep_claims() must only run after the lock is held")


# ---------------------------------------------------------------- MED-3
class TestResumeFromTranscript(unittest.TestCase):
    """Review finding 3: a summary failure meant re-running transcription
    and paying the ASR cost again (transcribe.py:194)."""

    def _valid_summary_data(self):
        return {"summary": "s", "project": None, "priorities": [], "events": [],
                "insights": {"outcome": "unclear", "objections": [], "recommendations": []}}

    def test_resume_skips_asr_and_writes_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out_20261117-100000"
            out.mkdir()
            (out / "transcript.txt").write_text("we met to plan the launch", encoding="utf-8")
            (out / "asr_meta.json").write_text(json.dumps({
                "title": "meeting", "audio": "meeting.m4a",
                "meeting_day": "2026-11-15", "date_note": "assumed",
                "duration_s": 120.0, "segments": 5, "segments_empty": 0,
                "cost_yuan_est": 0.12, "model": "glm-asr-2512"}), encoding="utf-8")

            def forbidden_asr(*a, **k):
                raise AssertionError("ASR ran on resume — that is the paid re-run defect")

            with mock.patch.object(transcribe.asr_lib, "transcribe", forbidden_asr), \
                 mock.patch.object(transcribe.summarize, "summarize",
                                   return_value=self._valid_summary_data()), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", "--resume", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            for name in ("summary.md", "tasks.md", "events.ics"):
                self.assertTrue((out / name).exists(), f"{name} missing after resume")
            # date came from the meta, not from today
            self.assertIn("2026-11-15", (out / "summary.md").read_text(encoding="utf-8"))

    def test_resume_date_correction_uses_given_date(self):
        # MED-7 companion: correcting the assumed date re-summarizes cheaply.
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir()
            (out / "transcript.txt").write_text("plan", encoding="utf-8")
            (out / "asr_meta.json").write_text(json.dumps({
                "title": "m", "meeting_day": "2026-11-17", "duration_s": 1.0,
                "segments": 1, "cost_yuan_est": 0.0, "model": "x"}), encoding="utf-8")
            captured = {}

            def fake_summarize(transcript, meeting_day, projects, project_key):
                captured["meeting_day"] = meeting_day
                return self._valid_summary_data()

            with mock.patch.object(transcribe.summarize, "summarize", fake_summarize), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", "--resume", str(out),
                                    "--date", "2026-11-02"]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            self.assertEqual(captured["meeting_day"], "2026-11-02")
            self.assertIn("corrected date", (out / "summary.md").read_text(encoding="utf-8"))

    def test_neg_resume_without_meta_refused(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out"
            out.mkdir()
            (out / "transcript.txt").write_text("x", encoding="utf-8")
            with mock.patch.object(sys, "argv",
                                   ["transcribe.py", "--resume", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 1)


# ---------------------------------------------------------------- MED-4
class TestCheckInstallation(unittest.TestCase):
    """Review finding 4: setup spans many steps and explains nothing when
    one is missing; the checker must state each gap in plain language."""

    def _fixture(self, complete: bool) -> Path:
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "transcriber" / "ffmpeg").mkdir(parents=True)
        (root / "news").mkdir()
        if complete:
            (root / "transcriber" / ".env").write_text(
                "ZHIPU_API_KEY=xxx\nDEEPSEEK_API_KEY=yyy\n", encoding="utf-8")
            (root / "news" / ".env").write_text("DEEPSEEK_API_KEY=yyy\n", encoding="utf-8")
            (root / "transcriber" / "projects.json").write_text(
                json.dumps([{"key": "k", "name": "n", "context": "c"}]), encoding="utf-8")
            (root / "news" / "categories.json").write_text(json.dumps(
                [{"key": "c", "name": "C", "focus": "f",
                  "feeds": [{"name": "F", "url": "https://example.org/rss"}]}]),
                encoding="utf-8")
        else:
            # the just-outside edge: files exist but are unusable
            (root / "transcriber" / ".env").write_text("ZHIPU_API_KEY=\n", encoding="utf-8")
            (root / "news" / ".env").write_text("# nothing here\n", encoding="utf-8")
            (root / "transcriber" / "projects.json").write_text("{oops", encoding="utf-8")
            (root / "news" / "categories.json").write_text("[]", encoding="utf-8")
        self.addCleanup(td.cleanup)
        return root

    def _rows(self, result, substring):
        return [r for r in result.rows if substring in r[1]]

    def test_complete_fixture_reports_no_config_gaps(self):
        c = check_installation.run_checks(self._fixture(True))
        env_rows = self._rows(c, "API keys file")
        self.assertTrue(env_rows and all(ok for _, _, ok, _ in env_rows),
                        f"complete fixture flagged: {env_rows}")
        proj = self._rows(c, "projects.json")
        cats = self._rows(c, "categories.json")
        self.assertTrue(proj and proj[0][2], "valid projects.json must pass")
        self.assertTrue(cats and cats[0][2], "valid categories.json must pass")

    def test_broken_fixture_names_each_gap(self):
        c = check_installation.run_checks(self._fixture(False))
        fails = c.failures
        joined = " | ".join(f for _, _, f in fails)
        self.assertTrue(any("transcriber/.env" in i or ".env" in i for _, i, _ in fails),
                        "empty key must be flagged")
        self.assertIn("missing or empty", joined)
        self.assertIn("not valid JSON", joined)
        self.assertTrue(any("categories.json" in i for _, i, _ in fails),
                        "empty categories list must be flagged")

    def test_missing_env_file_gets_copy_instruction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "transcriber").mkdir()
            (root / "news").mkdir()
            c = check_installation.run_checks(root)
            missing = [f for _, i, f in c.failures if "copy" in f and ".env.example" in f]
            self.assertEqual(len(missing), 2,
                             "both missing .env files must point at .env.example")


# ---------------------------------------------------------------- MED-5
class TestBackgroundDependability(unittest.TestCase):
    """Review finding 5: background runs were invisible and missed runs
    silently skipped (news/install_daily.cmd:13)."""

    def test_run_daily_self_test_writes_log(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "run_daily.py"
            script.write_text((ROOT / "news" / "run_daily.py").read_text(encoding="utf-8"),
                              encoding="utf-8")
            r = subprocess.run([sys.executable, "-I", str(script), "--self-test"],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            log = Path(td) / "output" / "news_log.txt"
            self.assertTrue(log.exists(), "the persistent log must exist after a run")
            self.assertIn("self-test OK", log.read_text(encoding="utf-8"))

    def test_log_rotation_keeps_one_generation(self):
        with tempfile.TemporaryDirectory() as td:
            import run_daily
            old_dir, old_log = run_daily.LOG_DIR, run_daily.LOG_FILE
            run_daily.LOG_DIR = Path(td)
            run_daily.LOG_FILE = Path(td) / "news_log.txt"
            try:
                run_daily.LOG_FILE.write_text("x" * (run_daily.LOG_MAX_BYTES + 10),
                                              encoding="utf-8")
                run_daily._rotate_if_big()
                self.assertFalse(run_daily.LOG_FILE.exists(), "oversized log renamed")
                self.assertTrue((Path(td) / "news_log.old.txt").exists())
            finally:
                run_daily.LOG_DIR, run_daily.LOG_FILE = old_dir, old_log

    def test_schedtasks_xml_sets_start_when_available(self):
        # The install script's promise, checked at the mechanism level: the
        # PowerShell settings object it applies must carry StartWhenAvailable.
        ps = ("New-ScheduledTaskSettingsSet -StartWhenAvailable "
              "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries")
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"({ps}).StartWhenAvailable"], capture_output=True,
                           text=True, timeout=90)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("True", r.stdout.strip())


# ---------------------------------------------------------------- MED-6
class TestNewsValidation(unittest.TestCase):
    """Review finding 6: an empty AI reply published as success, and URLs
    differing only in query params collapsed into one (fetch_news.py:176)."""

    def test_neg_baseline_dedupe_collapsed_query_only_links(self):
        # The exact old expression, shown failing the user first:
        url_a = "https://example.org/story?utm=feed"
        url_b = "https://example.org/story?id=456"
        old = lambda u: u.split("?")[0].rstrip("/")  # noqa: E731
        self.assertEqual(old(url_a), old(url_b),
                         "baseline: two distinct articles shared one identity")

    def test_pos_full_url_identity_keeps_both(self):
        url_a = "https://Example.org/story/?utm=feed"
        url_b = "https://example.org/story?id=456"
        self.assertNotEqual(fetch_news._link_identity(url_a),
                            fetch_news._link_identity(url_b))
        # normal dedupe still works: same story, cosmetically different
        self.assertEqual(fetch_news._link_identity("https://x.com/a#top"),
                         fetch_news._link_identity("https://x.com/a/"))

    def _candidates(self):
        return [{"id": 1, "title": "Story one", "link": "https://e.org/1",
                 "source": "S", "published": None, "age_h": 1, "snippet": ""}]

    def _curate_reply(self, content: str):
        resp = mock.Mock()
        resp.__enter__ = mock.Mock(return_value=resp)
        resp.__exit__ = mock.Mock(return_value=False)
        resp.read.return_value = json.dumps(
            {"choices": [{"message": {"content": content}}]}).encode("utf-8")
        return resp

    def test_neg_empty_curation_refused(self):
        # `{"categories": []}` parses fine — baseline published it as a
        # successful briefing with zero picks. Now: loud refusal.
        cats = [{"key": "tech", "name": "Tech", "focus": "f"}]
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test"}), \
             mock.patch.object(fetch_news.urllib.request, "urlopen",
                               return_value=self._curate_reply('{"categories": []}')):
            with self.assertRaises(RuntimeError) as ctx:
                fetch_news.curate(self._candidates(), cats, "2026-11-17")
        self.assertIn("no usable selections", str(ctx.exception))

    def test_pos_curation_with_valid_pick_succeeds(self):
        cats = [{"key": "tech", "name": "Tech", "focus": "f"}]
        reply = ('{"categories": [{"key": "tech", "summary": "s",'
                 ' "items": [{"id": 1, "takeaway": "t"}]}]}')
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test"}), \
             mock.patch.object(fetch_news.urllib.request, "urlopen",
                               return_value=self._curate_reply(reply)):
            out = fetch_news.curate(self._candidates(), cats, "2026-11-17")
        self.assertEqual(out["tech"]["items"][0]["title"], "Story one")

    def test_page_shows_curation_failure_note(self):
        html_out = fetch_news.build_page(
            [{"key": "tech", "name": "Tech"}], {}, "2026-11-17T00:00:00Z",
            "raw", [], curation_note="AI curation failed this run — showing raw headlines.")
        self.assertIn("AI curation failed this run", html_out)


# ---------------------------------------------------------------- MED-7
class TestMeetingDateVisibility(unittest.TestCase):
    """Review finding 7: the meeting date was assumed silently (mtime or
    today) and calendar items carried it without saying so."""

    def _valid_summary_data(self):
        return {"summary": "s", "project": None, "priorities": [], "events": [
            {"date": "2026-11-20", "subject": "Follow-up", "notes": "n"}],
            "insights": {"outcome": "unclear", "objections": [], "recommendations": []}}

    def test_summary_labels_assumed_date(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "meet.wav"
            make_wav(src, 2)
            meta_note = {}

            def fake_asr(path, **k):
                return {"transcript": "hello", "duration_s": 2.0, "segments": 1,
                        "segments_empty": 0, "cost_yuan_est": 0.01, "model": "m"}

            def fake_summarize(transcript, meeting_day, projects, project_key):
                meta_note["day"] = meeting_day
                return self._valid_summary_data()

            out = Path(td) / "o"
            with mock.patch.object(transcribe.asr_lib, "transcribe", fake_asr), \
                 mock.patch.object(transcribe.summarize, "summarize", fake_summarize), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", str(src), "--date", "2026-11-01",
                                    "--date-assumed", "--out", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("assumed from the recording file", summary)
            self.assertIn("--resume", summary, "the correction path is spelled out")
            meta = json.loads((out / "asr_meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["meeting_day"], "2026-11-01")
            self.assertIn("date_note", meta)

    def test_manual_today_default_is_labelled(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "meet.wav"
            make_wav(src, 2)

            def fake_asr(path, **k):
                return {"transcript": "hello", "duration_s": 2.0, "segments": 1,
                        "segments_empty": 0, "cost_yuan_est": 0.01, "model": "m"}

            out = Path(td) / "o"
            with mock.patch.object(transcribe.asr_lib, "transcribe", fake_asr), \
                 mock.patch.object(transcribe.summarize, "summarize",
                                   return_value=self._valid_summary_data()), \
                 mock.patch.object(sys, "argv",
                                   ["transcribe.py", str(src), "--out", str(out)]):
                rc = transcribe.main()
            self.assertEqual(rc, 0)
            summary = (out / "summary.md").read_text(encoding="utf-8")
            self.assertIn("--date YYYY-MM-DD", summary,
                          "the today-default must say how to correct it")


# ---------------------------------------------------------------- C2 (r2/r4)
def _lock_byte(fd: int) -> None:
    """Take the byte-0 lock non-blocking; raise OSError if held elsewhere."""
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_byte(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.lockf(fd, fcntl.LOCK_UN)


@contextmanager
def hold_marker_lock(path: Path):
    """Hold a file's byte-0 lock from this process (like a live child)."""
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    _lock_byte(fd)
    try:
        yield fd
    finally:
        try:
            _unlock_byte(fd)
        except OSError:
            pass
        os.close(fd)


class TestClaimSweep(unittest.TestCase):
    """Review r2 condition 2 / r4 finding: claiming and recovering the same
    recording are mutually exclusive — the marker's byte lock decides. The
    child locks the marker before touching the recording; the sweep must win
    the same lock before moving the claim. There is no check-then-move
    window left to lose a race in."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inbox = self.root / "inbox"
        self.failed = self.root / "failed"
        self.inbox.mkdir()
        self.failed.mkdir()  # production main() creates these before any sweep
        self.old_inbox, self.old_failed = watch.INBOX, watch.FAILED
        watch.INBOX, watch.FAILED = self.inbox, self.failed
        self.old_announced = set(watch._ANNOUNCED_ALIVE)
        watch._ANNOUNCED_ALIVE.clear()
        self.holders = []

    def tearDown(self):
        for h in self.holders:
            h.kill()
            h.wait()
        watch.INBOX, watch.FAILED = self.old_inbox, self.old_failed
        watch._ANNOUNCED_ALIVE.clear()
        watch._ANNOUNCED_ALIVE.update(self.old_announced)
        self.tmp.cleanup()

    def _lock_holder(self, marker: Path):
        """A real second process holding the marker's byte-0 lock for 60 s."""
        h = subprocess.Popen([sys.executable, "-c",
            "import os, sys, time\n"
            "f = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
            "if os.name == 'nt':\n"
            "    import msvcrt; msvcrt.locking(f, msvcrt.LK_NBLCK, 1)\n"
            "else:\n"
            "    import fcntl; fcntl.lockf(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "print('HELD', flush=True)\n"
            "time.sleep(60)\n", str(marker)],
            stdout=subprocess.PIPE, text=True)
        self.holders.append(h)
        return h

    def test_neg_locked_marker_claim_left_alone(self):
        # The r4 defect class, forced: the marker's PID note is DEAD (that
        # alone made the pre-r4 sweep move the claim) but a LIVE transcription
        # holds the marker lock — the lock is the decision, not the PID.
        claim = self.inbox / "meet.m4a.processing"
        claim.write_text("audio", encoding="utf-8")
        marker = watch._pid_file(claim)
        marker.write_text("999999", encoding="utf-8")
        holder = self._lock_holder(marker)
        self.assertEqual(holder.stdout.readline().strip(), "HELD")
        with mock.patch.object(watch, "log"):
            watch.sweep_claims()
        self.assertTrue(claim.exists(), "a locked marker means the claim is owned")
        self.assertTrue(marker.exists(), "the owner's marker must survive")
        self.assertEqual(list(self.failed.iterdir()), [])

    def test_pos_unlocked_marker_recovered(self):
        # Just-outside edge: marker present with a dead PID note, NO lock —
        # nobody owns the recording; the sweep wins the lock and moves it.
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        claim = self.inbox / "meet.m4a.processing"
        claim.write_text("audio", encoding="utf-8")
        watch._pid_file(claim).write_text(str(dead.pid), encoding="utf-8")
        with mock.patch.object(watch, "log"):
            watch.sweep_claims()
        self.assertFalse(claim.exists())
        self.assertTrue((self.failed / "meet.m4a").exists())
        self.assertFalse(watch._pid_file(claim).exists(), "the marker is tidied with the claim")

    def test_pos_ownerless_claim_recovered_and_tombstone_cleaned(self):
        # Birth shape: a claim with no marker at all (crash before the child
        # claimed it). The sweep tombstones the marker itself, moves the
        # claim, and leaves no marker behind.
        claim = self.inbox / "meet.m4a.processing"
        claim.write_text("audio", encoding="utf-8")
        with mock.patch.object(watch, "log"):
            watch.sweep_claims()
        self.assertTrue((self.failed / "meet.m4a").exists())
        self.assertFalse(watch._pid_file(claim).exists(), "the tombstone is tidied")

    def test_pos_stray_marker_tidied(self):
        # r3: a marker whose claim is gone (the child lost the publication
        # race) has nobody left to protect — the sweep tidies it.
        marker = self.inbox / "gone.m4a.processing.pid"
        marker.write_text("12345", encoding="utf-8")
        with mock.patch.object(watch, "log"):
            watch.sweep_claims()
        self.assertFalse(marker.exists())
        self.assertEqual(list(self.failed.iterdir()), [])


# ---------------------------------------------------------------- r5
class TestClaimNaming(unittest.TestCase):
    """Review r5 finding: the claim path used to be reused across attempts,
    so a delayed child from a crashed run could be re-attached to a NEWER
    recording with the same filename (duplicate paid calls, wrong contents).
    Each attempt now gets a unique claim path; display keeps the original."""

    def test_claim_paths_unique_per_attempt(self):
        import time as _time
        p1 = watch._claim_path(Path("meeting.m4a"))
        _time.sleep(0.0001)  # attempts are poll-cycles apart; force a tag change
        p2 = watch._claim_path(Path("meeting.m4a"))
        self.assertNotEqual(p1.name, p2.name, "two attempts must not share a claim")
        for p in (p1, p2):
            self.assertTrue(p.name.endswith(".processing"))
            self.assertEqual(watch._original_name(p.name), "meeting.m4a",
                             "display must recover the original filename")

    def test_original_name_parses_legacy_and_plain_shapes(self):
        # Legacy claims from before r5 carried no tag; the parse must not eat
        # real filename segments.
        self.assertEqual(watch._original_name("meeting.m4a.processing"), "meeting.m4a")
        self.assertEqual(
            watch._original_name("meeting.m4a.20261118-170830-086027.processing"),
            "meeting.m4a")
        self.assertEqual(watch._original_name("meet.2026.11.18.m4a.processing"),
                         "meet.2026.11.18.m4a", "dots in the real name must survive")


# ---------------------------------------------------------------- r5
class TestDelayedChildCantPickUpReplacement(unittest.TestCase):
    """Review r5 finding, replayed as the forced overlap: a delayed child
    starts, the shared filename fills with a NEW recording's bytes, the
    child resumes. With unique claim paths the child's argv can never point
    at another attempt's audio — its result stays its own recording. (This
    test is RED against dd98d4e, where the claim path was reused.)"""

    def test_delayed_child_transcribes_its_own_bytes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        inbox, processed, failed = root / "inbox", root / "processed", root / "failed"
        inbox.mkdir()
        processed.mkdir()
        failed.mkdir()  # production main() creates these before any processing
        old = {"HERE": watch.HERE, "INBOX": watch.INBOX,
               "PROCESSED": watch.PROCESSED, "FAILED": watch.FAILED}
        watch.HERE, watch.INBOX = root, inbox
        watch.PROCESSED, watch.FAILED = processed, failed
        seen = {}

        class _FakeProc:
            pid = 424242
            returncode = 0

            def __init__(self, cmd):
                self.cmd = cmd

            def communicate(self, timeout=None):
                claimed = Path(self.cmd[2])
                seen["argv"] = str(claimed)
                seen["bytes_at_child_start"] = claimed.read_bytes()
                # the "release": a new recording fills the SHARED legacy name
                # (inbox/meeting.m4a.processing) while the child is mid-flight
                (inbox / "meeting.m4a.processing").write_bytes(b"B-CONTAMINATED!")
                return ("", "")

            def kill(self):
                pass

        try:
            source = inbox / "meeting.m4a"
            source.write_bytes(b"A-ORIGINAL")
            with mock.patch.object(watch, "log"), \
                 mock.patch.object(watch.subprocess, "Popen",
                                   side_effect=lambda cmd, **k: _FakeProc(cmd)):
                rc = watch.process_one(source)
            self.assertTrue(rc)
            result = (processed / "meeting.m4a").read_bytes()
            self.assertEqual(result, b"A-ORIGINAL",
                             "the delayed child must transcribe ITS recording, "
                             "not whichever bytes later filled a shared path")
            self.assertNotEqual(seen["argv"], str(inbox / "meeting.m4a.processing"),
                                "the claim path must be unique per attempt")
            self.assertEqual(seen["bytes_at_child_start"], b"A-ORIGINAL",
                             "the child started on its own recording's bytes")
        finally:
            watch.HERE, watch.INBOX = old["HERE"], old["INBOX"]
            watch.PROCESSED, watch.FAILED = old["PROCESSED"], old["FAILED"]


# ------------------------------------------------- r3/r4: ownership first
class TestTranscribeClaimBeacon(unittest.TestCase):
    """Review r3 finding 1 + r4 finding: the child must take the marker's
    byte lock BEFORE it can use the recording, and must refuse to start if
    the marker cannot be opened, locked, or written. The lock is what makes
    claiming mutually exclusive with recovery. (In-process: real files.)"""

    def _valid_summary_data(self):
        return {"summary": "s", "project": None, "priorities": [], "events": [],
                "insights": {"outcome": "unclear", "objections": [], "recommendations": []}}

    def _fake_asr(self, seen: dict):
        def fake(path, **k):
            marker = Path(str(path) + ".pid")
            seen["marker_at_call"] = marker.exists()
            seen["locked_at_call"] = None
            if marker.exists():
                try:
                    fd = os.open(marker, os.O_RDWR)
                except OSError:
                    seen["locked_at_call"] = None
                else:
                    try:
                        try:
                            _lock_byte(fd)
                            seen["locked_at_call"] = False  # we got it: child did NOT
                            _unlock_byte(fd)
                        except OSError:
                            seen["locked_at_call"] = True   # child holds it
                    finally:
                        os.close(fd)
            return {"transcript": "hello", "duration_s": 2.0, "segments": 1,
                    "segments_empty": 0, "cost_yuan_est": 0.01, "model": "m"}
        return fake

    def _run(self, argv_extra):
        seen = {}
        out = self.inbox / "out"
        with mock.patch.object(sys, "argv",
                               ["transcribe.py", *argv_extra, "--out", str(out)]), \
             mock.patch.object(transcribe.asr_lib, "transcribe",
                               side_effect=self._fake_asr(seen)), \
             mock.patch.object(transcribe.summarize, "summarize",
                               return_value=self._valid_summary_data()):
            rc = transcribe.main()
        return rc, seen

    def setUp(self):
        # ignore_cleanup_errors: the in-process child holds its marker lock
        # until the test PROCESS exits (by design — the OS releases it), so
        # Windows cannot delete the locked marker at cleanup time.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.inbox = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_marker_locked_before_first_use(self):
        # Order 1 (transcription claims first): by the time asr can read the
        # recording, the child already holds the marker lock — recovery at
        # that instant must lose the lock and leave the claim alone.
        claim = self.inbox / "meet.m4a.processing"
        claim.write_text("audio", encoding="utf-8")
        rc, seen = self._run([str(claim), "--date", "2026-11-18"])
        self.assertEqual(rc, 0)
        self.assertTrue(seen["marker_at_call"])
        self.assertTrue(seen["locked_at_call"],
                        "the marker must be LOCKED before asr reads the recording")

    def test_refuses_to_run_when_marker_locked_by_other(self):
        # Order 2 (recovery wins): the sweep holds the marker lock — the
        # child must exit BEFORE transcription, never reading the recording.
        claim = self.inbox / "meet.m4a.processing"
        claim.write_text("audio", encoding="utf-8")
        with hold_marker_lock(self.inbox / "meet.m4a.processing.pid"):
            rc, seen = self._run([str(claim), "--date", "2026-11-18"])
        self.assertEqual(rc, 1, "a recovering claim must not be transcribed")
        self.assertIsNone(seen.get("marker_at_call"), "the recording must never be read")

    def test_recovery_wins_child_exits_before_transcription(self):
        # The full order-2 flow: recovery moves the unowned claim first; a
        # child started afterwards finds no recording and exits — with the
        # marker it created tidied by the next sweep.
        claim = self.inbox / "meet.m4a.processing"
        claim.write_text("audio", encoding="utf-8")
        failed = self.inbox / "failed"
        failed.mkdir()  # production main() creates these before any sweep
        with mock.patch.object(watch, "log"), \
             mock.patch.object(watch, "INBOX", self.inbox), \
             mock.patch.object(watch, "FAILED", failed):
            watch.sweep_claims()
        self.assertTrue((failed / "meet.m4a").exists(),
                        "recovery moved the unowned claim")
        rc, seen = self._run([str(claim), "--date", "2026-11-18"])
        self.assertEqual(rc, 1, "no recording = nothing to transcribe")
        self.assertIsNone(seen.get("marker_at_call"), "the recording must never be read")
        os.close(transcribe._CLAIM_MARKER_FD)  # simulate the child process exiting
        with mock.patch.object(watch, "log"), \
             mock.patch.object(watch, "INBOX", self.inbox), \
             mock.patch.object(watch, "FAILED", failed):
            watch.sweep_claims()
        self.assertEqual(list(self.inbox.glob("*.pid")), [],
                         "the child's leftover marker is tidied by the next sweep")

    def test_refuses_to_run_when_marker_unwritable(self):
        claim = self.inbox / "meet.m4a.processing"
        claim.write_text("audio", encoding="utf-8")
        (self.inbox / "meet.m4a.processing.pid").mkdir()  # marker path = a directory
        rc, seen = self._run([str(claim), "--date", "2026-11-18"])
        self.assertEqual(rc, 1, "an unrecognizable transcription must refuse to start")
        self.assertIsNone(seen.get("marker_at_call"), "the recording must never be read")

    def test_no_marker_for_standalone_file(self):
        audio = self.inbox / "plain.wav"
        audio.write_text("audio", encoding="utf-8")
        rc, seen = self._run([str(audio), "--date", "2026-11-18"])
        self.assertEqual(rc, 0)
        self.assertFalse(seen["marker_at_call"])
        self.assertEqual(list(self.inbox.glob("*.pid")), [],
                         "standalone runs never write markers")


# ---------------------------------------------------------------- C3 (r2)
class TestCompletenessNote(unittest.TestCase):
    """Review r2 finding 3 / condition 3: the empty-segment warning must be
    durable — read structurally from asr_meta.json, never swallowed with the
    child's captured output, and unreadable metadata must say so."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _meta(self, **kw):
        (self.out / "asr_meta.json").write_text(json.dumps(kw), encoding="utf-8")

    def _meta_raw(self, text: str):
        (self.out / "asr_meta.json").write_text(text, encoding="utf-8")

    def test_warning_on_empty_segments(self):
        self._meta(segments=5, segments_empty=2)
        note = watch._completeness_note(self.out)
        self.assertIn("WARNING", note)
        self.assertIn("2 of 5", note)

    def test_plain_done_when_zero_empty(self):
        self._meta(segments=5, segments_empty=0)
        self.assertEqual(watch._completeness_note(self.out), "")

    def test_missing_meta_names_itself(self):
        self.assertIn("not found", watch._completeness_note(self.out))
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_malformed_meta_names_itself(self):
        self._meta_raw("{not json")
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_non_dict_meta_names_itself(self):
        self._meta_raw("[1, 2]")
        self.assertIn("unexpected shape", watch._completeness_note(self.out))

    # r3 finding 2: only true integers with 0 <= empty <= total, total > 0
    # are trusted. Everything else must be a diagnostic — never a silent
    # plain DONE, never an exception that kills the watch loop.
    def test_empty_dict_is_not_silently_complete(self):
        # r3 probe: {} yielded a plain DONE — missing counts cannot be trusted.
        self._meta_raw("{}")
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_fractional_count_rejected(self):
        self._meta(segments=5, segments_empty=1.5)
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_infinity_does_not_raise(self):
        # r3 probe: 1e999 parsed to float infinity and int(inf) raised
        # OverflowError AFTER the audio was already archived — killing the
        # watch loop. Must be a diagnostic instead.
        self._meta_raw('{"segments": 5, "segments_empty": 1e999}')
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_bool_count_rejected(self):
        # bool is an int subclass; true must not silently count as 1.
        self._meta(segments=5, segments_empty=True)
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_string_count_rejected(self):
        self._meta(segments=5, segments_empty="2")
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_negative_empty_rejected(self):
        self._meta(segments=5, segments_empty=-1)
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_empty_greater_than_total_rejected(self):
        self._meta(segments=2, segments_empty=3)
        self.assertIn("cannot verify", watch._completeness_note(self.out))

    def test_zero_total_rejected(self):
        self._meta(segments=0, segments_empty=0)
        self.assertIn("cannot verify", watch._completeness_note(self.out))


# ---------------------------------------------------------------- r2 finding 1
class TestCmdFilesNoLiveParensInBlocks(unittest.TestCase):
    """Review r2 finding 1 (class-level guard): inside a parenthesized cmd
    block, an unescaped ( or ) in an echo line closes the block early —
    setup.cmd:47/48/53 aborted first-time setup with 'was unexpected'.
    Escaped ^(^) is required inside blocks; top-level echoes may keep parens."""

    @staticmethod
    def _live(text: str) -> str:
        """Strip cmd escapes: ^x is literal x, never structural."""
        out, i = [], 0
        while i < len(text):
            if text[i] == "^" and i + 1 < len(text):
                i += 2
                continue
            out.append(text[i])
            i += 1
        return "".join(out)

    @classmethod
    def _violations(cls, text: str):
        bad, depth = [], 0
        for no, line in enumerate(text.splitlines(), 1):
            live = cls._live(line)
            if depth > 0 and re.match(r"\s*echo\b", line, re.I):
                if "(" in live or ")" in live:
                    bad.append((no, line.strip()))
                    continue  # file is already broken; depth no longer trusted
            depth += live.count("(") - live.count(")")
            depth = max(depth, 0)
        return bad

    def _cmd_files(self):
        return sorted(set(ROOT.rglob("*.cmd")))

    def test_all_cmd_files_have_no_live_parens_inside_blocks(self):
        problems = []
        for f in self._cmd_files():
            for no, line in self._violations(f.read_text(encoding="utf-8")):
                problems.append(f"{f.name}:{no}: {line}")
        self.assertEqual(problems, [], "unescaped parens in echo inside cmd blocks")

    def test_neg_checker_catches_synthetic_violation(self):
        # Negative control for the checker itself: the exact broken shape.
        bad = "@echo off\nif not exist x (\n    echo key (optional; note).\n)\necho after\n"
        found = self._violations(bad)
        self.assertEqual(len(found), 1, "the checker must flag the broken shape")
        self.assertIn("(optional", found[0][1])

    def test_checker_accepts_escaped_shape(self):
        good = "@echo off\nif not exist x (\n    echo key ^(optional; note^).\n)\necho after\n"
        self.assertEqual(self._violations(good), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
